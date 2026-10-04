"""Reviewable, explicitly gated 0006 -> 0007 formal schema preservation helper.

Without --apply-preserving-old-data, preflight only reads local pinned files.
With the flag, exact .env formal identity and a complete RR/READ ONLY baseline
must match before the standard Alembic upgrade. The repository's env.py uses its
own migration connection; before/after samples each use their own RR/RO transaction.
No seed/reset, business DML, downgrade, database creation/deletion, or test runner.
Ordinary concurrent application writes are detected by the preservation comparison;
the runner must keep formal business writers quiescent during the short migration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
BEFORE_REVISION = "0006_audit_chain"
AFTER_REVISION = "0007_external_bank_facts"
BASELINE_PATH = "docs/progress/evidence/MVP-304-targeted-close-formal-snapshot.json"
MIGRATION_PATH = "apps/api/alembic/versions/0007_external_bank_facts.py"
BASELINE_DATA_SHA = "281b302813a75cda170f2b20195e9719283cef699300db34741cc7c4db09b85f"
ORIGINAL_20_DATA_SHA = "dbbc4819f11e156ddcc11a2af3d08739cbe4101c9d2f547469fa3dd05101e2d0"
PINS = {
    BASELINE_PATH: "bf6d25daf144b52f161bee3dba1d6c3428329779f1f3bc0d92b59a004dae1991",
    MIGRATION_PATH: "b859958079f3e0f746796f726036b2134526c484e39473bb93bd89a67be1ca9d",
    ".env": "96844669463bcc90ea46cba5f32cc17b3b84e6e823396ca0b6cec29babd68726",
}
AUDIT_TABLES = ("audit_epochs", "audit_events", "audit_subject_snapshots")
ORIGINAL_TIMESTAMP_FIELDS = {
    "accounts": {"created_at", "observed_at"},
    "asset_positions": {"created_at", "purchased_at", "maturity_at", "available_at"},
    "asset_products": {"created_at", "effective_from"},
    "credit_card_bills": {"created_at"},
    "evidence_items": {"created_at", "observed_at", "valid_from", "valid_to"},
    "simulated_bank_postings": {"created_at", "occurred_at"},
    "transactions": {"created_at", "occurred_at", "observed_at"},
    "users": {"created_at"},
}
EXTERNAL_FIELDS = frozenset(
    "id user_id created_at protocol_version source_id external_ref kind account_id "
    "amount_cents currency counterparty_ref occurred_at observed_at idempotency_key "
    "request request_canonical_text request_hash bank_status accepted_at settled_at "
    "bank_result bank_result_canonical_text bank_result_hash projection_status "
    "projected_at projection_result projection_result_canonical_text "
    "projection_result_hash updated_at".split()
)


class PreservationError(ValueError):
    """A safe identity, source pin, schema, or original-data proof differs."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PreservationError(message)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encode(value: Any) -> str:
    # Exact algorithm of the actual MVP-304 all-table baseline.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def checked_files() -> dict[str, bytes]:
    result = {}
    for name, digest in PINS.items():
        path = ROOT / name
        require(path.is_file() and not path.is_symlink(), f"Missing/symlinked pinned file: {name}")
        require(path.stat().st_size <= 16 * 1024 * 1024, f"Oversized pinned file: {name}")
        value = path.read_bytes()
        require(sha(value) == digest, f"Pinned source/baseline changed: {name}")
        result[name] = value
    return result


def validate_snapshot(value: dict[str, Any], revision: str) -> None:
    require(value.get("database_name") == "bounded_funds", "Snapshot formal database differs")
    require(value.get("database_revision") == revision, "Snapshot Alembic revision differs")
    require(value.get("isolation") == "repeatable read", "Snapshot did not observe RR")
    require(value.get("read_only") is True, "Snapshot did not observe READ ONLY")
    data, columns = value.get("data"), value.get("columns")
    require(type(data) is dict and type(columns) is dict, "Incomplete snapshot objects")
    assert isinstance(data, dict) and isinstance(columns, dict)
    require(data.keys() == columns.keys(), "Row and column table inventories differ")
    for name, rows in data.items():
        require(type(rows) is list and type(columns[name]) is list, f"Invalid table: {name}")
        require(
            bool(columns[name]) and len(set(columns[name])) == len(columns[name]),
            f"Empty/duplicate column inventory: {name}",
        )
        require(rows == sorted(rows, key=encode), f"Rows are not canonical: {name}")
        require(
            all(type(row) is dict and row.keys() == set(columns[name]) for row in rows),
            f"Rows omit or add unknown columns: {name}",
        )
    require(
        value.get("table_counts") == {name: len(rows) for name, rows in data.items()},
        "Snapshot row counts differ from its complete rows",
    )
    require(
        value.get("table_sha256")
        == {name: sha(encode(rows).encode("utf-8")) for name, rows in data.items()},
        "Snapshot table hashes differ from its rows",
    )
    require(
        value.get("data_sha256") == sha(encode(data).encode("utf-8")),
        "Snapshot full data hash differs",
    )
    require(
        data.get("alembic_version") == [{"version_num": revision}],
        "Expected exactly one revision row",
    )
    for name in AUDIT_TABLES:
        require(data.get(name) == [], f"Formal legacy audit history changed: {name}")


