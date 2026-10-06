"""Recovered compilation preserves its original anchor under a real read-only snapshot."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.db.models import PolicyProposal
from app.services import policy_compilation
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.policy_compilation import compile_candidate, revise_compilation
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_read_revised_compilation_uses_original_anchor_without_locks_or_writes(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        compiled = compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
    assert compiled.configuration is not None and compiled.proposal_id is not None
    with Session(demo_engine) as session, session.begin():
        revised = revise_compilation(
            session,
            DEMO_USER_ID,
            compiled.compilation_id,
            {**compiled.configuration, "amount_cents": 700000},
            SEED_AS_OF + timedelta(days=1),
        )
    assert revised.proposal_id is not None and revised.proposal_id != compiled.proposal_id
    before = database_snapshot(demo_engine)
    reader = getattr(policy_compilation, "read_compilation", None)
    proposal_reader = getattr(policy_compilation, "proposal_compilation_id", None)
    assert callable(reader), "Missing lock-free compilation reader"
    assert callable(proposal_reader), "Missing proven proposal compilation identity reader"
    statements: list[str] = []

    def captured_sql(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(demo_engine, "before_cursor_execute", captured_sql)
    try:
        with (
            demo_engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=connection) as session:
                assert session.execute(text("SHOW transaction_isolation")).scalar_one() == (
                    "repeatable read"
                )
                assert session.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
                later = SEED_AS_OF + timedelta(days=2)
                recovered = reader(session, DEMO_USER_ID, compiled.compilation_id, later)
                assert recovered.compilation == compiled.compilation
                assert recovered.compilation.reference_date == compiled.compilation.reference_date
                assert recovered.configuration == revised.configuration
                assert recovered.configuration_hash == revised.configuration_hash
                assert recovered.proposal_id == revised.proposal_id
                assert recovered.proposal_status == "PROPOSED"
                current = session.get(PolicyProposal, revised.proposal_id)
                original = session.get(PolicyProposal, compiled.proposal_id)
                assert current is not None and original is not None
                assert proposal_reader(session, DEMO_USER_ID, current, later) == (
                    compiled.compilation_id
                )
                assert proposal_reader(session, DEMO_USER_ID, original, later) == (
                    compiled.compilation_id
                )
                for owner, identity in (
                    (uuid4(), compiled.compilation_id),
                    (DEMO_USER_ID, uuid4()),
                ):
                    with pytest.raises(PolicyLifecycleError) as missing:
                        reader(session, owner, identity, later)
                    assert missing.value.status_code == 404
                with pytest.raises(PolicyLifecycleError) as future:
                    reader(
                        session,
                        DEMO_USER_ID,
                        compiled.compilation_id,
                        SEED_AS_OF - timedelta(seconds=1),
                    )
                assert future.value.code == "INVALID_EVIDENCE"
    finally:
        event.remove(demo_engine, "before_cursor_execute", captured_sql)
    assert statements and all("FOR UPDATE" not in sql.upper() for sql in statements)
    assert all(
        not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE"))
        for sql in statements
    )
    assert database_snapshot(demo_engine) == before
