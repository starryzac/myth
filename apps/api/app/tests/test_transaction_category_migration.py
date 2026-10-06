"""Upgrade preserves every old physical row; retained new originals prevent downgrade."""

from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.db.models import Transaction
from app.domain.transaction_category import CategoryConfirmationRequest
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.transaction_category import confirm_category, review_category
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_category_protocol_upgrade_preserves_all_rows_and_refuses_history_loss(
    migrated_database: tuple[Engine, Config],
) -> None:
    import json

    engine, config = migrated_database
    command.downgrade(config, "0010_product_catalog")
    seed_demo(engine)
    before: dict[str, Any] = json.loads(physical_snapshot(engine))
    command.upgrade(config, "head")
    after = json.loads(physical_snapshot(engine))
    for name, rows in before.items():
        if name != "alembic_version":
            assert after[name] == rows, name
    assert len(after) == len(before)
    with Session(engine) as session:
        identity = session.scalar(
            select(Transaction.id).where(
                Transaction.user_id == DEMO_USER_ID, Transaction.category == "rent"
            )
        )
        assert identity is not None
        review = review_category(session, DEMO_USER_ID, identity, SEED_AS_OF)
    actual = confirm_category(
        engine,
        DEMO_USER_ID,
        identity,
        CategoryConfirmationRequest(
            category="rent",
            accepted=True,
            reviewed_transaction_hash=review.reviewed_transaction_hash,
            expected_epoch_id=review.epoch_id,
            idempotency_key="migration-category-original",
            reason="隔离真实迁移与产品调用验证，不是真人研究",
        ),
        SEED_AS_OF,
    )
    assert actual.status == "RECORDED" and actual.audit_event_hash is not None
    preserved = physical_snapshot(engine)
    with pytest.raises(DBAPIError, match="Refusing to discard retained category audit protocol"):
        command.downgrade(config, "0010_product_catalog")
    assert physical_snapshot(engine) == preserved
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0011_transaction_category_audit"
        )
