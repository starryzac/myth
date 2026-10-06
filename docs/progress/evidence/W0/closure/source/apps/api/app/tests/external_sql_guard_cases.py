"""Actual external-bank ordinary-DML guards on an existing disposable test fixture.

0007 protects the fact's original request/first key and validates the exact two
economic legs on DELETE at deferred commit. Posting UPDATE protection is inherited
from 0003, not a new 0007 trigger. This helper makes no claim that every projection
diagnostic is immutable: updated_at remains monotonic mutable, UNKNOWN may later
project, and a completed PROJECTED result is fixed by the actual 0007 contract.
Admin DDL/trigger disabling and TRUNCATE are outside these five ordinary-DML cases.
The caller owns migration and generated-database cleanup; this helper creates no DB.
"""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.models import Account, ExternalBankFact, SimulatedBankPosting
from app.db.testing import require_test_database
from app.domain.external_bank_fact import external_hash, external_json, external_text
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.external_bank_facts import ingest_external_fact
from sqlalchemy import Table, delete, inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql import Executable

ROOT = Path(__file__).resolve().parents[4]
EXPECTED_REVISION = "0007_external_bank_facts"


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def snapshot(engine: Engine) -> dict[str, Any]:
    """All actual public tables, complete columns/rows, in one RR + READ ONLY snapshot."""
    expected_database = require_test_database(engine.url.database)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            assert connection.scalar(text("SELECT current_database()")) == expected_database
            assert connection.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            assert connection.scalar(text("SHOW transaction_read_only")) == "on"
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == EXPECTED_REVISION
            )
            inspector = inspect(connection)
            names = sorted(inspector.get_table_names(schema="public"))
            quote = connection.dialect.identifier_preparer.quote
            columns = {
                name: [column["name"] for column in inspector.get_columns(name, schema="public")]
                for name in names
            }
            data = {
                name: sorted(
                    connection.scalars(text(f"SELECT to_jsonb(t) FROM public.{quote(name)} AS t")),
                    key=encode,
                )
                for name in names
            }
            assert "external_bank_facts" in data and "alembic_version" in data
            return {
                "database_name": expected_database,
                "revision": EXPECTED_REVISION,
                "isolation": "repeatable read",
                "read_only": True,
                "columns": columns,
                "data": data,
                "table_counts": {name: len(rows) for name, rows in data.items()},
                "table_sha256": {
                    name: hashlib.sha256(encode(rows).encode("utf-8")).hexdigest()
                    for name, rows in data.items()
                },
                "data_sha256": hashlib.sha256(encode(data).encode("utf-8")).hexdigest(),
            }