def validate_original_20_bridge(snapshot: dict[str, Any], original: dict[str, Any]) -> None:
    """Bind two authentic representations without substituting their native hashes.

    The 20-table artifact is driver-typed SELECT ORDER BY id + default=str. The
    23-table artifact is PG to_jsonb + encode sort. Only known top-level timestamp
    columns differ in representation; nested JSON, numeric types and all other
    originals must remain exact. Future DB samples independently reproduce both.
    """
    columns = original["original_columns"]
    require(columns.keys() == original["data"].keys(), "Original 20 inventory differs")
    for name, fields in columns.items():
        require(set(fields) <= set(snapshot["columns"][name]), f"Original columns missing: {name}")
        rows = original["data"][name]
        require(
            rows == sorted(rows, key=lambda row: row["id"]),
            f"Original typed rows are not ordered by id: {name}",
        )
        actual = {row["id"]: row for row in snapshot["data"][name]}
        require(
            len(actual) == len(rows) == len(snapshot["data"][name]),
            f"Original/current row counts differ: {name}",
        )
        require(
            {row["id"] for row in rows} == actual.keys(), f"Original row identities differ: {name}"
        )
        for row in rows:
            require(set(row) == set(fields), f"Original row columns differ: {name}")
            for field in fields:
                value, pg_value = row[field], actual[row["id"]][field]
                if field in ORIGINAL_TIMESTAMP_FIELDS.get(name, set()) and value is not None:
                    require(
                        type(value) is str and type(pg_value) is str,
                        f"Original timestamp representation differs: {name}.{field}",
                    )
                    instant = datetime.fromisoformat(value)
                    require(
                        instant.tzinfo is not None and instant.utcoffset() is not None,
                        f"Original timestamp is naive: {name}.{field}",
                    )
                    require(
                        str(instant) == value and instant.isoformat() == pg_value,
                        f"Original timestamp value differs: {name}.{field}",
                    )
                else:
                    require(
                        encode(value) == encode(pg_value),
                        f"Original typed/PG value differs: {name}.{field}",
                    )


