"""Three real homepage chains on the fixed seed; no mutable final-balance fixtures."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.db.models import Account
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.action_contracts import (
    GoalIntent,
    PrepareActionRequest,
    PurchaseIntent,
    RedeemIntent,
)
from app.services.demo_seed import seed_demo
from app.services.execution import execute_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.goals import create_goal_projection
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import confirmed_policy
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

STAGES = (
    "initial",
    "goal_confirmed",
    "goal_created",
    "salary",
    "goal_allocated",
    "purchase_prepared",
    "purchased",
    "consumption",
    "redeem_prepared",
    "redeemed",
)


@dataclass
class DashboardScenario:
    engine: Engine
    now: datetime = datetime(2026, 10, 4, 1, tzinfo=UTC)
    identities: dict[str, UUID] = field(default_factory=dict)
    stage: str = "initial"

    def initialize(self) -> None:
        seed_demo(self.engine)
        with Session(self.engine) as session:
            cash = session.scalar(
                select(Account).where(
                    Account.user_id == DEMO_USER_ID,
                    Account.account_type == "CASH",
                )
            )
            assert cash is not None
            self.identities["cash"] = cash.id

    def advance(self, stage: str) -> dict[str, Any]:
        if stage != STAGES[STAGES.index(self.stage) + 1]:
            raise ValueError("The real chain must advance in its original order")
        self.now += timedelta(seconds=10)
        result: Any = None
        if stage == "goal_confirmed":
            with Session(self.engine) as session, session.begin():
                policy_id, version_id = confirmed_policy(
                    session,
                    {
                        "type": "goal_saving",
                        "name": "旅行储备",
                        "target_cents": 100_000,
                        "deadline": "2026-12-31",
                        "monthly_contribution": {
                            "min_cents": 0,
                            "target_cents": 10_000,
                            "max_cents": 10_000,
                        },
                    },
                    self.now,
                )
                self.identities.update(goal_policy=policy_id, goal_version=version_id)
        elif stage == "goal_created":
            with Session(self.engine) as session, session.begin():
                result = create_goal_projection(
                    session,
                    DEMO_USER_ID,
                    self.identities["goal_policy"],
                    self.identities["goal_version"],
                    self.identities["cash"],
                    self.now,
                )
                self.identities["goal"] = result.goal.id
                asset_policy, _ = confirmed_policy(
                    session,
                    authorization(
                        allowed_asset_classes=["CASH_MGMT_T0"],
                        max_auto_managed_cents=250_000,
                        single_action_cap_cents=250_000,
                    ),
                    self.now,
                )
                self.identities["asset_policy"] = asset_policy
        elif stage in {"salary", "consumption"}:
            result = ingest_external_fact(
                self.engine,
                DEMO_USER_ID,
                ExternalFactRequest(
                    user_id=DEMO_USER_ID,
                    idempotency_key=f"401-{stage}",
                    external_ref=f"401-{stage}",
                    kind="INCOME" if stage == "salary" else "CONSUMPTION",
                    account_id=self.identities["cash"],
                    amount_cents=200_000 if stage == "salary" else 3_132_400,
                    counterparty_ref="payroll" if stage == "salary" else "merchant",
                    occurred_at=self.now,
                ),
                self.now,
            )
            if result.bank_status != "SETTLED" or result.projection_status != "PROJECTED":
                raise AssertionError(result.model_dump(mode="json"))
            self.identities[stage] = result.external_fact_id
        elif stage in {"goal_allocated", "purchase_prepared", "redeem_prepared"}:
            intent = (
                GoalIntent(kind="allocate_goal", goal_id=self.identities["goal"])
                if stage == "goal_allocated"
                else PurchaseIntent(
                    kind="purchase_asset", policy_id=self.identities["asset_policy"]
                )
                if stage == "purchase_prepared"
                else RedeemIntent(kind="redeem_asset", position_id=self.identities["position"])
            )
            result = prepare_action(
                self.engine,
                DEMO_USER_ID,
                PrepareActionRequest(idempotency_key=f"401-{stage}", intent=intent),
                self.now,
            )
            assert result.autonomy_level == "AUTO_EXECUTE", result.prepared_validation
            self.identities[stage] = result.action_id
            if stage == "goal_allocated":
                result = execute_action(self.engine, DEMO_USER_ID, result.action_id, self.now)
                assert result.receipt is not None and result.receipt.executed_cents == 10_000
            elif stage == "purchase_prepared":
                assert result.effect.amount_cents == 250_000
                assert result.effect.position_id is not None
                self.identities["position"] = result.effect.position_id
        elif stage in {"purchased", "redeemed"}:
            original = "purchase_prepared" if stage == "purchased" else "redeem_prepared"
            result = execute_action(self.engine, DEMO_USER_ID, self.identities[original], self.now)
            assert result.receipt is not None and result.receipt.executed_cents == 250_000
            assert result.receipt.fee_cents == result.receipt.loss_cents == 0
        self.stage = stage
        return {
            "stage": stage,
            "as_of": self.now.isoformat(),
            "identities": {key: str(value) for key, value in self.identities.items()},
            "result": result.model_dump(mode="json") if result is not None else None,
        }