def coherent_semantic_change(original: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    """Recompute every JSON/text/hash binding so rejection tests immutable originals.

    A request-only malformed hash would merely exercise REQUEST_INVALID. Instead,
    both money/counterparty attempts keep all 0007 before-trigger shape/hash/binding
    checks self-consistent and must still hit ORIGINAL_IMMUTABLE for a SETTLED fact.
    The original economic legs remain intact in the transaction's other tables.
    """
    request = deepcopy(original["request"])
    request[key] = value
    request_hash = external_hash(request)
    bank = deepcopy(original["bank_result"])
    bank["request_hash"] = request_hash
    bank_hash = external_hash(bank)
    projection = deepcopy(original["projection_result"])
    projection["request_hash"] = request_hash
    projection["bank_result_hash"] = bank_hash
    return {
        key: value,
        "request": external_json(request),
        "request_canonical_text": external_text(request),
        "request_hash": request_hash,
        "bank_result": external_json(bank),
        "bank_result_canonical_text": external_text(bank),
        "bank_result_hash": bank_hash,
        "projection_result": external_json(projection),
        "projection_result_canonical_text": external_text(projection),
        "projection_result_hash": external_hash(projection),
    }


def assert_rejected_without_changes(
    engine: Engine,
    *,
    name: str,
    statement: Executable,
    expected_message: str,
    expected_phase: str,
    original_guard_revision: str,
) -> dict[str, Any]:
    require_test_database(engine.url.database)
    before = snapshot(engine)
    caught: DBAPIError | None = None
    phase = "EXECUTE"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            result = connection.execute(statement)
            assert getattr(result, "rowcount", None) == 1, "The actual target row was not changed"
            phase = "COMMIT"
            transaction.commit()  # Deferred DELETE validation must run here, not at a mock flush.
        except DBAPIError as error:
            caught = error
            transaction.rollback()
        except BaseException:
            transaction.rollback()
            raise
    after = snapshot(engine)
    assert after == before, f"{name}: all-table rows/columns/counts/SHA256 changed"
    assert caught is not None, f"{name}: database committed a forbidden original mutation"
    sqlstate = getattr(caught.orig, "sqlstate", None)
    assert sqlstate == "23514", f"{name}: unexpected SQLSTATE {sqlstate}"
    primary = getattr(getattr(caught.orig, "diag", None), "message_primary", None)
    assert primary == expected_message, f"{name}: unexpected SQL diagnostic {primary}"
    assert phase == expected_phase, f"{name}: rejected during {phase}, not {expected_phase}"
    return {
        "case": name,
        "ordinary_dml_rejected": True,
        "sqlstate": sqlstate,
        "message_primary": primary,
        "rejected_at": phase,
        "guard_original_revision": original_guard_revision,
        "all_actual_tables_equal": True,
        "before_data_sha256": before["data_sha256"],
        "after_data_sha256": after["data_sha256"],
        "table_counts": after["table_counts"],
        "table_sha256": after["table_sha256"],
    }


def run_guards(engine: Engine) -> dict[str, Any]:
    """One real salary, five direct SQL mutation attempts, no second payroll/reset."""
    require_test_database(engine.url.database)
    seed_demo(engine)
    now = SEED_AS_OF + timedelta(minutes=1)
    with engine.connect() as connection:
        account_id = connection.scalar(
            select(Account.id).where(
                Account.user_id == DEMO_USER_ID, Account.account_type == "CASH"
            )
        )
    assert isinstance(account_id, UUID)
    request = ExternalFactRequest.model_validate_json(
        json.dumps(
            {
                "user_id": str(DEMO_USER_ID),
                "account_id": str(account_id),
                "kind": "INCOME",
                "amount_cents": 600000,
                "external_ref": "sql-guard-real-salary",
                "idempotency_key": "sql-guard-first-key",
                "counterparty_ref": "payroll",
                "occurred_at": now.isoformat(),
            }
        )
    )
    salary = ingest_external_fact(engine, DEMO_USER_ID, request, now)
    assert salary.bank_status == "SETTLED" and salary.projection_status == "PROJECTED", salary
    assert len(salary.economic_posting_ids) == 2 and salary.transaction_id is not None
    with engine.connect() as connection:
        original = dict(
            connection.execute(
                select(ExternalBankFact.__table__).where(
                    ExternalBankFact.id == salary.external_fact_id
                )
            )
            .mappings()
            .one()
        )
        cash_posting_id = connection.scalar(
            select(SimulatedBankPosting.id).where(
                SimulatedBankPosting.external_fact_id == salary.external_fact_id,
                SimulatedBankPosting.ledger_dimension == "ECONOMIC",
                SimulatedBankPosting.account_id == account_id,
            )
        )
    assert isinstance(cash_posting_id, UUID) and cash_posting_id in salary.economic_posting_ids
    fact = ExternalBankFact.__table__
    posting = SimulatedBankPosting.__table__
    assert isinstance(fact, Table) and isinstance(posting, Table)
    cases: list[dict[str, Any]] = []
    for name, changes in (
        (
            "original_request_coherent_rehash",
            coherent_semantic_change(original, "counterparty_ref", "changed-counterparty"),
        ),
        (
            "original_amount_coherent_rehash",
            coherent_semantic_change(original, "amount_cents", request.amount_cents + 1),
        ),
        ("original_first_key", {"idempotency_key": "changed-original-first-key"}),
    ):
        cases.append(
            assert_rejected_without_changes(
                engine,
                name=name,
                statement=update(fact)
                .where(fact.c.id == salary.external_fact_id)
                .values(**changes),
                expected_message="EXTERNAL_FACT_ORIGINAL_IMMUTABLE",
                expected_phase="EXECUTE",
                original_guard_revision="0007_external_bank_facts",
            )
        )
    cases.append(
        assert_rejected_without_changes(
            engine,
            name="delete_actual_economic_cash_leg",
            statement=delete(posting).where(posting.c.id == cash_posting_id),
            expected_message="EXTERNAL_ECONOMIC_POSTING_SET_INVALID",
            expected_phase="COMMIT",
            original_guard_revision="0007_external_bank_facts",
        )
    )
    cases.append(
        assert_rejected_without_changes(
            engine,
            name="update_actual_economic_cash_leg",
            statement=update(posting)
            .where(posting.c.id == cash_posting_id)
            .values(
                delta_cents=posting.c.delta_cents + 1,
                balance_after_cents=posting.c.balance_after_cents + 1,
            ),
            expected_message="Bank postings are immutable economic facts",
            expected_phase="EXECUTE",
            original_guard_revision="0003_simulated_bank (retained by 0007)",
        )
    )
    return {
        "actual_salary": salary.model_dump(mode="json"),
        "bank_economic_posting_count": 2,
        "case_count": len(cases),
        "cases": cases,
        "projection_diagnostics_tested_as_economic_originals": False,
    }
