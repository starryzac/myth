"""Trusted local RR/read-only original collector; never execute financial choices.

Full physical rows and existing TEXT are retained. Any typed head/checkpoint or
current-subject text is a separately labelled projection of this SQL snapshot.
The collector does not authenticate an earlier history or compute an oracle.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any, cast
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))

from app.db import models  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.settings import DatabaseSettings  # noqa: E402
from app.domain import audit_chain as audit_data  # noqa: E402
from app.domain import audit_chain_types as audit_types  # noqa: E402
from app.domain import bank_posting_codec as posting_data  # noqa: E402
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "mvp-readonly-collector-registration-v1"
MAX_BYTES = 512 * 1024 * 1024
BINDINGS = {
    "experiment_run_id",
    "case_id",
    "arm_id",
    "execution_mode",
    "input_sha256",
    "oracle_sha256",
    "design_sha256",
    "rule_sha256",
    "source_sha256",
    "seed_version",
    "isolated_db_epoch",
    "purpose",
    "user_id",
}
FINANCIAL_TABLES = (
    "accounts",
    "evidence_items",
    "policies",
    "policy_versions",
    "goals",
    "transactions",
    "credit_card_bills",
    "asset_positions",
    "asset_products",
    "bank_operations",
    "action_plans",
    "action_receipts",
    "simulated_bank_postings",
    "action_resource_reservations",
)
SUBJECT_TABLES = {
    "USER": "users",
    "ACCOUNT": "accounts",
    "EVIDENCE": "evidence_items",
    "TRANSACTION": "transactions",
    "CREDIT_CARD_BILL": "credit_card_bills",
    "ASSET_PRODUCT": "asset_products",
    "POLICY": "policies",
    "POLICY_VERSION": "policy_versions",
    "POLICY_PROPOSAL": "policy_proposals",
    "GOAL": "goals",
    "ASSET_POSITION": "asset_positions",
    "DECISION_RUN": "decision_runs",
    "DECISION_CONSTRAINT": "decision_constraints",
    "ACTION_PLAN": "action_plans",
    "ACTION_RECEIPT": "action_receipts",
    "BANK_REDEMPTION": "simulated_bank_redemptions",
    "BANK_POSTING": "simulated_bank_postings",
    "BANK_OPERATION": "bank_operations",
    "BANK_EXTERNAL_FACT": "external_bank_facts",
    "RESOURCE_CLAIM": "action_resource_reservations",
}
TABLE_SQL = """SELECT n.nspname AS schema_name, c.relname AS table_name,
 c.relkind::text AS relkind, c.relrowsecurity AS row_security,
 c.relforcerowsecurity AS force_row_security
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
 AND c.relkind IN ('r','p','f') ORDER BY n.nspname,c.relname"""
COLUMN_SQL = """SELECT n.nspname AS schema_name,c.relname AS table_name,
 a.attname AS column_name,a.attnum AS ordinal,t.typname AS type_name,
 a.attnotnull AS not_null,pg_catalog.format_type(a.atttypid,a.atttypmod) AS formatted_type
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
 JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
 WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
 AND c.relkind IN ('r','p','f') AND a.attnum>0 AND NOT a.attisdropped
 ORDER BY n.nspname,c.relname,a.attnum"""
IDENTITY_SQL = """SELECT current_database() AS database_name,pg_backend_pid() AS backend_pid,
 txid_current()::text AS transaction_id,current_setting('transaction_isolation') AS isolation,
 current_setting('transaction_read_only') AS read_only,current_user AS database_role,
 clock_timestamp() AS observed_at,txid_current_snapshot()::text AS snapshot_id"""


class CollectorRefused(ValueError):
    """No captured success can be established from this original context."""


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise CollectorRefused(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: Any) -> str:
    check(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "INVALID_SHA256")
    return cast(str, value)


def strict(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            check(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(value: str) -> Any:
        raise CollectorRefused("NONFINITE_JSON:" + value)

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
    check(type(result) is dict, "ORIGINAL_NOT_OBJECT")
    return cast(dict[str, Any], result)


def json_value(value: Any, *, money: bool = False) -> Any:
    if money and value is not None:
        check(type(value) is int, "MONEY_NOT_ORIGINAL_INTEGER")
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        check(math.isfinite(value), "NONFINITE_ORIGINAL_NUMBER")
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        check(value.tzinfo is not None and value.utcoffset() is not None, "NAIVE_ORIGINAL_TIME")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if type(value) is list:
        return [json_value(item) for item in value]
    if type(value) is dict:
        check(all(type(key) is str for key in value), "NONSTRING_ORIGINAL_JSON_KEY")
        # Preserve invalid JSON evidence too. Monetary DB columns are checked at
        # the physical-row boundary; an oracle must assess JSON content itself.
        return {key: json_value(item) for key, item in value.items()}
    raise CollectorRefused("UNSUPPORTED_ORIGINAL_DB_TYPE:" + type(value).__name__)


def canonical(value: Any) -> bytes:
    return json.dumps(
        json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def aware(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    check(result.tzinfo is not None and result.utcoffset() is not None, "NAIVE_REGISTERED_TIME")
    return result.astimezone(UTC)


def original(ref: dict[str, str], workspace: Path) -> bytes:
    check(set(ref) == {"path", "sha256"}, "INVALID_ORIGINAL_DESCRIPTOR")
    path = Path(ref["path"])
    check(
        path.is_absolute()
        and not path.is_symlink()
        and path.resolve().is_relative_to(workspace.resolve()),
        "ORIGINAL_OUTSIDE_WORKSPACE",
    )
    check(path.is_file() and path.stat().st_size <= MAX_BYTES, "ORIGINAL_MISSING_OR_OVER_CAPACITY")
    raw = path.read_bytes()
    check(sha(raw) == digest(ref["sha256"]), "ORIGINAL_BYTE_SHA_MISMATCH")
    return raw


def function_codes(raw: bytes, filename: str) -> dict[str, CodeType]:
    module = compile(raw, filename, "exec", dont_inherit=True)
    return {code.co_name: code for code in module.co_consts if isinstance(code, CodeType)}


def loaded_guard(module: Any, raw: bytes, *, names: set[str] | None = None) -> None:
    expected = function_codes(raw, str(Path(module.__file__).resolve()))
    selected = (
        names
        if names is not None
        else {
            name
            for name, fn in vars(module).items()
            if isinstance(fn, FunctionType) and fn.__module__ == module.__name__
        }
    )
    for name in selected:
        fn = getattr(module, name, None)
        check(
            isinstance(fn, FunctionType)
            and fn.__code__ == expected.get(name)
            and fn.__globals__ is vars(module),
            "LOADED_FUNCTION_SOURCE_MISMATCH:" + name,
        )
    if names is None:
        class_names = {node.name for node in ast.parse(raw).body if isinstance(node, ast.ClassDef)}
        for name in class_names:
            klass = getattr(module, name, None)
            check(
                isinstance(klass, type) and klass.__module__ == module.__name__,
                "LOADED_CLASS_BINDING_MISMATCH:" + name,
            )
            code = expected.get(name)
            check(code is not None, "LOADED_CLASS_SOURCE_MISSING:" + name)
            method_codes = {
                item.co_name: item
                for item in cast(CodeType, code).co_consts
                if isinstance(item, CodeType)
            }
            for method_name, method in vars(klass).items():
                fn = method.__func__ if isinstance(method, (classmethod, staticmethod)) else method
                if not isinstance(fn, FunctionType):
                    continue
                if method_name not in method_codes and fn.__module__ != module.__name__:
                    continue  # Generated framework constructors are not module source methods.
                check(
                    fn.__module__ == module.__name__
                    and fn.__code__ == method_codes.get(method_name)
                    and fn.__globals__ is vars(module),
                    "LOADED_METHOD_SOURCE_MISMATCH:" + name + "." + method_name,
                )


def required_sources(workspace: Path) -> set[str]:
    result = {
        p.relative_to(workspace).as_posix()
        for folder in (workspace / "apps/api/app", workspace / "apps/api/alembic")
        for p in folder.rglob("*.py")
        if "tests" not in p.relative_to(folder).parts and "__pycache__" not in p.parts
    }
    return result | {
        "scripts/mvp_readonly_collector.py",
        "pyproject.toml",
        "uv.lock",
        "alembic.ini",
    }


class Registration:
    def __init__(self, ref: dict[str, str], workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.ref = dict(ref)
        self.value = strict(original(ref, self.workspace))
        check(
            set(self.value)
            == {
                "protocol",
                "bindings",
                "database_name",
                "as_of",
                "role",
                "source_ref",
                "root_registration_ref",
            },
            "REGISTRATION_FIELDS_DIFFER",
        )
        check(self.value["protocol"] == PROTOCOL, "UNKNOWN_COLLECTOR_REGISTRATION")
        self.bindings = self.value["bindings"]
        check(
            type(self.bindings) is dict and set(self.bindings) == BINDINGS,
            "INCOMPLETE_THIRTEEN_BINDINGS",
        )
        check(
            all(type(item) is str and item for item in self.bindings.values()),
            "INVALID_BINDING_VALUE",
        )
        for name in ("experiment_run_id", "user_id", "isolated_db_epoch"):
            check(
                str(UUID(self.bindings[name])) == self.bindings[name], "NONCANONICAL_BINDING_UUID"
            )
        for name in ("input", "oracle", "design", "rule", "source"):
            digest(self.bindings[name + "_sha256"])
        check(
            self.bindings["purpose"] in {"DEVELOPMENT", "MVP_FROZEN", "FULL_FAMILY_FROZEN"}
            and self.bindings["arm_id"] in {"B0", "B1", "B2", "B3", "P"}
            and self.bindings["execution_mode"] == "SERVICE_INTEGRATION"
            and self.bindings["seed_version"] == "mvp-301-v6",
            "UNSUPPORTED_ORIGINAL_CONTEXT",
        )
        self.database = self.value["database_name"]
        check(
            type(self.database) is str
            and re.fullmatch(r"bf_test_[0-9a-f]{32}", self.database) is not None,
            "DATABASE_NOT_GENERATED_TEST",
        )
        self.as_of = aware(self.value["as_of"])
        check(
            self.value["role"] in {"BEFORE", "AFTER", "FINAL", "SNAPSHOT"}, "INVALID_SNAPSHOT_ROLE"
        )
        self.source_ref = self.value["source_ref"]
        check(
            type(self.source_ref) is dict
            and self.source_ref.get("sha256") == self.bindings["source_sha256"],
            "ORIGINAL_SOURCE_REBOUND",
        )
        source = strict(original(self.source_ref, self.workspace))
        check(source.get("purpose") == self.bindings["purpose"], "ORIGINAL_SOURCE_PURPOSE_REBOUND")
        self.sources: dict[str, str] = {}
        for row in source["files"]:
            check(type(row) is dict and set(row) == {"path", "sha256"}, "SOURCE_FILE_FIELDS_DIFFER")
            name = row["path"]
            path = Path(name)
            check(
                type(name) is str
                and not path.is_absolute()
                and ".." not in path.parts
                and name not in self.sources,
                "INVALID_OR_ALIASED_SOURCE_PATH",
            )
            self.sources[name] = digest(row["sha256"])
        required = required_sources(self.workspace)
        check(required <= self.sources.keys(), "COMPLETE_CURRENT_SOURCE_SCOPE_MISSING")
        self.parsed_digest = sha(canonical(self.value))
        self.source_digest = sha(canonical(self.sources))
        self.checkpoint()

    def checkpoint(self) -> None:
        check(
            sha(canonical(self.value)) == self.parsed_digest
            and sha(canonical(self.sources)) == self.source_digest,
            "PARSED_REGISTRATION_MUTATED",
        )
        original(self.ref, self.workspace)
        original(self.source_ref, self.workspace)
        check(
            required_sources(self.workspace) <= self.sources.keys(),
            "NEW_CURRENT_SOURCE_UNREGISTERED",
        )
        root_ref = self.value["root_registration_ref"]
        if root_ref is not None:
            root = strict(original(root_ref, self.workspace))
            check(
                root.get("bindings") == self.bindings
                and root.get("database_name") == self.database,
                "ROOT_REGISTRATION_CONTEXT_REBOUND",
            )
        for name, expected in self.sources.items():
            path = (self.workspace / name).resolve()
            check(path.is_relative_to(self.workspace) and path.is_file(), "CURRENT_SOURCE_MISSING")
            check(sha(path.read_bytes()) == expected, "CURRENT_SOURCE_DRIFT:" + name)
        modules = (sys.modules[__name__], audit_data, audit_types, posting_data)
        for module in modules:
            filename = module.__file__
            check(type(filename) is str, "LOADED_MODULE_SOURCE_MISSING")
            path = Path(cast(str, filename)).resolve()
            name = path.relative_to(self.workspace).as_posix()
            check(name in self.sources, "LOADED_MODULE_SOURCE_UNREGISTERED")
            loaded_guard(module, path.read_bytes())


def schema_contract() -> dict[str, dict[str, Any]]:
    # Importing models populates Base metadata; no engine or SQL is created.
    check(
        len(Base.metadata.tables) == 23 and models.User.__tablename__ == "users",
        "BASE_SCHEMA_DRIFT",
    )
    types = {
        "INTEGER": "int4",
        "BIGINT": "int8",
        "UUID": "uuid",
        "TEXT": "text",
        "BOOLEAN": "bool",
        "DATE": "date",
        "JSONB": "jsonb",
        "DATETIME": "timestamptz",
    }
    formats = {
        "int4": "integer",
        "int8": "bigint",
        "uuid": "uuid",
        "text": "text",
        "bool": "boolean",
        "date": "date",
        "jsonb": "jsonb",
        "timestamptz": "timestamp with time zone",
    }
    result: dict[str, dict[str, Any]] = {}
    for name, table in Base.metadata.tables.items():
        columns = {}
        for column in table.columns:
            rendered = str(column.type).upper()
            pg_type = (
                "uuid"
                if rendered == "CHAR(32)"
                else ("varchar" if rendered.startswith("VARCHAR") else types.get(rendered))
            )
            check(pg_type is not None, "UNSUPPORTED_BASE_COLUMN_TYPE:" + name + "." + column.name)
            columns[column.name] = {
                "type_name": pg_type,
                "not_null": not column.nullable,
                "formatted_type": (
                    "character varying(" + str(cast(Any, column.type).length) + ")"
                    if pg_type == "varchar"
                    else formats[cast(str, pg_type)]
                ),
            }
        result[name] = columns
    result["alembic_version"] = {
        "version_num": {
            "type_name": "varchar",
            "not_null": True,
            "formatted_type": "character varying(32)",
        }
    }
    return result


def validate_physical(
    tables: list[dict[str, Any]], columns: list[dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> None:
    check(len(tables) == 24 and len(expected) == 24, "PHYSICAL_TABLE_COUNT_DIFFERS")
    names = []
    for row in tables:
        check(
            row["schema_name"] == "public"
            and row["relkind"] == "r"
            and row["row_security"] is False
            and row["force_row_security"] is False,
            "UNSUPPORTED_SCHEMA_PARTITION_FOREIGN_TABLE_OR_RLS",
        )
        names.append(row["table_name"])
    check(len(set(names)) == 24 and set(names) == set(expected), "PHYSICAL_TABLE_INVENTORY_DIFFERS")
    actual: dict[str, dict[str, Any]] = {name: {} for name in names}
    for row in columns:
        name, key = row["table_name"], row["column_name"]
        check(
            row["schema_name"] == "public" and name in actual and key not in actual[name],
            "PHYSICAL_COLUMN_INVENTORY_DIFFERS",
        )
        actual[name][key] = {
            "type_name": row["type_name"],
            "not_null": row["not_null"],
            "formatted_type": row["formatted_type"],
        }
    check(actual == expected, "PHYSICAL_COLUMN_TYPE_NULLABILITY_OR_COMPLETENESS_DIFFERS")


def select_sql(table: str, columns: dict[str, Any]) -> tuple[str, str]:
    check(
        re.fullmatch(r"[a-z_]+", table) is not None
        and all(re.fullmatch(r"[a-z_]+", key) is not None for key in columns),
        "INVALID_SQL_IDENTIFIER",
    )
    fields = [f'"{key}"' for key in columns]
    fields += [
        f'CAST("{key}" AS text) AS "__jsonb_text_{key}"'
        for key, value in columns.items()
        if value["type_name"] == "jsonb"
    ]
    if table == "asset_products":
        where, scope, order = "", "GLOBAL_CATALOG", "id"
    elif table == "alembic_version":
        where, scope, order = "", "SCHEMA_METADATA", "version_num"
    elif table == "users":
        where, scope, order = " WHERE id = %(owner)s", "TENANT_USER", "id"
    else:
        check("user_id" in columns, "BUSINESS_TABLE_WITHOUT_ORIGINAL_OWNER")
        where, scope, order = " WHERE user_id = %(owner)s", "TENANT", "id"
    return f'SELECT {",".join(fields)} FROM "public"."{table}"{where} ORDER BY "{order}"', scope


def query(
    connection: Connection, sql: str, parameters: dict[str, Any], log: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    log.append({"sql": sql, "parameters": json_value(parameters)})
    # An empty mapping still selects psycopg's placeholder parsing. Raw catalog
    # literals such as LIKE 'pg_%' must use the no-parameters driver path.
    result = (
        connection.exec_driver_sql(sql, parameters)
        if parameters
        else connection.exec_driver_sql(sql, execution_options={"no_parameters": True})
    )
    return [dict(row) for row in result.mappings().all()] if result.returns_rows else []


def read_snapshot(connection: Connection, registration: Registration) -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    query(connection, "SET TRANSACTION READ ONLY", {}, log)
    identities = query(connection, IDENTITY_SQL, {}, log)
    check(len(identities) == 1, "SQL_IDENTITY_MISSING")
    sql_identity = identities[0]
    check(
        sql_identity["database_name"] == registration.database
        and sql_identity["isolation"] == "repeatable read"
        and sql_identity["read_only"] == "on",
        "ACTUAL_DATABASE_OR_RR_READ_ONLY_DIFFERS",
    )
    schema = schema_contract()
    physical = query(connection, TABLE_SQL, {}, log)
    physical_columns = query(connection, COLUMN_SQL, {}, log)
    validate_physical(physical, physical_columns, schema)
    tables: dict[str, list[dict[str, Any]]] = {}
    inventories, storage = {}, {}
    owner = registration.bindings["user_id"]
    for name in sorted(schema):
        sql, scope = select_sql(name, schema[name])
        parameters = {"owner": UUID(owner)} if scope.startswith("TENANT") else {}
        original_rows = query(connection, sql, parameters, log)
        rows, texts = [], {}
        for row in original_rows:
            extras = {
                "__jsonb_text_" + key
                for key, field in schema[name].items()
                if field["type_name"] == "jsonb"
            }
            check(set(row) == set(schema[name]) | extras, "RETURNED_ORIGINAL_COLUMNS_DIFFER")
            values = {
                key: json_value(row[key], money=key.endswith("_cents")) for key in schema[name]
            }
            if scope == "TENANT":
                check(values["user_id"] == owner, "RETURNED_FOREIGN_OWNER")
            elif scope == "TENANT_USER":
                check(values["id"] == owner, "RETURNED_FOREIGN_USER")
            key = values["version_num"] if scope == "SCHEMA_METADATA" else values["id"]
            check(type(key) is str and key not in texts, "DUPLICATE_ORIGINAL_ROW_ID")
            if scope != "SCHEMA_METADATA":
                check(str(UUID(key)) == key, "NONCANONICAL_ORIGINAL_ROW_ID")
            texts[key] = {field.removeprefix("__jsonb_text_"): row[field] for field in extras}
            check(
                all(text is None or type(text) is str for text in texts[key].values()),
                "JSONB_TEXT_NOT_ORIGINAL_TEXT",
            )
            rows.append(values)
        key_name = "version_num" if scope == "SCHEMA_METADATA" else "id"
        ids = [row[key_name] for row in rows]
        check(ids == sorted(ids) and len(ids) == len(set(ids)), "ORIGINAL_ROW_ORDER_OR_IDS_DIFFER")
        count_sql = sql[sql.index(' FROM "public"') : sql.index(" ORDER BY")]
        counts = query(connection, "SELECT count(*) AS row_count" + count_sql, parameters, log)
        check(
            len(counts) == 1
            and type(counts[0]["row_count"]) is int
            and counts[0]["row_count"] == len(rows),
            "ORIGINAL_FULL_ROW_COUNT_DIFFERS",
        )
        tables[name], storage[name] = rows, texts
        inventories[name] = {
            "scope": scope,
            "columns": list(schema[name]),
            "row_ids": ids,
            "sha256": sha(canonical(rows)),
            "row_count": len(rows),
        }
    users = tables["users"]
    check(len(users) == 1 and users[0]["is_simulated"] is True, "ACTUAL_SIMULATED_USER_MISSING")
    epochs = [row for row in tables["audit_epochs"] if row["status"] == "OPEN"]
    check(
        len(epochs) == 1 and epochs[0]["id"] == registration.bindings["isolated_db_epoch"],
        "ACTUAL_UNIQUE_OPEN_EPOCH_DIFFERS",
    )
    genesis = [row for row in tables["audit_events"] if row["id"] == epochs[0]["genesis_event_id"]]
    check(len(genesis) == 1 and genesis[0]["epoch_id"] == epochs[0]["id"], "ACTUAL_GENESIS_MISSING")
    event = strict(genesis[0]["canonical_text"].encode("utf-8"))
    check(
        event.get("user_id") == owner
        and event.get("epoch_id") == epochs[0]["id"]
        and event.get("payload", {}).get("epoch_transition", {}).get("seed_version")
        == registration.bindings["seed_version"],
        "ACTUAL_ORIGINAL_GENESIS_SEED_DIFFERS",
    )
    registration.checkpoint()
    return {
        "capture_origin": "TRUSTED_LOCAL_RR_READ_ONLY_DATABASE_CAPTURE",
        "complete": True,
        "captured_at": json_value(sql_identity["observed_at"]),
        "as_of": registration.value["as_of"],
        "sql_identity": json_value(sql_identity),
        "physical_tables": json_value(physical),
        "physical_columns": json_value(physical_columns),
        "tables": tables,
        "inventory": inventories,
        "jsonb_storage_text": storage,
        "queries": log,
        "scope": "ALL_24_PHYSICAL_TABLES_WITH_EXACT_OWNED_ROW_SCOPE",
        "financial_effect_verified": False,
        "execution_authority": False,
    }


def audit_originals(snapshot: dict[str, Any], bindings: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "MISSING",
        "missing_reason": None,
        "financial_effect_verified": False,
        "integrity_verified": False,
    }
    tables = snapshot["tables"]
    epoch = next(
        row for row in tables["audit_epochs"] if row["id"] == bindings["isolated_db_epoch"]
    )
    events = sorted(
        (row for row in tables["audit_events"] if row["epoch_id"] == epoch["id"]),
        key=lambda row: row["sequence_number"],
    )
    subjects = [row for row in tables["audit_subject_snapshots"] if row["epoch_id"] == epoch["id"]]
    result.update(
        {
            "epoch_row": epoch,
            "event_rows": events,
            "subject_rows": subjects,
            "event_texts": [row["canonical_text"] for row in events],
            "subject_texts": [row["canonical_text"] for row in subjects],
            "head_origin": "DATABASE_HEAD_COLUMN_PROJECTION",
            "checkpoint_origin": "NEW_CAPTURE_CHECKPOINT_FROM_SAME_SQL_HEAD",
            "current_subject_origin": "CURRENT_FULL_ROW_TYPED_PROJECTION",
        }
    )
    try:
        fields = {
            key: epoch[key]
            for key in (
                "schema_version",
                "canonical_version",
                "user_id",
                "epoch_number",
                "status",
                "event_count",
                "last_sequence",
                "last_event_id",
                "last_event_hash",
                "genesis_event_id",
                "genesis_event_hash",
                "previous_epoch_id",
                "previous_seal_hash",
            )
        }
        fields["epoch_id"] = epoch["id"]
        head = audit_types.AuditHead.model_validate_json(json.dumps(fields, allow_nan=False))
        result["head_text"] = audit_data.canonical_text(head.model_dump(mode="python"))
        checkpoint = audit_data.build_checkpoint(head, captured_at=aware(snapshot["captured_at"]))
        result["checkpoint_text"] = audit_data.checkpoint_canonical_text(checkpoint)
        current, current_keys, current_sources, seen = [], [], [], set()
        for row in subjects:
            kind, identity, scope = row["kind"], row["entity_id"], row["scope"]
            check(
                scope == "TENANT" or (scope == "GLOBAL_CATALOG" and kind == "ASSET_PRODUCT"),
                "INVALID_AUDIT_GLOBAL_SCOPE",
            )
            key = (kind, identity)
            if key in seen:
                continue
            seen.add(key)
            check(kind in SUBJECT_TABLES, "UNSUPPORTED_CURRENT_AUDIT_SUBJECT_KIND")
            actual = [item for item in tables[SUBJECT_TABLES[kind]] if item["id"] == identity]
            check(len(actual) == 1, "CURRENT_AUDIT_ORIGINAL_MISSING")
            data = actual[0]
            version = 1
            if kind == "BANK_POSTING":
                data = posting_data.bank_posting_data(data)
                version = posting_data.bank_posting_snapshot_version(data)
            typed = audit_types.AuditSubject.model_validate_json(
                json.dumps(
                    {
                        "user_id": bindings["user_id"],
                        "epoch_id": epoch["id"],
                        "kind": kind,
                        "id": identity,
                        "scope": scope,
                        "snapshot_version": version,
                        "data": data,
                    },
                    allow_nan=False,
                )
            )
            current.append(audit_data.canonical_text(typed.model_dump(mode="python"), raw=True))
            current_keys.append(
                {
                    "kind": kind,
                    "id": identity,
                    "snapshot_hash": sha(
                        b"bounded-funds/audit-subject-v1\0" + current[-1].encode("utf-8")
                    ),
                }
            )
            current_sources.append(
                {
                    "kind": kind,
                    "id": identity,
                    "table": SUBJECT_TABLES[kind],
                    "row_index": tables[SUBJECT_TABLES[kind]].index(actual[0]),
                    "projection": "BANK_POSTING_ORIGINAL_LAYOUT"
                    if kind == "BANK_POSTING"
                    else "ALL_ORIGINAL_COLUMNS",
                }
            )
        result["current_subject_texts"] = current
        result["current_subject_sources"] = current_sources
        result["reference_manifest"] = {
            "event_ids": [row["id"] for row in events],
            "event_count": len(events),
            "subject_keys": [
                {"kind": row["kind"], "id": row["entity_id"], "snapshot_hash": row["snapshot_hash"]}
                for row in subjects
            ],
            "current_keys": current_keys,
        }
        result["status"] = "CAPTURED_TYPED_DATA_NOT_INTEGRITY_VERIFIED"
    except (KeyError, TypeError, ValueError, AssertionError, StopIteration) as error:
        result["missing_reason"] = type(error).__name__ + ":" + str(error)
    return result


class Writer:
    def __init__(self, directory: Path, registration: Registration) -> None:
        self.directory = directory.resolve()
        check(self.directory.is_relative_to(registration.workspace), "OUTPUT_OUTSIDE_WORKSPACE")
        check(not self.directory.exists(), "OUTPUT_ALREADY_EXISTS_IMMUTABLE")
        self.directory.mkdir(parents=True)
        self.registration = registration
        self.files: list[dict[str, Any]] = []
        self.originals: dict[str, dict[str, str]] = {}

    def write(self, name: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        check(re.fullmatch(r"[a-z0-9_-]+", name) is not None, "INVALID_OUTPUT_NAME")
        value = {
            "protocol": "mvp-raw-observation-v2",
            "kind": kind,
            "bindings": self.registration.bindings,
            "payload": payload,
            "collector_registration_ref": self.registration.ref,
        }
        root_ref = self.registration.value["root_registration_ref"]
        if root_ref is not None:
            value["root_registration_ref"] = root_ref
        raw = canonical(value)
        check(len(raw) <= MAX_BYTES, "FULL_ORIGINAL_EXCEEDS_512MIB_NO_TRUNCATION")
        path = self.directory / (name + ".json")
        with path.open("xb") as handle:
            handle.write(raw)
        digest_value = sha(raw)
        self.originals[digest_value] = {"utf8": raw.decode("utf-8")}
        self.files.append(
            {"path": str(path), "sha256": digest_value, "kind": kind, "bytes": len(raw)}
        )
        return {
            "artifact_sha256": digest_value,
            "json_pointer": "/payload",
            "value_sha256": sha(canonical(payload)),
        }


def table_reference(artifact: str, table: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        "artifact_sha256": artifact,
        "json_pointer": "/payload/tables/" + table,
        "value_sha256": sha(canonical(rows)),
    }


def emit_originals(
    snapshot: dict[str, Any], registration: Registration, writer: Writer
) -> dict[str, Any]:
    tables = snapshot["tables"]
    snapshot_ref = writer.write("physical-snapshot", "BUSINESS_SNAPSHOT", snapshot)
    basis_ref = writer.write(
        "financial-basis",
        "FINANCIAL_BASIS",
        {
            "tables": tables,
            "captured_at": snapshot["captured_at"],
            "as_of": registration.value["as_of"],
            "physical_inventory_ref": snapshot_ref,
            "complete": True,
        },
    )
    artifact = basis_ref["artifact_sha256"]
    facts_tables = {name: tables[name] for name in FINANCIAL_TABLES}
    inventory = {
        name: {
            "row_ids": [row["id"] for row in rows],
            "sha256": sha(canonical(rows)),
            "source_ref": table_reference(artifact, name, rows),
        }
        for name, rows in facts_tables.items()
    }
    facts = {
        "protocol": "mvp-financial-facts-v2",
        "user_id": registration.bindings["user_id"],
        "timezone": tables["users"][0]["timezone"],
        "as_of": registration.value["as_of"],
        "complete": True,
        "tables": facts_tables,
        "inventory": inventory,
        "artifact_originals": {artifact: writer.originals[artifact]},
        "policy_state_events_protocol": "TYPED_AUDIT_POLICY_EVENTS_V1",
        "policy_state_events": tables["audit_events"],
        "policy_state_events_source_ref": table_reference(
            artifact, "audit_events", tables["audit_events"]
        ),
        "policy_subjects": tables["audit_subject_snapshots"],
        "policy_subjects_source_ref": table_reference(
            artifact, "audit_subject_snapshots", tables["audit_subject_snapshots"]
        ),
        "missing_originals": [
            "INDEPENDENT_EXPENSE_HISTORY_COVERAGE",
            "ACTUAL_SERVICE_TIMELINE_AND_EFFECT_REFS",
        ],
        "financial_effect_verified": False,
        "execution_authority": False,
    }
    funds = [
        row
        for row in tables["evidence_items"]
        if row["source_type"] == "SIMULATED_NEW_FUNDS_LEDGER"
        and row["evidence_level"] in {"BANK_CONFIRMED", "BANK_OBSERVED"}
        and row["status"] == "VALID"
        and aware(row["observed_at"]) <= registration.as_of
        and aware(row["valid_from"]) <= registration.as_of
        and (row["valid_to"] is None or registration.as_of < aware(row["valid_to"]))
    ]
    if len(funds) == 1:
        facts["income_payload"] = funds[0]["content"]
        facts["income_payload_source_ref"] = {
            "artifact_sha256": artifact,
            "json_pointer": "/payload/tables/evidence_items/"
            + str(tables["evidence_items"].index(funds[0]))
            + "/content",
            "value_sha256": sha(canonical(funds[0]["content"])),
        }
    else:
        facts["missing_originals"].append("UNIQUE_CURRENT_ORIGINAL_NEW_FUNDS_EVIDENCE")
    facts_ref = writer.write("financial-facts", "FINANCIAL_FACTS", {"facts": facts})
    audit = audit_originals(snapshot, registration.bindings)
    snapshot_sha = snapshot_ref["artifact_sha256"]
    audit["original_row_refs"] = {
        "epoch": table_reference(snapshot_sha, "audit_epochs", tables["audit_epochs"]),
        "events": table_reference(snapshot_sha, "audit_events", tables["audit_events"]),
        "subjects": table_reference(
            snapshot_sha, "audit_subject_snapshots", tables["audit_subject_snapshots"]
        ),
    }
    audit_ref = writer.write(
        "audit-originals",
        "AUDIT_DATABASE_ORIGINALS",
        {
            "originals": audit,
            "snapshot_ref": snapshot_ref,
            "current_business_rows_ref": snapshot_ref,
        },
    )
    model_tables = {name: rows for name, rows in tables.items() if name != "alembic_version"}
    trace_ref = writer.write(
        "trace-snapshot",
        "TRACE_SNAPSHOT",
        {
            "role": registration.value["role"],
            "tables": model_tables,
            "inventory": {name: snapshot["inventory"][name] for name in model_tables},
            "captured_at": snapshot["captured_at"],
            "source_snapshot_ref": snapshot_ref,
            "coverage": {
                "complete": True,
                "user_id": registration.bindings["user_id"],
                "epoch_id": registration.bindings["isolated_db_epoch"],
                "tables": sorted(model_tables),
            },
            "missing_originals": ["ACTUAL_EXECUTION_HTTP_INVENTORY_AND_A3_VERIFIER_RESPONSE"],
            "financial_effect_verified": False,
        },
    )
    return {
        "snapshot_ref": snapshot_ref,
        "financial_basis_ref": basis_ref,
        "financial_facts_ref": facts_ref,
        "audit_database_originals_ref": audit_ref,
        "trace_snapshot_ref": trace_ref,
    }


def collect_readonly(
    registration_ref: dict[str, str], output_directory: Path, *, workspace: Path = ROOT
) -> dict[str, Any]:
    registration = Registration(registration_ref, workspace)
    check(not output_directory.exists(), "OUTPUT_ALREADY_EXISTS_IMMUTABLE")
    source = make_url(DatabaseSettings().database_url)
    check(
        source.host == "127.0.0.1"
        and source.port == 54329
        and source.get_backend_name() == "postgresql",
        "NONLOCAL_SIMULATION_ENDPOINT",
    )
    engine = create_engine(
        source.set(database=registration.database),
        poolclass=NullPool,
        connect_args={"options": "-c timezone=UTC"},
    )
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                snapshot = read_snapshot(connection, registration)
            # Only a normally exited original transaction reaches this point.
        registration.checkpoint()
        snapshot["readonly_transaction_exited"] = "COMPLETED"
        writer = Writer(output_directory, registration)
        refs = emit_originals(snapshot, registration, writer)
        registration.checkpoint()
        manifest = {
            "protocol": "mvp-readonly-original-collection-v1",
            "status": "CAPTURED_RAW_NOT_ECONOMICALLY_VERIFIED",
            "bindings": registration.bindings,
            "collector_registration_ref": registration.ref,
            "database_name": registration.database,
            "snapshot_refs": refs,
            "files": writer.files,
            "capture_id": str(uuid4()),
            "execution_authority": False,
            "financial_effect_verified": False,
            "independent_phase_proof_verified": False,
            "missing_originals": [
                "REAL_HTTP_ACTOR_CLOCK_ERROR_TIMELINE",
                "REGISTERED_EXPENSE_COVERAGE",
                "A3_ORIGINAL_VERIFIER_RESPONSE",
                "ACTUAL_RUN_TO_CAPTURE_ATTESTATION",
            ],
        }
        raw = canonical(manifest)
        with (writer.directory / "manifest.json").open("xb") as handle:
            handle.write(raw)
        return manifest
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--registration-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = collect_readonly(
        {"path": str(args.registration.resolve()), "sha256": args.registration_sha256}, args.output
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "files": len(result["files"]),
                "output": str(args.output.resolve()),
                "financial_effect_verified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
