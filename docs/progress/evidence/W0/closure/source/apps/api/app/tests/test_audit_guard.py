"""The command lifetime gate survives business commits without using a pool slot."""

from uuid import uuid4

import pytest
from app.db.audit_guard import audit_command_guard, gate_key, transaction_gate
from app.domain.demo_identity import DEMO_USER_ID
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_three_transactions_hold_one_reset_gate_with_a_one_slot_business_pool(
    demo_engine: Engine,
) -> None:
    narrow = create_engine(demo_engine.url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    try:
        with audit_command_guard(narrow, DEMO_USER_ID):
            with audit_command_guard(narrow, DEMO_USER_ID):
                for phase in range(3):
                    with Session(narrow) as session, session.begin():
                        transaction_gate(session, DEMO_USER_ID)
                        assert session.scalar(text("SELECT :phase"), {"phase": phase}) == phase
                    with demo_engine.connect() as contender:
                        assert (
                            contender.scalar(
                                text("SELECT pg_try_advisory_xact_lock(:key)"),
                                {"key": gate_key(DEMO_USER_ID)},
                            )
                            is False
                        )
                        assert (
                            contender.scalar(
                                text("SELECT pg_try_advisory_xact_lock(:key)"),
                                {"key": gate_key(uuid4())},
                            )
                            is True
                        )
        with demo_engine.connect() as contender:
            assert (
                contender.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": gate_key(DEMO_USER_ID)}
                )
                is True
            )
    finally:
        narrow.dispose()


def test_failed_command_releases_session_lock(demo_engine: Engine) -> None:
    with pytest.raises(RuntimeError, match="business failure"):
        with audit_command_guard(demo_engine, DEMO_USER_ID):
            raise RuntimeError("business failure")
    with demo_engine.connect() as contender:
        assert (
            contender.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": gate_key(DEMO_USER_ID)}
            )
            is True
        )