def preflight() -> tuple[dict[str, Any], str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url

    files = checked_files()
    baseline: dict[str, Any] = json.loads(files[BASELINE_PATH])
    validate_snapshot(baseline, BEFORE_REVISION)
    require(len(baseline["data"]) == 23, "Baseline must contain all actual 23 tables")
    require(baseline["data_sha256"] == BASELINE_DATA_SHA, "Formal baseline data pin differs")
    old = baseline.get("original_20_projection")
    require(
        type(old) is dict and len(old.get("original_columns", {})) == 20,
        "Missing original 20 business-table projection",
    )
    assert isinstance(old, dict)
    validate_original_20_bridge(baseline, old)
    require(
        old["table_counts"] == {name: len(rows) for name, rows in old["data"].items()},
        "Original 20 row counts differ",
    )
    require(
        old["data_sha256"] == ORIGINAL_20_DATA_SHA == sha(encode(old["data"]).encode("utf-8")),
        "Original 20 hash differs",
    )
    require(
        "external_fact_id" not in baseline["columns"]["simulated_bank_postings"],
        "Baseline already contains the new posting origin",
    )
    env = dotenv_values(ROOT / ".env")
    require(env.get("SIMULATION_MODE") == "true", "Formal .env must remain simulation=true")
    raw_url = env.get("DATABASE_URL")
    require(type(raw_url) is str and bool(raw_url), "Formal .env DATABASE_URL is missing")
    assert isinstance(raw_url, str)
    url = make_url(raw_url)
    require(
        (url.drivername, url.host, url.port, url.database)
        == ("postgresql+psycopg", "127.0.0.1", 54329, "bounded_funds"),
        "Only the exact .env formal bounded_funds@127.0.0.1:54329 is permitted",
    )
    require(not url.query, "Formal URL query overrides are forbidden")
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    require(scripts.get_heads() == [AFTER_REVISION], "Only the reviewed 0007 head is permitted")
    migration = scripts.get_revision(AFTER_REVISION)
    require(migration is not None, "Reviewed migration is missing")
    assert migration is not None
    require(
        Path(migration.path).resolve() == (ROOT / MIGRATION_PATH).resolve()
        and migration.down_revision == BEFORE_REVISION,
        "Alembic head does not load the pinned 0006 -> 0007 migration file",
    )
    return baseline, url.render_as_string(hide_password=False)


def sample(
    engine: Engine,
    *,
    revision: str,
    expected_counts: dict[str, int],
    original_columns: dict[str, list[str]],
) -> dict[str, Any]:
    from sqlalchemy import inspect, text

    require(
        (engine.url.drivername, engine.url.host, engine.url.port, engine.url.database)
        == ("postgresql+psycopg", "127.0.0.1", 54329, "bounded_funds"),
        "Engine formal identity differs",
    )
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            require(
                connection.scalar(text("SELECT current_database()")) == "bounded_funds",
                "Actual connected database is not the original formal database",
            )
            inspector = inspect(connection)
            names = sorted(inspector.get_table_names(schema="public"))
            require(set(names) == set(expected_counts), "Actual formal table inventory changed")
            quote = connection.dialect.identifier_preparer.quote
            data, columns = {}, {}
            for name in names:
                # Fail before loading an unexpectedly changed or large financial row set.
                count = connection.scalar(text(f"SELECT count(*) FROM public.{quote(name)}"))
                require(count == expected_counts[name], f"Actual row count changed: {name}")
                columns[name] = [
                    column["name"] for column in inspector.get_columns(name, schema="public")
                ]
                data[name] = sorted(
                    connection.scalars(text(f"SELECT to_jsonb(t) FROM public.{quote(name)} AS t")),
                    key=encode,
                )
            typed_rows = {}
            for name, fields in original_columns.items():
                require(
                    name in names and set(fields) <= set(columns[name]),
                    f"Original typed projection columns differ: {name}",
                )
                typed_rows[name] = [
                    dict(row)
                    for row in connection.exec_driver_sql(
                        "SELECT "
                        + ",".join(quote(field) for field in fields)
                        + f" FROM public.{quote(name)} ORDER BY id"
                    ).mappings()
                ]
            # Preserve the exact old driver/default=str representation and ORDER BY id.
            typed_data = json.loads(json.dumps(typed_rows, default=str, sort_keys=True))
            original = {
                "original_columns": original_columns,
                "data": typed_data,
                "table_counts": {name: len(rows) for name, rows in typed_data.items()},
                "data_sha256": sha(encode(typed_data).encode("utf-8")),
            }
            value = {
                "read_at": datetime.now(UTC).isoformat(),
                "database_name": connection.scalar(text("SELECT current_database()")),
                "database_revision": connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ),
                "isolation": connection.scalar(text("SHOW transaction_isolation")),
                "read_only": connection.scalar(text("SHOW transaction_read_only")) == "on",
                "columns": columns,
                "data": data,
                "table_counts": {name: len(rows) for name, rows in data.items()},
                "table_sha256": {
                    name: sha(encode(rows).encode("utf-8")) for name, rows in data.items()
                },
                "data_sha256": sha(encode(data).encode("utf-8")),
                "original_20_projection": original,
            }
            validate_snapshot(value, revision)
            validate_original_20_bridge(value, original)
            return value


def compare_before(actual: dict[str, Any], baseline: dict[str, Any]) -> None:
    for key in (
        "database_revision",
        "columns",
        "data",
        "table_counts",
        "table_sha256",
        "data_sha256",
        "original_20_projection",
    ):
        require(actual[key] == baseline[key], f"Formal pre-migration baseline differs: {key}")


