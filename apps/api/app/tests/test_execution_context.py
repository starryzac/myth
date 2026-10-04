"""Real PostgreSQL adapters preserve original authority and exclude only their own claims."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.db.models import ActionPlan, AssetPosition, AssetProduct, Policy
from app.domain.asset_allocation_types import PlannedExit
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionEffect
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution_context import load_execution_context
from app.services.recovery import preview_recovery
from app.tests.test_boundary_service import seed_legacy_income_fixture
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_recovery_service import recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def fixture_exit(product: AssetProduct) -> PlannedExit:
    from app.domain.policy_configuration import configuration_hash

    return PlannedExit(
        kind="PLANNED_REDEMPTION",
        request_at=SEED_AS_OF + timedelta(days=90 - product.redemption_delay_days),
        principal_available_at=SEED_AS_OF + timedelta(days=90),
        earning_days=90 - product.redemption_delay_days,
        liquidity_days=product.lock_days + product.redemption_delay_days,
        terms_digest=configuration_hash(product.maturity_rule),
    )


def redemption_effect(session: Session) -> ExecutionEffect:
    action = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF).plan.steps[0]
    quote = action.quote
    return ExecutionEffect(
        operation_id=quote.quote_id,
        user_id=DEMO_USER_ID,
        business_key=f"redeem:{action.position_id}",
        action_type="REDEEM_ASSET",
        amount_cents=quote.principal_cents,
        destination_account_id=action.destination_account_id,
        goal_id=action.goal_id,
        policy_id=action.current_policy_id,
        policy_version_id=action.current_policy_version_id,
        policy_version_ids=[action.current_policy_version_id],
        product_id=action.product_id,
        product_version_number=action.product_version_number,
        terms_digest=action.terms_digest,
        position_id=action.position_id,
        position_account_id=action.source_account_id,
        original_policy_version_id=action.original_policy_version_id,
        quote_id=quote.quote_id,
        net_cents=quote.net_cents,
        fee_cents=quote.fee_cents,
        loss_cents=quote.loss_cents,
        settlement_delay_days=(quote.principal_available_at - quote.request_at).days,
        latest_arrival_at=quote.expires_at + (quote.principal_available_at - quote.request_at),
        valid_from=SEED_AS_OF,
        expires_at=quote.expires_at,
    )


def test_context_keeps_original_quote_and_authority_as_execution_clock_advances(
    demo_engine: Engine,
) -> None:
    seed_legacy_income_fixture(demo_engine)
    recovery_fixture(demo_engine)
    before = database_snapshot(demo_engine)
    with Session(demo_engine) as session:
        effect = redemption_effect(session)
        loaded = load_execution_context(
            session, DEMO_USER_ID, effect, SEED_AS_OF + timedelta(minutes=2)
        )
        assert loaded.source_issues == []
        assert loaded.redemption_quote is not None
        assert loaded.redemption_quote.quote_id == effect.quote_id
        assert loaded.redemption_quote.request_at == SEED_AS_OF
        assert any(v.version_id == effect.policy_version_id for v in loaded.versions)
        assert revalidate_execution(effect, loaded).status == "READY"
    assert database_snapshot(demo_engine) == before


def test_another_action_cannot_be_named_as_self_to_hide_its_resource_claims(
    demo_engine: Engine,
) -> None:
    seed_legacy_income_fixture(demo_engine)
    recovery_fixture(demo_engine)
    with Session(demo_engine) as session:
        effect = redemption_effect(session)
        other = session.scalars(select(ActionPlan)).first()
        assert other is not None
        with pytest.raises(ValueError):
            load_execution_context(
                session, DEMO_USER_ID, effect, SEED_AS_OF, own_action_id=other.id
            )


def test_current_authorization_cannot_adopt_a_historical_manual_position(
    demo_engine: Engine,
) -> None:
    seed_legacy_income_fixture(demo_engine)
    recovery_fixture(demo_engine)
    with Session(demo_engine) as session:
        effect = redemption_effect(session)
        manual = session.scalars(
            select(AssetPosition).where(AssetPosition.policy_version_id.is_(None))
        ).first()
        assert manual is not None
        product = session.get(AssetProduct, manual.product_id)
        assert product is not None
        from app.domain.policy_configuration import configuration_hash

        rebound = effect.model_copy(
            update={
                "position_id": manual.id,
                "position_account_id": manual.account_id,
                "product_id": manual.product_id,
                "product_version_number": product.version_number,
                "terms_digest": configuration_hash(product.maturity_rule),
                "amount_cents": manual.principal_cents,
                "net_cents": manual.principal_cents,
                "quote_id": uuid4(),
            }
        )
        loaded = load_execution_context(session, DEMO_USER_ID, rebound, SEED_AS_OF)
        assert "INVALID_ORIGINAL_REDEMPTION_SOURCE" in {
            issue.code for issue in loaded.source_issues
        }
        assert revalidate_execution(rebound, loaded).status == "INSUFFICIENT_EVIDENCE"


def test_revoked_current_policy_closes_prepared_redemption(demo_engine: Engine) -> None:
    seed_legacy_income_fixture(demo_engine)
    recovery_fixture(demo_engine)
    with Session(demo_engine) as session, session.begin():
        effect = redemption_effect(session)
        current = session.get(Policy, effect.policy_id)
        assert current is not None
        current.status = "REVOKED"
        session.flush()
        loaded = load_execution_context(session, DEMO_USER_ID, effect, SEED_AS_OF)
        assert "INVALID_EXECUTION_AUTHORITY" in {issue.code for issue in loaded.source_issues}
        assert revalidate_execution(effect, loaded).status == "INSUFFICIENT_EVIDENCE"


def test_context_excludes_only_own_reserved_purchase_without_double_counting_exposure(
    demo_engine: Engine,
) -> None:
    from app.db.models import Account
    from app.domain.execution import execution_effect_hash
    from app.domain.execution_types import BankCommand, CashUse
    from app.domain.policy_configuration import configuration_hash
    from app.services.execution_exposure import refresh_execution_exposure
    from app.services.execution_reservations import ResourceClaim, reserve_resources
    from app.tests.test_asset_allocation_service import authorization, purchase_action
    from app.tests.test_boundary_service import confirmed_policy

    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, authorization())
        source = session.scalars(select(Account).where(Account.account_type == "CASH")).first()
        asset = session.scalars(
            select(Account).where(Account.account_type == "CASH_MANAGEMENT")
        ).first()
        assert source is not None and asset is not None
        source_id = source.id
        commands = []
        for amount in (50000, 20000):
            action = purchase_action(session, version_id, amount)
            product = session.get(AssetProduct, action.product_id)
            assert product is not None
            effect = ExecutionEffect(
                operation_id=action.id,
                user_id=DEMO_USER_ID,
                business_key="purchase:" + str(action.id),
                action_type="PURCHASE_ASSET",
                amount_cents=amount,
                cash_uses=[CashUse(account_id=source.id, amount_cents=amount)],
                policy_id=policy_id,
                policy_version_id=version_id,
                policy_version_ids=[version_id],
                product_id=product.id,
                product_version_number=product.version_number,
                terms_digest=configuration_hash(product.maturity_rule),
                position_id=uuid4(),
                position_account_id=asset.id,
                return_account_id=source.id,
                purchase_exit=fixture_exit(product),
                latest_arrival_at=SEED_AS_OF + timedelta(days=90, minutes=15),
                valid_from=SEED_AS_OF,
                expires_at=SEED_AS_OF + timedelta(minutes=15),
            )
            action.request = {
                "execution": BankCommand(
                    effect=effect, effect_hash=execution_effect_hash(effect)
                ).model_dump(mode="json")
            }
            action.request_hash = configuration_hash(action.request)
            action.status = "SUBMITTED"
            reserve_resources(
                session,
                DEMO_USER_ID,
                action.id,
                [
                    ResourceClaim(
                        resource_kind="CASH",
                        resource_key=str(source.id),
                        amount_cents=amount,
                        capacity_cents=source.balance_cents,
                    ),
                    ResourceClaim(
                        resource_kind="BUSINESS",
                        resource_key=effect.business_key,
                        amount_cents=1,
                        capacity_cents=1,
                    ),
                ],
                SEED_AS_OF,
            )
            commands.append(effect)
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, uuid4())
    before = database_snapshot(demo_engine)
    with Session(demo_engine) as session:
        all_reserved = load_execution_context(session, DEMO_USER_ID, commands[0], SEED_AS_OF)
        assert all_reserved.source_issues == []
        assert all_reserved.reserved_cash_by_account == {source_id: 70000}
        own_excluded = load_execution_context(
            session, DEMO_USER_ID, commands[0], SEED_AS_OF, own_action_id=commands[0].operation_id
        )
        assert own_excluded.source_issues == []
        assert own_excluded.reserved_cash_by_account == {source_id: 20000}
        assert own_excluded.exposure is not None
        assert own_excluded.exposure.pending_purchase_cents == 20000
        assert own_excluded.exposure.reserved_cash_by_account == {source_id: 20000}
        assert own_excluded.exposure.counted_action_ids == [commands[1].operation_id]
        assert revalidate_execution(commands[0], own_excluded).status == "READY"
    assert database_snapshot(demo_engine) == before


@pytest.mark.parametrize("valid", [True, False])
def test_imported_legacy_income_reservation_remains_cash_unavailable(
    demo_engine: Engine, valid: bool
) -> None:
    from app.db.models import Account, EvidenceItem, Transaction
    from app.domain.execution_types import CashUse
    from app.domain.policy_configuration import configuration_hash
    from app.tests.test_asset_allocation_service import authorization
    from app.tests.test_boundary_service import confirmed_policy

    seed_legacy_income_fixture(demo_engine)
    with Session(demo_engine) as session, session.begin():
        cash = session.scalars(select(Account).where(Account.account_type == "CASH")).one()
        asset = session.scalars(
            select(Account).where(Account.account_type == "CASH_MANAGEMENT")
        ).one()
        product = session.scalars(
            select(AssetProduct).where(
                AssetProduct.asset_class == "CASH_MGMT_T0", AssetProduct.version_number == 2
            )
        ).one()
        policy_id, version_id = confirmed_policy(session, authorization())
        cash_id = cash.id
        lots: list[dict[str, Any]] = []
        for transaction in session.scalars(
            select(Transaction)
            .where(Transaction.account_id == cash.id)
            .order_by(Transaction.occurred_at)
        ):
            bank = session.get(EvidenceItem, transaction.evidence_id)
            if bank is None or bank.content.get("economic_role") != "INCOME":
                continue
            reserved = 30000 if not lots else 0
            lots.append(
                {
                    "transaction_id": str(transaction.id),
                    "account_id": str(cash.id),
                    "bank_evidence_id": str(bank.id),
                    "bank_evidence_hash": bank.content_hash,
                    "original_cents": transaction.amount_cents,
                    "prior_unspent_cents": reserved,
                    "spent_cents": transaction.amount_cents - reserved,
                    "assigned_cents": 0,
                    "reserved_cents": reserved,
                    "available_cents": 0,
                }
            )
        assert lots
        payload = {
            "simulation": True,
            "protocol": "new-funds-ledger-v1",
            "user_id": str(DEMO_USER_ID),
            "complete": True,
            "as_of": SEED_AS_OF.isoformat(),
            "scope_account_ids": [str(cash.id)],
            "lots": lots,
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_NEW_FUNDS_LEDGER",
                source_ref="legacy-fixture",
                content=payload,
                content_hash=configuration_hash(payload) if valid else "0" * 64,
                observed_at=SEED_AS_OF,
                valid_from=SEED_AS_OF,
                status="VALID",
            )
        )
        effect = ExecutionEffect(
            operation_id=uuid4(),
            user_id=DEMO_USER_ID,
            business_key="legacy-cash-use",
            action_type="PURCHASE_ASSET",
            amount_cents=50000,
            cash_uses=[CashUse(account_id=cash.id, amount_cents=50000)],
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            product_id=product.id,
            product_version_number=product.version_number,
            terms_digest=configuration_hash(product.maturity_rule),
            position_id=uuid4(),
            position_account_id=asset.id,
            return_account_id=cash.id,
            purchase_exit=fixture_exit(product),
            latest_arrival_at=SEED_AS_OF + timedelta(days=90, minutes=15),
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
    before = database_snapshot(demo_engine)
    with Session(demo_engine) as session:
        state = load_execution_context(session, DEMO_USER_ID, effect, SEED_AS_OF)
        if valid:
            assert state.source_issues == []
            assert state.reserved_cash_by_account.get(cash_id) == 30000
            assert sum(lot.available_cents for lot in state.lots) == 0
            assert revalidate_execution(effect, state).status == "READY"
        else:
            assert state.source_issues
            assert revalidate_execution(effect, state).status == "INSUFFICIENT_EVIDENCE"
    assert database_snapshot(demo_engine) == before


@pytest.mark.parametrize(
    "change",
    [
        {"allow_auto_recovery_without_penalty": False},
        {"single_action_cap_cents": 100000},
        {"allowed_asset_classes": ["CASH"]},
        {"max_redemption_delay_days": 0},
    ],
)
def test_current_recovery_permission_restrictions_cannot_be_ignored(
    demo_engine: Engine, change: dict[str, Any]
) -> None:
    from app.domain.policy_configuration import configuration_hash, validate_configuration
    from app.services.policy_lifecycle import change_policy
    from app.tests.test_asset_allocation_service import authorization

    seed_legacy_income_fixture(demo_engine)
    recovery_fixture(demo_engine, delay=1)
    with Session(demo_engine) as session, session.begin():
        effect = redemption_effect(session)
        assert effect.policy_id is not None and effect.policy_version_id is not None
        config = validate_configuration(authorization(**change))
        changed = change_policy(
            session,
            DEMO_USER_ID,
            effect.policy_id,
            effect.policy_version_id,
            config,
            configuration_hash(config),
            True,
            "narrow authority",
            str(uuid4()),
            SEED_AS_OF,
        )
        effect = effect.model_copy(
            update={
                "policy_version_id": changed.current_version_id,
                "policy_version_ids": [changed.current_version_id],
            }
        )
    with Session(demo_engine) as session:
        loaded = load_execution_context(session, DEMO_USER_ID, effect, SEED_AS_OF)
        assert "EXECUTION_REDEMPTION_PERMISSION_DENIED" in {
            issue.code for issue in loaded.source_issues
        }
        assert revalidate_execution(effect, loaded).status == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("kind,auto", [("exact", False), ("range", True), ("exact", True)])
def test_payment_confirmation_requirement_comes_from_current_confirmed_policy(
    demo_engine: Engine, kind: str, auto: bool
) -> None:
    from app.db.models import Account
    from app.domain.execution_types import CashUse, OccurrenceReference
    from app.tests.test_boundary_service import confirmed_policy

    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        cash = session.scalars(select(Account).where(Account.account_type == "CASH")).one()
        rule = (
            {"kind": "exact", "amount_cents": 30000}
            if kind == "exact"
            else {"kind": "range", "min_cents": 20000, "max_cents": 40000}
        )
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "landlord",
                "amount_rule": rule,
                "due_day": 4,
                "auto_execute": auto,
            },
        )
        effect = ExecutionEffect(
            operation_id=uuid4(),
            user_id=DEMO_USER_ID,
            business_key="payment:2026-10",
            action_type="PAY_RECURRING",
            amount_cents=30000,
            cash_uses=[CashUse(account_id=cash.id, amount_cents=30000)],
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            liability=OccurrenceReference(policy_id=policy_id, period="2026-10", evidence_ids=[]),
            payee_id="landlord",
            payee_evidence_id=uuid4(),
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
    with Session(demo_engine) as session:
        loaded = load_execution_context(session, DEMO_USER_ID, effect, SEED_AS_OF)
        assert loaded.source_issues == []
        assert loaded.requires_confirmation is (kind == "range" or not auto)
        if kind == "exact":
            assert revalidate_execution(effect, loaded).status == (
                "READY" if auto else "CONFIRMATION_REQUIRED"
            )


@pytest.mark.parametrize("denied", ["original", "current", "neither"])
def test_cost_confirmation_does_not_replace_both_historical_and_current_permissions(
    demo_engine: Engine, denied: str
) -> None:
    from app.db.models import EvidenceItem
    from app.domain.asset_exposure import EXPOSURE_SOURCE
    from app.domain.execution import execution_effect_hash
    from app.domain.execution_types import ConfirmationGrant
    from app.domain.policy_configuration import configuration_hash, validate_configuration
    from app.services.policy_lifecycle import change_policy
    from app.tests.test_asset_allocation_service import authorization, exposure_statement
    from app.tests.test_boundary_service import confirmed_policy

    seed_legacy_income_fixture(demo_engine)
    recovery_fixture(demo_engine)
    with Session(demo_engine) as session, session.begin():
        effect = redemption_effect(session)
        quote = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF).plan.steps[0].quote
        position = session.get(AssetPosition, effect.position_id)
        assert position is not None
        original_config = authorization(allow_early_withdrawal_with_penalty=denied != "original")
        policy_id, original_id = confirmed_policy(
            session, original_config, position.purchased_at - timedelta(days=1)
        )
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
            )
        ).one()
        action = session.get(ActionPlan, proof.content["purchase_action_id"])
        assert action is not None
        action.policy_version_id = position.policy_version_id = original_id
        proof.content = {**proof.content, "policy_version_id": str(original_id)}
        proof.content_hash = configuration_hash(proof.content)
        current_config = validate_configuration(
            authorization(allow_early_withdrawal_with_penalty=denied != "current")
        )
        changed = change_policy(
            session,
            DEMO_USER_ID,
            policy_id,
            original_id,
            current_config,
            configuration_hash(current_config),
            True,
            "new authority",
            str(uuid4()),
            SEED_AS_OF,
        )
        quote = quote.model_copy(
            update={"fee_cents": 100, "net_cents": quote.principal_cents - 100}
        )
        payload = {
            "simulation": True,
            "protocol": "recovery-quote-v1",
            "user_id": str(DEMO_USER_ID),
            "position_id": str(position.id),
            "destination_account_id": str(effect.destination_account_id),
            "goal_id": None,
            "quote": quote.model_dump(mode="json"),
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_REDEMPTION_QUOTE",
                source_ref="known-cost",
                content=payload,
                content_hash=configuration_hash(payload),
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
                status="VALID",
            )
        )
        effect = effect.model_copy(
            update={
                "policy_id": policy_id,
                "policy_version_id": changed.current_version_id,
                "policy_version_ids": [changed.current_version_id],
                "original_policy_version_id": original_id,
                "fee_cents": 100,
                "net_cents": quote.net_cents,
            }
        )
        exposure = session.scalars(
            select(EvidenceItem).where(EvidenceItem.source_type == EXPOSURE_SOURCE)
        ).one()
        exposure_statement(session, exposure.content["settlements"])
    with Session(demo_engine) as session:
        loaded = load_execution_context(session, DEMO_USER_ID, effect, SEED_AS_OF)
        confirmed = ConfirmationGrant(
            user_id=DEMO_USER_ID,
            operation_id=effect.operation_id,
            effect_hash=execution_effect_hash(effect),
            evidence_id=uuid4(),
            confirmed_at=SEED_AS_OF,
            expires_at=effect.expires_at,
        )
        if denied != "neither":
            assert "EXECUTION_REDEMPTION_PERMISSION_DENIED" in {
                issue.code for issue in loaded.source_issues
            }
            assert (
                revalidate_execution(effect, loaded, confirmation=confirmed).status
                == "INSUFFICIENT_EVIDENCE"
            )
        else:
            assert loaded.source_issues == []
            assert revalidate_execution(effect, loaded).status == "CONFIRMATION_REQUIRED"
            assert revalidate_execution(effect, loaded, confirmation=confirmed).status == "READY"


def test_zero_cost_quote_does_not_override_original_product_automatic_exit_flag(
    demo_engine: Engine,
) -> None:
    seed_legacy_income_fixture(demo_engine)
    recovery_fixture(demo_engine)
    with Session(demo_engine) as session, session.begin():
        effect = redemption_effect(session)
        product = session.get(AssetProduct, effect.product_id)
        assert product is not None
        product.auto_redeem_allowed = False
    with Session(demo_engine) as session:
        loaded = load_execution_context(session, DEMO_USER_ID, effect, SEED_AS_OF)
        assert "EXECUTION_REDEMPTION_PERMISSION_DENIED" in {
            issue.code for issue in loaded.source_issues
        }
        assert revalidate_execution(effect, loaded).status == "INSUFFICIENT_EVIDENCE"
