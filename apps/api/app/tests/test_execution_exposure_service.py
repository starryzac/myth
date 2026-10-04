"""Execution declarations retain complete independent bank coverage."""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.db.models import AssetProduct
from app.domain.asset_allocation_types import PlannedExit
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
from app.domain.policy_configuration import configuration_hash
from app.services.asset_exposure_import import load_asset_exposure
from app.services.boundary import load_boundary_context
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution_exposure import refresh_execution_exposure
from app.tests.test_asset_allocation_service import authorization, purchase_action
from app.tests.test_boundary_service import boundary_engine, confirmed_policy
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def test_prepared_purchase_is_fully_bound_but_does_not_reserve_cash(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, authorization())
        action = purchase_action(session, version_id, amount=50000)
        product = session.get(AssetProduct, action.product_id)
        assert product is not None
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key="purchase:" + str(action.id),
            action_type="PURCHASE_ASSET",
            amount_cents=50000,
            cash_uses=[CashUse(account_id=action.source_account_id, amount_cents=50000)],
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            product_id=product.id,
            product_version_number=product.version_number,
            terms_digest=configuration_hash(product.maturity_rule),
            position_id=uuid4(),
            position_account_id=action.source_account_id,
            return_account_id=action.source_account_id,
            purchase_exit=PlannedExit(
                kind="PLANNED_REDEMPTION",
                request_at=SEED_AS_OF + timedelta(days=90 - product.redemption_delay_days),
                principal_available_at=SEED_AS_OF + timedelta(days=90),
                earning_days=90 - product.redemption_delay_days,
                liquidity_days=product.redemption_delay_days,
                terms_digest=configuration_hash(product.maturity_rule),
            ),
            latest_arrival_at=SEED_AS_OF + timedelta(days=90),
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
        action.request = {
            "execution": BankCommand(
                effect=effect, effect_hash=execution_effect_hash(effect)
            ).model_dump(mode="json")
        }
        action.request_hash = configuration_hash(action.request)
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, action.id)
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        exposure = load_asset_exposure(session, context, {"scope": "general_idle_funds"})
        assert context.sources.issues == []
        assert exposure.pending_purchase_cents == 0
        assert exposure.reserved_cash_by_account == {}