def compare_after(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    preserved = {}
    for name, columns in before["columns"].items():
        expected_columns = columns + (
            ["external_fact_id"] if name == "simulated_bank_postings" else []
        )
        require(after["columns"][name] == expected_columns, f"Unexpected old-column change: {name}")
        projected = sorted(
            [{column: row[column] for column in columns} for row in after["data"][name]], key=encode
        )
        if name == "alembic_version":
            require(projected == [{"version_num": AFTER_REVISION}], "New revision is not 0007")
        else:
            require(projected == before["data"][name], f"An original field or row changed: {name}")
            preserved[name] = sha(encode(projected).encode("utf-8"))
    require(after["data"]["external_bank_facts"] == [], "Migration inserted external facts")
    require(
        set(after["columns"]["external_bank_facts"]) == EXTERNAL_FIELDS,
        "Unexpected external fact column layout",
    )
    require(
        all(row["external_fact_id"] is None for row in after["data"]["simulated_bank_postings"]),
        "Migration assigned a new origin to an original posting",
    )
    old = after["original_20_projection"]
    require(
        old == before["original_20_projection"]
        and old["data_sha256"] == ORIGINAL_20_DATA_SHA == sha(encode(old["data"]).encode("utf-8")),
        "Migration changed original 20 business-table fields",
    )
    return {
        "all_old_field_rows_preserved_except_alembic_version": True,
        "preserved_old_table_sha256": preserved,
        "original_20_data_sha256": ORIGINAL_20_DATA_SHA,
        "original_20_representation": "DRIVER_TYPED_SELECT_ORDER_BY_ID_DEFAULT_STR",
        "new_external_fact_id_all_null": True,
        "external_bank_fact_rows": 0,
        "registered_audit_epochs": 0,
        "audit_status": "LEGACY_UNAUDITED",
        "audit_chain_verified_as_valid": False,
        "formal_seed_reset": False,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-preserving-old-data", action="store_true")
    args = parser.parse_args()
    baseline, url = preflight()
    if not args.apply_preserving_old_data:
        print(
            json.dumps(
                {
                    "status": "OFFLINE_READY",
                    "database_connected": False,
                    "source_pins": PINS,
                    "formal_database": "bounded_funds@127.0.0.1:54329",
                },
                indent=2,
            )
        )
        return 0
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from alembic import command
    from alembic.config import Config
    from app.db.session import create_database_engine

    tool_path = Path(__file__).resolve()
    tool_hash = sha(tool_path.read_bytes())
    started_at = datetime.now(UTC).isoformat()
    evidence_dir = (
        ROOT
        / ".runtime"
        / (
            "MVP-401-formal-migration-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        )
    )
    evidence_dir.mkdir(exist_ok=False)
    stage = "PRE_MIGRATION_RR_READ_ONLY"
    engine = create_database_engine(url)
    try:
        original_columns = baseline["original_20_projection"]["original_columns"]
        before = sample(
            engine,
            revision=BEFORE_REVISION,
            expected_counts=baseline["table_counts"],
            original_columns=original_columns,
        )
        compare_before(before, baseline)
        write_json(evidence_dir / "before.json", before)
        checked_files()
        stage = "ALEMBIC_UPGRADE_REVIEWED_0007_HEAD"
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        stage = "POST_MIGRATION_RR_READ_ONLY"
        after = sample(
            engine,
            revision=AFTER_REVISION,
            expected_counts={**baseline["table_counts"], "external_bank_facts": 0},
            original_columns=original_columns,
        )
        write_json(evidence_dir / "after.json", after)
        checked_files()
        require(
            sha(tool_path.read_bytes()) == tool_hash, "Migration helper changed during execution"
        )
        stage = "COMPARE_ORIGINAL_FIELDS"
        comparison = compare_after(after, before)
        comparison.update(
            {
                "status": "MIGRATED_PRESERVING_OLD_DATA",
                "started_at": started_at,
                "finished_at": datetime.now(UTC).isoformat(),
                "database": "bounded_funds@127.0.0.1:54329",
                "before_revision": BEFORE_REVISION,
                "after_revision": AFTER_REVISION,
                "baseline_file": BASELINE_PATH,
                "baseline_file_sha256": PINS[BASELINE_PATH],
                "baseline_actual_read_at": baseline["read_at"],
                "source_pins": PINS,
                "tool_sha256": tool_hash,
                "before_data_sha256": before["data_sha256"],
                "after_data_sha256": after["data_sha256"],
                "before_table_counts": before["table_counts"],
                "after_table_counts": after["table_counts"],
                "snapshot_before_and_after_rr_read_only": True,
                "snapshot_files_sha256": {
                    name: sha((evidence_dir / name).read_bytes())
                    for name in ("before.json", "after.json")
                },
            }
        )
        write_json(evidence_dir / "compare.json", comparison)
        print(
            json.dumps(
                {
                    "status": comparison["status"],
                    "audit_status": comparison["audit_status"],
                    "evidence_directory": str(evidence_dir.relative_to(ROOT)),
                    "original_20_data_sha256": ORIGINAL_20_DATA_SHA,
                },
                indent=2,
            )
        )
        return 0
    except Exception as error:
        # Preserve the actual completed phase; do not undo DDL or claim a failed comparison passed.
        failure = {
            "status": "FAILED",
            "stage": stage,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "error_type": type(error).__name__,
            "reason": str(error) if isinstance(error, PreservationError) else "Database/tool error",
            "source_pins": PINS,
            "tool_sha256": tool_hash,
        }
        write_json(evidence_dir / "failed.json", failure)
        print(json.dumps(failure, indent=2), file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
