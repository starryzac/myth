"""TOOL_TEST_ONLY SQL spies and saved synthetic rows; no DB or financial execution."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.domain import audit_chain as audit_data
from app.domain import audit_chain_types as audit_types
from sqlalchemy.engine import Connection

from scripts import mvp_readonly_collector as c

AT = datetime(2026, 2, 15, 4, 0, tzinfo=UTC)
STAMP = "2026-02-15T04:00:00.000000Z"


class Fixture:
    def __init__(self) -> None:
        self.directory = c.ROOT / ".runtime/W1-readonly-collector-tool-fixtures" / uuid4().hex
        self.directory.mkdir(parents=True)
        self.owner, self.epoch, self.genesis = (str(uuid4()) for _ in range(3))
        self.schema = c.schema_contract()
        self.bindings = {
            "experiment_run_id": str(uuid4()),
            "case_id": "TOOL_TEST_ONLY",
            "arm_id": "P",
            "execution_mode": "SERVICE_INTEGRATION",
            "input_sha256": "1" * 64,
            "oracle_sha256": "2" * 64,
            "design_sha256": "3" * 64,
            "rule_sha256": "4" * 64,
            "source_sha256": "",
            "seed_version": "mvp-301-v6",
            "isolated_db_epoch": self.epoch,
            "purpose": "DEVELOPMENT",
            "user_id": self.owner,
        }
        self.marker = self.directory / "source-marker.py"
        self.marker.write_bytes(b"# TOOL_TEST_ONLY registered marker\n")
        sources = c.required_sources(c.ROOT) | {self.marker.relative_to(c.ROOT).as_posix()}
        self.source = self.file(
            "source.json",
            {
                "purpose": "DEVELOPMENT",
                "files": [
                    {"path": name, "sha256": c.sha((c.ROOT / name).read_bytes())}
                    for name in sorted(sources)
                ],
            },
        )
        self.bindings["source_sha256"] = self.source["sha256"]
        self.registration: dict[str, Any] = {
            "protocol": c.PROTOCOL,
            "bindings": self.bindings,
            "database_name": "bf_test_" + uuid4().hex,
            "as_of": STAMP,
            "role": "FINAL",
            "source_ref": self.source,
            "root_registration_ref": None,
        }
        self.ref = self.file("registration.json", self.registration)
        self.physical = [
            {
                "schema_name": "public",
                "table_name": name,
                "relkind": "r",
                "row_security": False,
                "force_row_security": False,
            }
            for name in sorted(self.schema)
        ]
        self.columns = [
            {
                "schema_name": "public",
                "table_name": name,
                "column_name": key,
                "ordinal": ordinal,
                **fields,
                "formatted_type": fields["formatted_type"],
            }
            for name, columns in self.schema.items()
            for ordinal, (key, fields) in enumerate(columns.items(), start=1)
        ]
        self.rows: dict[str, list[dict[str, Any]]] = {name: [] for name in self.schema}
        self.rows["users"] = [
            self.row("users", id=self.owner, is_simulated=True, timezone="Asia/Shanghai")
        ]
        self.rows["audit_epochs"] = [
            self.row(
                "audit_epochs",
                id=self.epoch,
                status="OPEN",
                event_count=1,
                last_sequence=1,
                last_event_id=self.genesis,
                genesis_event_id=self.genesis,
                last_event_hash="5" * 64,
                genesis_event_hash="5" * 64,
                schema_version="audit-head-v1",
                canonical_version="audit-canonical-json-v1",
                previous_epoch_id=None,
                previous_seal_hash=None,
            )
        ]
        text = c.canonical(
            {
                "user_id": self.owner,
                "epoch_id": self.epoch,
                "payload": {"epoch_transition": {"seed_version": "mvp-301-v6"}},
            }
        ).decode()
        self.rows["audit_events"] = [
            self.row("audit_events", id=self.genesis, epoch_id=self.epoch, canonical_text=text)
        ]
        self.rows["alembic_version"] = [{"version_num": "0008_TOOL_TEST_ONLY"}]
        self.identity = {
            "database_name": self.registration["database_name"],
            "backend_pid": 99,
            "transaction_id": "4294967355",
            "isolation": "repeatable read",
            "read_only": "on",
            "database_role": "TOOL_TEST_ONLY",
            "observed_at": AT,
            "snapshot_id": "1:2:",
        }
        self.count_overrides: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def row(self, table: str, **updates: Any) -> dict[str, Any]:
        values: dict[str, Callable[[], Any]] = {
            "uuid": lambda: uuid4(),
            "int8": lambda: 0,
            "int4": lambda: 1,
            "varchar": lambda: "TOOL_TEST_ONLY",
            "text": lambda: "原TEXT\n  untouched",
            "bool": lambda: True,
            "date": lambda: date(2026, 2, 15),
            "timestamptz": lambda: AT,
            "jsonb": lambda: {"nullable": None},
        }
        result = {key: values[field["type_name"]]() for key, field in self.schema[table].items()}
        if "user_id" in result:
            result["user_id"] = self.owner
        result.update(updates)
        return result

    def file(self, name: str, body: Any) -> dict[str, str]:
        raw = c.canonical(body)
        path = self.directory / name
        with path.open("xb") as handle:
            handle.write(raw)
        return {"path": str(path), "sha256": c.sha(raw)}

    def parsed(self) -> c.Registration:
        return c.Registration(self.ref, c.ROOT)

    def snapshot(self) -> dict[str, Any]:
        return c.read_snapshot(cast(Connection, self), self.parsed())

    def exec_driver_sql(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        *,
        execution_options: dict[str, Any] | None = None,
    ) -> Result:
        if not parameters:
            assert parameters is None and execution_options == {"no_parameters": True}
        else:
            assert execution_options is None
        self.calls.append((sql, parameters or {}))
        if sql == "SET TRANSACTION READ ONLY":
            return Result([], returns_rows=False)
        if sql == c.IDENTITY_SQL:
            return Result([self.identity])
        if sql == c.TABLE_SQL:
            return Result(self.physical)
        if sql == c.COLUMN_SQL:
            return Result(self.columns)
        name = re.search(r'FROM "public"\."([a-z_]+)"', sql)
        assert name is not None, sql
        table = name[1]
        if sql.startswith("SELECT count(*)"):
            return Result([{"row_count": self.count_overrides.get(table, len(self.rows[table]))}])
        rows = []
        for original in self.rows[table]:
            row = dict(original)
            row.update(
                {
                    "__jsonb_text_" + key: None
                    if row[key] is None
                    else json.dumps(row[key], ensure_ascii=False)
                    for key, field in self.schema[table].items()
                    if field["type_name"] == "jsonb"
                }
            )
            rows.append(row)
        return Result(rows)


class Result:
    def __init__(self, rows: list[dict[str, Any]], *, returns_rows: bool = True) -> None:
        self.rows, self.returns_rows = rows, returns_rows

    def mappings(self) -> Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return deepcopy(self.rows)


def test_complete_physical_snapshot_preserves_columns_nulls_raw_json_and_text() -> None:
    f = Fixture()
    item = f.row(
        "evidence_items",
        content={
            "amount_cents": True,
            "NULL": None,
            "large_original_integer": 2**100,
            "numeric": 0.25,
        },
        valid_to=None,
    )
    f.rows["evidence_items"] = [item]
    product = f.row("asset_products")
    f.rows["asset_products"] = [product]
    before = deepcopy(f.rows)
    snapshot = f.snapshot()
    assert len(snapshot["tables"]) == len(snapshot["inventory"]) == 24
    assert snapshot["tables"]["evidence_items"][0] == c.json_value(item)
    assert (
        snapshot["tables"]["audit_events"][0]["canonical_text"]
        == before["audit_events"][0]["canonical_text"]
    )
    assert snapshot["tables"]["evidence_items"][0]["valid_to"] is None
    assert snapshot["inventory"]["asset_products"]["scope"] == "GLOBAL_CATALOG"
    assert snapshot["inventory"]["alembic_version"]["scope"] == "SCHEMA_METADATA"
    assert before == f.rows
    assert snapshot["financial_effect_verified"] is snapshot["execution_authority"] is False
    assert f.calls[0][0] == "SET TRANSACTION READ ONLY"
    assert all(
        not any(
            word in sql.upper() for word in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ")
        )
        for sql, _ in f.calls
    )
    assert sum(sql.startswith("SELECT count(*)") for sql, _ in f.calls) == 24
    for sql, parameters in f.calls:
        if 'FROM "public"' in sql and not any(
            name in sql for name in ('"asset_products"', '"alembic_version"')
        ):
            assert parameters == {"owner": UUID(f.owner)}


@pytest.mark.parametrize(
    "case",
    [
        "extra",
        "missing",
        "schema",
        "rls",
        "foreign",
        "partition",
        "column",
        "type",
        "nullable",
        "length",
        "duplicate",
    ],
)
def test_physical_inventory_refuses_unregistered_or_hidden_schema(case: str) -> None:
    f = Fixture()
    if case == "extra":
        f.physical.append(dict(f.physical[0], table_name="hidden"))
    elif case == "missing":
        f.physical.pop()
    elif case == "schema":
        f.physical[0]["schema_name"] = "other"
    elif case == "rls":
        f.physical[0]["row_security"] = True
    elif case in {"foreign", "partition"}:
        f.physical[0]["relkind"] = "f" if case == "foreign" else "p"
    elif case == "column":
        f.columns.pop()
    elif case == "type":
        f.columns[0]["type_name"] = "numeric"
    elif case == "nullable":
        f.columns[0]["not_null"] = not f.columns[0]["not_null"]
    elif case == "length":
        f.columns[0]["formatted_type"] = "character varying(999)"
    else:
        f.columns.append(dict(f.columns[0]))
    with pytest.raises(c.CollectorRefused):
        f.snapshot()


@pytest.mark.parametrize(
    "key,value",
    [("database_name", "formal_history"), ("isolation", "read committed"), ("read_only", "off")],
)
def test_real_connection_identity_is_required(key: str, value: str) -> None:
    f = Fixture()
    f.identity[key] = value
    with pytest.raises(c.CollectorRefused, match="ACTUAL_DATABASE_OR_RR_READ_ONLY_DIFFERS"):
        f.snapshot()


@pytest.mark.parametrize(
    "case",
    [
        "foreign_user",
        "foreign_row",
        "nonsimulated",
        "epoch",
        "two_open",
        "no_open",
        "seed",
        "genesis",
        "count",
        "columns",
        "bool_money",
    ],
)
def test_context_and_actual_full_rows_fail_closed(case: str) -> None:
    f = Fixture()
    if case == "foreign_user":
        f.rows["users"][0]["id"] = str(uuid4())
    elif case == "foreign_row":
        f.rows["accounts"] = [f.row("accounts", user_id=str(uuid4()))]
    elif case == "nonsimulated":
        f.rows["users"][0]["is_simulated"] = False
    elif case == "epoch":
        f.rows["audit_epochs"][0]["id"] = str(uuid4())
    elif case == "two_open":
        f.rows["audit_epochs"].append(f.row("audit_epochs", id=str(uuid4()), status="OPEN"))
        f.rows["audit_epochs"].sort(key=lambda row: row["id"])
    elif case == "no_open":
        f.rows["audit_epochs"][0]["status"] = "SEALED"
    elif case == "seed":
        body = c.strict(f.rows["audit_events"][0]["canonical_text"].encode())
        body["payload"]["epoch_transition"]["seed_version"] = "OTHER"
        f.rows["audit_events"][0]["canonical_text"] = c.canonical(body).decode()
    elif case == "genesis":
        f.rows["audit_events"] = []
    elif case == "count":
        f.count_overrides["users"] = 2
    elif case == "columns":
        f.rows["users"][0].pop("external_ref")
    else:
        f.rows["accounts"] = [f.row("accounts", balance_cents=True)]
    with pytest.raises((c.CollectorRefused, KeyError)):
        f.snapshot()


@pytest.mark.parametrize(
    "value", [Decimal("1.25"), float("nan"), datetime(2026, 2, 15), {1: "not-string"}]
)
def test_original_scalar_never_coerces_or_invents_missing_timezone(value: Any) -> None:
    with pytest.raises(c.CollectorRefused):
        c.json_value(value)


def test_new_files_are_byte_bound_and_never_overwrite() -> None:
    f = Fixture()
    registration = f.parsed()
    snapshot = f.snapshot()
    writer = c.Writer(f.directory / "captured", registration)
    refs = c.emit_originals(snapshot, registration, writer)
    assert len(writer.files) == 5
    original_files = {item["path"]: Path(item["path"]).read_bytes() for item in writer.files}
    assert all(
        c.sha(raw) == next(item["sha256"] for item in writer.files if item["path"] == path)
        for path, raw in original_files.items()
    )
    basis = next(
        c.strict(raw)
        for raw in original_files.values()
        if c.strict(raw)["kind"] == "FINANCIAL_BASIS"
    )
    facts = next(
        c.strict(raw)["payload"]["facts"]
        for raw in original_files.values()
        if c.strict(raw)["kind"] == "FINANCIAL_FACTS"
    )
    assert set(facts["tables"]) == set(facts["inventory"]) == set(c.FINANCIAL_TABLES)
    assert facts["policy_state_events"] == basis["payload"]["tables"]["audit_events"]
    assert facts["policy_subjects"] == basis["payload"]["tables"]["audit_subject_snapshots"]
    assert "expense_history" not in facts and "income_payload" not in facts
    for table in c.FINANCIAL_TABLES:
        ref = facts["inventory"][table]["source_ref"]
        assert ref["artifact_sha256"] == refs["financial_basis_ref"]["artifact_sha256"]
        assert ref["value_sha256"] == c.sha(c.canonical(basis["payload"]["tables"][table]))
    with pytest.raises(c.CollectorRefused, match="OUTPUT_ALREADY_EXISTS"):
        c.Writer(writer.directory, registration)
    with pytest.raises(FileExistsError):
        writer.write("financial-basis", "OTHER", {})
    assert original_files == {path: Path(path).read_bytes() for path in original_files}


def test_capture_checkpoint_is_new_projection_and_never_integrity_or_economic_success() -> None:
    f = Fixture()
    audit = c.audit_originals(f.snapshot(), f.bindings)
    assert audit["head_origin"] == "DATABASE_HEAD_COLUMN_PROJECTION"
    assert audit["checkpoint_origin"] == "NEW_CAPTURE_CHECKPOINT_FROM_SAME_SQL_HEAD"
    assert audit["event_texts"] == [f.rows["audit_events"][0]["canonical_text"]]
    assert audit["integrity_verified"] is audit["financial_effect_verified"] is False
    assert audit["status"] == "CAPTURED_TYPED_DATA_NOT_INTEGRITY_VERIFIED"
    head = audit_types.AuditHead.model_validate_json(audit["head_text"])
    checkpoint = audit_data.parse_checkpoint(audit["checkpoint_text"])
    assert str(head.epoch_id) == str(checkpoint.epoch_id) == f.epoch
    assert checkpoint.captured_at == AT


@pytest.mark.parametrize(
    "case",
    [
        "source_bytes",
        "registration_bytes",
        "parsed_binding",
        "parsed_sources",
        "root_binding",
        "extra_field",
        "source_purpose",
        "database",
        "new_source",
    ],
)
def test_registration_source_and_root_bytes_are_rechecked(case: str) -> None:
    f = Fixture()
    registration = f.parsed()
    if case == "source_bytes":
        f.marker.write_bytes(b"# changed\n")
    elif case == "registration_bytes":
        Path(f.ref["path"]).write_bytes(b"{}")
    elif case == "parsed_binding":
        registration.value["bindings"]["user_id"] = str(uuid4())
    elif case == "parsed_sources":
        registration.sources.pop(next(iter(registration.sources)))
    elif case == "new_source":
        # Exercise current inventory comparison without touching real API source.
        assert "apps/api/app/new_collector_source.py" not in registration.sources
        with pytest.MonkeyPatch.context() as patch:
            old = c.required_sources
            patch.setattr(
                c,
                "required_sources",
                lambda root: old(root) | {"apps/api/app/new_collector_source.py"},
            )
            with pytest.raises(c.CollectorRefused, match="NEW_CURRENT_SOURCE_UNREGISTERED"):
                registration.checkpoint()
        return
    else:
        body = deepcopy(f.registration)
        if case == "root_binding":
            root = f.file(
                "root.json",
                {
                    "bindings": dict(f.bindings, arm_id="B0"),
                    "database_name": f.registration["database_name"],
                },
            )
            body["root_registration_ref"] = root
        elif case == "extra_field":
            body["success"] = True
        elif case == "source_purpose":
            source = c.strict(Path(f.source["path"]).read_bytes())
            source["purpose"] = "MVP_FROZEN"
            body["source_ref"] = f.file("other-source.json", source)
            body["bindings"]["source_sha256"] = body["source_ref"]["sha256"]
        elif case == "database":
            body["database_name"] = "bounded_funds"
        ref = f.file("other-registration.json", body)
        with pytest.raises(c.CollectorRefused):
            c.Registration(ref, c.ROOT)
        return
    with pytest.raises(c.CollectorRefused):
        registration.checkpoint()


def test_loaded_function_source_not_a_status_string() -> None:
    module = ModuleType("TOOL_TEST_ONLY")
    module.__file__ = str(c.ROOT / ".runtime/never-executed-source.py")
    original = b"def captured():\n    return 1\n"
    exec(compile(original, module.__file__, "exec", dont_inherit=True), module.__dict__)
    c.loaded_guard(module, original)
    with pytest.raises(c.CollectorRefused, match="LOADED_FUNCTION_SOURCE_MISMATCH"):
        c.loaded_guard(module, b"def captured():\n    return 'SUCCESS'\n")


def test_loaded_class_method_must_match_current_original_source() -> None:
    module = ModuleType("TOOL_TEST_ONLY")
    module.__file__ = str(c.ROOT / ".runtime/never-executed-class.py")
    original = b"class Writer:\n    def write(self):\n        return False\n"
    exec(compile(original, module.__file__, "exec", dont_inherit=True), module.__dict__)
    c.loaded_guard(module, original)
    with pytest.raises(c.CollectorRefused, match="LOADED_METHOD_SOURCE_MISMATCH"):
        c.loaded_guard(module, b"class Writer:\n    def write(self):\n        return True\n")


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_strict_registration_json(raw: bytes) -> None:
    with pytest.raises(c.CollectorRefused):
        c.strict(raw)


@pytest.mark.parametrize("bound", [False, True])
def test_driver_literal_percent_is_not_a_parameter_placeholder(bound: bool) -> None:
    """TOOL_ONLY driver protocol; real PG18102def supplies the original failure."""
    calls: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = []

    class Driver:
        def exec_driver_sql(
            self,
            sql: str,
            parameters: dict[str, Any] | None = None,
            *,
            execution_options: dict[str, Any] | None = None,
        ) -> Result:
            calls.append((sql, parameters, execution_options))
            if "LIKE 'pg_%'" in sql:
                if parameters is not None or execution_options != {"no_parameters": True}:
                    raise ValueError("only '%s', '%b', '%t' are allowed; got \"%'\"")
            else:
                assert parameters == {"owner": "actual-owner"} and execution_options is None
            return Result([{"captured": "TOOL_TEST_ONLY"}])

    params = {"owner": "actual-owner"} if bound else {}
    sql = "SELECT %(owner)s" if bound else "SELECT name WHERE name NOT LIKE 'pg_%'"
    log: list[dict[str, Any]] = []
    assert c.query(cast(Connection, Driver()), sql, params, log) == [{"captured": "TOOL_TEST_ONLY"}]
    assert log == [{"sql": sql, "parameters": params}]
    assert calls == [(sql, params if bound else None, None if bound else {"no_parameters": True})]
