"""Four-level assessment uses real isolated PostgreSQL facts and never writes."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    AssetPosition,
    AssetProduct,
    DecisionRun,
    EvidenceItem,
    Goal,
    PolicyVersion,
)
from app.domain.autonomy_types import AutonomyFacts
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.services import autonomy as service
from app.services.action_contracts import (
    ActionIntent,
    ConfirmActionRequest,
    GoalIntent,
    PaymentIntent,
    PrepareActionRequest,
    PurchaseIntent,
    RedeemIntent,
    TransferIntent,
)
from app.services.autonomy import assess_action, assess_intent, assess_transfer_preferences
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_sources import execution_return_account
from app.services.policy_lifecycle import PolicyLifecycleError, change_policy, suspend_policy
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import confirmed_policy, imported_proof, snapshot
from app.tests.test_execution_goal_creation import public_zero_goal
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_recovery_service import recovery_fixture
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_current_safe_authorized_purchase_is_auto_and_read_only(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, _ = confirmed_policy(session, authorization())
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        response = assess_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy),
            SEED_AS_OF,
        )
        assert response.decision.level == "AUTO_EXECUTE"
        assert response.decision.financial_evaluation == "VERIFIED"
        assert response.decision.execution_eligible is True
        assert response.effect is not None and response.effect.amount_cents == 1000000
        assert response.source_evidence_ids
    assert snapshot(boundary_engine) == before


def test_explicit_transfer_is_ask_even_after_exact_confirmation(boundary_engine: Engine) -> None:
    source, target = transfer_accounts(boundary_engine)
    intent = TransferIntent(
        kind="transfer_internal",
        source_account_id=source,
        destination_account_id=target,
        amount_cents=10000,
    )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        response = assess_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert response.decision.level == "ASK_ONCE"
        assert response.decision.financial_evaluation == "VERIFIED"
        assert not response.decision.execution_eligible
    assert snapshot(boundary_engine) == before
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="autonomy-confirm", intent=intent),
        SEED_AS_OF,
    )
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        response = assess_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
        assert response.decision.level == "ASK_ONCE"
        assert response.decision.confirmation_satisfied
        assert response.decision.execution_eligible
        assert response.effect == prepared.effect
    assert snapshot(boundary_engine) == before


def test_suspended_current_formal_policy_only_allows_unvalued_policy_advice(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, version = confirmed_policy(session, authorization())
        suspend_policy(session, DEMO_USER_ID, policy, version, SEED_AS_OF)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy),
            SEED_AS_OF,
        )
        assert result.decision.level == "ADVISE_ONLY"
        assert result.decision.financial_evaluation == "NOT_EVALUATED"
        assert result.effect is None and not result.decision.execution_eligible
        assert "POLICY_SUSPENDED" in result.decision.reasons
    assert snapshot(boundary_engine) == before


def test_missing_bank_evidence_is_blocked_not_a_user_question(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, _ = confirmed_policy(session, authorization())
        row = session.scalars(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_BANK_BALANCE")
        ).first()
        assert row is not None
        row.content_hash = "f" * 64
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy),
            SEED_AS_OF,
        )
        assert result.decision.level == "BLOCKED"
        assert not result.decision.execution_eligible and result.effect is None
    assert snapshot(boundary_engine) == before


def test_existing_action_keeps_old_version_and_cannot_be_advice_or_rebound(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, version = confirmed_policy(session, authorization())
    intent = PurchaseIntent(kind="purchase_asset", policy_id=policy)
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="old-version", intent=intent),
        SEED_AS_OF,
    )
    now = SEED_AS_OF + timedelta(minutes=1)
    with Session(boundary_engine) as session, session.begin():
        config = authorization(single_action_cap_cents=900000)
        from app.domain.policy_configuration import validate_configuration

        config = validate_configuration(config)
        change_policy(
            session,
            DEMO_USER_ID,
            policy,
            version,
            config,
            configuration_hash(config),
            True,
            "changed",
            "new-version",
            now,
        )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = assess_action(session, DEMO_USER_ID, prepared.action_id, now)
        assert result.decision.level == "BLOCKED"
        assert not result.decision.execution_eligible
        assert result.effect is None or result.effect.policy_version_id == version
    assert snapshot(boundary_engine) == before


def test_finite_user_amount_worlds_are_server_built_read_only_and_order_stable(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        first = assess_transfer_preferences(
            session, DEMO_USER_ID, source, target, [10000, 20000], SEED_AS_OF
        )
        second = assess_transfer_preferences(
            session, DEMO_USER_ID, source, target, [20000, 10000], SEED_AS_OF
        )
        assert first == second
        assert first.decision.level == "ASK_ONCE"
        assert first.decision.uncertainty_status == "DIVERGENT"
        assert not first.decision.execution_eligible and first.effect is None
        assert set(first.decision.candidate_signatures) == {"10000", "20000"}
    assert snapshot(boundary_engine) == before


def test_oversized_transfer_is_a_financial_block_and_finite_disagreement_is_not_executable(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    with Session(boundary_engine) as session:
        intent = TransferIntent(
            kind="transfer_internal",
            source_account_id=source,
            destination_account_id=target,
            amount_cents=100000000,
        )
        blocked = assess_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert blocked.decision.level == "BLOCKED"
        mixed = assess_transfer_preferences(
            session, DEMO_USER_ID, source, target, [10000, 100000000], SEED_AS_OF
        )
        assert mixed.decision.level == "ASK_ONCE"
        assert not mixed.decision.execution_eligible and mixed.effect is None


def test_cross_user_or_unknown_objects_are_404_not_authority_advice(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session:
        with pytest.raises(PolicyLifecycleError) as caught:
            assess_intent(
                session,
                DEMO_USER_ID,
                PurchaseIntent(kind="purchase_asset", policy_id=uuid4()),
                SEED_AS_OF,
            )
        assert caught.value.status_code == 404
        with pytest.raises(PolicyLifecycleError) as missing:
            assess_action(session, DEMO_USER_ID, uuid4(), SEED_AS_OF)
        assert missing.value.status_code == 404


def test_policy_proof_corruption_cannot_be_presented_as_known_no_authority(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, version_id = confirmed_policy(session, authorization())
        suspend_policy(session, DEMO_USER_ID, policy, version_id, SEED_AS_OF)
        version = session.get(PolicyVersion, version_id)
        assert version is not None
        proof = session.scalars(
            select(EvidenceItem).where(EvidenceItem.source_ref == str(version_id))
        ).one()
        proof.content_hash = "0" * 64
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy),
            SEED_AS_OF,
        )
        assert result.decision.level == "BLOCKED"


@pytest.mark.parametrize(
    "options", [[True, 2], [1, 1], [1], [0, 1], list(range(1, 10)), [1, 9223372036854775808]]
)
def test_finite_options_reject_non_money_or_unbounded_sets(options: list[int]) -> None:
    with Session() as session, pytest.raises(PolicyLifecycleError) as caught:
        assess_transfer_preferences(session, DEMO_USER_ID, uuid4(), uuid4(), options, SEED_AS_OF)
    assert caught.value.code == "INVALID_USER_OPTIONS"


@pytest.mark.parametrize("known", [True, False])
def test_suspended_payment_advice_requires_real_payee_identity(
    boundary_engine: Engine, known: bool
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "synthetic-landlord-001" if known else "new-unknown-payee",
                "due_day": 4,
                "auto_execute": True,
                "amount_rule": {"kind": "exact", "amount_cents": 180000},
            },
        )
        suspend_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            PaymentIntent(kind="pay_recurring", policy_id=policy_id, period="2026-10"),
            SEED_AS_OF,
        )
        assert result.decision.level == ("ADVISE_ONLY" if known else "BLOCKED")
        assert result.effect is None and not result.decision.execution_eligible
        assert result.decision.financial_evaluation == "NOT_EVALUATED"
    assert snapshot(boundary_engine) == before


@pytest.mark.parametrize("automatic", [True, False])
def test_known_current_exact_payment_keeps_auto_and_manual_policy_distinct(
    boundary_engine: Engine, automatic: bool
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, _ = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "synthetic-landlord-001",
                "due_day": 4,
                "auto_execute": automatic,
                "amount_rule": {"kind": "exact", "amount_cents": 180000},
            },
        )
        imported_proof(
            session,
            "SIMULATED_RECURRING_SETTLEMENT",
            {
                "protocol": "recurring-settlement-v1",
                "policy_id": str(policy_id),
                "period": "2026-10",
                "paid_cents": 10000,
                "payee_id": "synthetic-landlord-001",
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        response = assess_intent(
            session,
            DEMO_USER_ID,
            PaymentIntent(kind="pay_recurring", policy_id=policy_id, period="2026-10"),
            SEED_AS_OF,
        )
        assert response.decision.level == ("AUTO_EXECUTE" if automatic else "ASK_ONCE")
        assert response.decision.execution_eligible is automatic
        assert response.effect is not None and response.effect.amount_cents == 170000
    assert snapshot(boundary_engine) == before


def test_qualified_recovery_is_not_blocked_merely_for_current_liquidity_risk(
    boundary_engine: Engine,
) -> None:
    _, position, principal = recovery_fixture(boundary_engine)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        response = assess_intent(
            session,
            DEMO_USER_ID,
            RedeemIntent(kind="redeem_asset", position_id=position),
            SEED_AS_OF,
        )
        assert response.decision.level == "AUTO_EXECUTE"
        assert response.decision.financial_evaluation == "VERIFIED"
        assert response.effect is not None and response.effect.amount_cents == principal
    assert snapshot(boundary_engine) == before


def test_known_manual_position_is_only_unvalued_advice_not_adopted_authority(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session:
        position = session.scalars(select(AssetPosition)).first()
        assert position is not None and position.policy_version_id is None
        result = assess_intent(
            session,
            DEMO_USER_ID,
            RedeemIntent(kind="redeem_asset", position_id=position.id),
            SEED_AS_OF,
        )
        assert result.decision.level == "ADVISE_ONLY"
        assert result.decision.financial_evaluation == "NOT_EVALUATED"
        assert result.effect is None and not result.decision.execution_eligible


def test_baseline_risk_outranks_suspended_policy_advice(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy, version = confirmed_policy(session, authorization())
        suspend_policy(session, DEMO_USER_ID, policy, version, SEED_AS_OF)
        confirmed_policy(session, {"type": "emergency_buffer", "amount_cents": 10000000})
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            PurchaseIntent(kind="purchase_asset", policy_id=policy),
            SEED_AS_OF,
        )
        assert result.decision.level == "BLOCKED"
        assert "BASELINE_LIQUIDITY_RISK" in result.decision.reasons


def cost_position(engine: Engine, *, permitted: bool = True, corrupt_quote: bool = False) -> UUID:
    """Trusted historical fixture; new exact fixed terms, not a v1 product mutation."""
    _, position_id, principal = recovery_fixture(engine)
    with Session(engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None
        _, version_id = confirmed_policy(
            session,
            authorization(allow_early_withdrawal_with_penalty=permitted),
            SEED_AS_OF - timedelta(days=40),
        )
        template = session.scalars(
            select(AssetProduct).where(
                AssetProduct.asset_class == "FIXED_DEPOSIT", AssetProduct.version_number == 2
            )
        ).one()
        product = AssetProduct(
            **{
                column.name: getattr(template, column.name)
                for column in AssetProduct.__table__.columns
                if column.name not in {"id", "version_number", "created_at", "effective_from"}
            },
            id=uuid4(),
            version_number=3,
            created_at=position.purchased_at - timedelta(days=1),
            effective_from=position.purchased_at - timedelta(days=1),
        )
        session.add(product)
        session.flush()
        position.product_id = product.id
        position.maturity_at = position.purchased_at + timedelta(days=30)
        assert position.maturity_at > SEED_AS_OF
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position_id),
                EvidenceItem.status == "VALID",
            )
        ).one()
        purchase = session.get(ActionPlan, UUID(proof.content["purchase_action_id"]))
        assert purchase is not None
        run = session.get(DecisionRun, purchase.decision_run_id)
        assert run is not None
        position.policy_version_id = purchase.policy_version_id = version_id
        purchase.product_id = product.id
        run.policy_version_ids = [str(version_id)]
        proof.content = {
            **proof.content,
            "policy_version_id": str(version_id),
            "product_id": str(product.id),
            "maturity_at": position.maturity_at.isoformat(),
        }
        proof.content_hash = configuration_hash(proof.content)
        quote = RecoveryQuote(
            quote_id=uuid4(),
            user_id=DEMO_USER_ID,
            position_id=position_id,
            product_id=product.id,
            product_version_number=product.version_number,
            terms_digest=configuration_hash(product.maturity_rule),
            principal_cents=principal,
            fee_cents=100,
            loss_cents=1000,
            net_cents=principal - 1100,
            request_at=SEED_AS_OF,
            principal_available_at=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
            kind="EARLY_WITHDRAW",
        )
        payload = {
            "simulation": True,
            "protocol": "recovery-quote-v1",
            "user_id": str(DEMO_USER_ID),
            "position_id": str(position_id),
            "destination_account_id": str(
                execution_return_account(session, DEMO_USER_ID, position, SEED_AS_OF)
            ),
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
                source_ref=str(quote.quote_id),
                content=payload,
                content_hash="f" * 64 if corrupt_quote else configuration_hash(payload),
                status="VALID",
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
            )
        )
        session.flush()
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, uuid4())
    return position_id


@pytest.mark.parametrize(
    "permitted,corrupt,level",
    [
        (True, False, "ASK_ONCE"),
        (False, False, "ADVISE_ONLY"),
        (True, True, "BLOCKED"),
        (False, True, "BLOCKED"),
    ],
)
def test_fixed_deposit_cost_permission_quote_and_confirmation_are_separate(
    boundary_engine: Engine, permitted: bool, corrupt: bool, level: str
) -> None:
    position = cost_position(boundary_engine, permitted=permitted, corrupt_quote=corrupt)
    intent = RedeemIntent(kind="redeem_asset", position_id=position)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        response = assess_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert response.decision.level == level
        assert not response.decision.execution_eligible
    assert snapshot(boundary_engine) == before
    if level == "ADVISE_ONLY":
        with pytest.raises(PolicyLifecycleError):
            prepare_action(
                boundary_engine,
                DEMO_USER_ID,
                PrepareActionRequest(idempotency_key="denied-cost", intent=intent),
                SEED_AS_OF,
            )
        assert snapshot(boundary_engine) == before
    if level != "ASK_ONCE":
        return
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="exact-cost", intent=intent),
        SEED_AS_OF,
    )
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        accepted = assess_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
        assert accepted.decision.level == "ASK_ONCE"
        assert accepted.decision.confirmation_satisfied and accepted.decision.execution_eligible
        assert (
            accepted.effect is not None
            and accepted.effect.fee_cents == 100
            and accepted.effect.loss_cents == 1000
        )
    assert snapshot(boundary_engine) == before


def test_finite_worlds_use_a_real_readonly_repeatable_snapshot_during_concurrent_commit(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = transfer_accounts(boundary_engine)
    original = service._facts
    seen: list[AutonomyFacts] = []
    isolation: list[tuple[str, str]] = []

    def interleaved(
        session: Session, user_id: UUID, intent: ActionIntent, now: datetime, basis: service._Basis
    ) -> AutonomyFacts:
        isolation.append(
            (
                str(session.scalar(text("SHOW transaction_isolation"))),
                str(session.scalar(text("SHOW transaction_read_only"))),
            )
        )
        result = original(session, user_id, intent, now, basis)
        seen.append(result)
        if len(seen) == 1:
            with Session(boundary_engine) as writer, writer.begin():
                account = writer.get(Account, source)
                assert account is not None
                account.balance_cents += 1
                proof = writer.scalars(
                    select(EvidenceItem).where(
                        EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                        EvidenceItem.content["account_id"].as_string() == str(source),
                        EvidenceItem.status == "VALID",
                    )
                ).one()
                proof.content = {**proof.content, "balance_cents": account.balance_cents}
                proof.content_hash = configuration_hash(proof.content)
        return result

    monkeypatch.setattr(service, "_facts", interleaved)
    with Session(boundary_engine) as session:
        # Already reading on the caller connection must not cause a silent isolation override.
        assert session.scalar(text("SELECT 1")) == 1
        result = assess_transfer_preferences(
            session, DEMO_USER_ID, source, target, [10000, 20000], SEED_AS_OF
        )
        assert result.decision.level == "ASK_ONCE"
        assert all(not fact.source_issues for fact in seen)
        assert isolation == [("repeatable read", "on"), ("repeatable read", "on")]
        assert session.in_transaction()
        caller = str(session.scalar(text("SHOW transaction_isolation")))
        assert caller == "read committed"
    monkeypatch.setattr(service, "_facts", original)
    with Session(boundary_engine) as session:
        next_result = assess_transfer_preferences(
            session, DEMO_USER_ID, source, target, [10000, 20000], SEED_AS_OF
        )
        assert next_result.decision.level == "BLOCKED"
        assert next_result.input_digest != result.input_digest


def test_finite_readonly_snapshot_can_verify_existing_automatic_acquisition(
    boundary_engine: Engine,
) -> None:
    recovery_fixture(boundary_engine)
    source, target = transfer_accounts(boundary_engine)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        normal = assess_intent(
            session,
            DEMO_USER_ID,
            TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=10000,
            ),
            SEED_AS_OF,
        )
        result = assess_transfer_preferences(
            session, DEMO_USER_ID, source, target, [10000, 20000], SEED_AS_OF
        )
        assert normal.decision.level == result.decision.level == "ASK_ONCE"
        assert normal.decision.financial_evaluation == "VERIFIED"
    assert snapshot(boundary_engine) == before


def test_zero_cost_permission_denial_never_uses_cost_financial_advice_shortcut(
    boundary_engine: Engine,
) -> None:
    policy, position, _ = recovery_fixture(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        version = session.scalars(
            select(PolicyVersion).where(PolicyVersion.policy_id == policy)
        ).one()
        from app.domain.policy_configuration import validate_configuration

        config = validate_configuration(authorization(allow_auto_recovery_without_penalty=False))
        change_policy(
            session,
            DEMO_USER_ID,
            policy,
            version.id,
            config,
            configuration_hash(config),
            True,
            "no automated recovery",
            "zero-cost-denied",
            SEED_AS_OF,
        )
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            RedeemIntent(kind="redeem_asset", position_id=position),
            SEED_AS_OF,
        )
        assert result.decision.level == "BLOCKED"  # Known risk; no financial pass is manufactured.
        assert result.decision.financial_evaluation == "NOT_EVALUATED"
        assert result.effect is None and not result.decision.execution_eligible


def test_cost_advice_keeps_original_purchase_and_future_cash_floor_as_hard_gates(
    boundary_engine: Engine,
) -> None:
    from app.domain.policy_configuration import validate_configuration
    from app.services.boundary import AVAILABILITY_SOURCE

    identity = cost_position(boundary_engine, permitted=False)
    with Session(boundary_engine) as session, session.begin():
        position = session.get(AssetPosition, identity)
        assert position is not None and position.maturity_at is not None
        position.available_at = position.maturity_at
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(identity),
                EvidenceItem.status == "VALID",
            )
        ).one()
        proof.content = {**proof.content, "available_at": position.available_at.isoformat()}
        proof.content_hash = configuration_hash(proof.content)
        imported_proof(
            session,
            AVAILABILITY_SOURCE,
            {
                "protocol": "principal-availability-v1",
                "position_id": str(identity),
                "account_id": str(position.account_id),
                "goal_id": None,
                "principal_cents": position.principal_cents,
                "available_at": position.available_at.isoformat(),
                "principal_return_bps": 10000,
                "rollover": False,
            },
        )
        version = session.scalars(
            select(PolicyVersion).where(
                PolicyVersion.configuration["type"].as_string() == "emergency_buffer"
            )
        ).one()
        config = validate_configuration(
            {"type": "emergency_buffer", "amount_cents": 3157400 + position.principal_cents - 100}
        )
        change_policy(
            session,
            DEMO_USER_ID,
            version.policy_id,
            version.id,
            config,
            configuration_hash(config),
            True,
            "future floor",
            "future-floor",
            SEED_AS_OF,
        )
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, uuid4())
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            RedeemIntent(kind="redeem_asset", position_id=identity),
            SEED_AS_OF,
        )
        assert result.decision.level == "BLOCKED"
        assert result.decision.financial_evaluation == "REJECTED"
        assert "REDEMPTION_DOES_NOT_SAFELY_IMPROVE_DEFICIT" in result.decision.reasons
    assert snapshot(boundary_engine) == before
    with Session(boundary_engine) as session, session.begin():
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(identity),
                EvidenceItem.status == "VALID",
            )
        ).one()
        proof.content_hash = "0" * 64
    with Session(boundary_engine) as session:
        result = assess_intent(
            session,
            DEMO_USER_ID,
            RedeemIntent(kind="redeem_asset", position_id=identity),
            SEED_AS_OF,
        )
        assert result.decision.level == "BLOCKED" and result.effect is None


def test_finite_rejects_dirty_caller_without_committing_it(boundary_engine: Engine) -> None:
    source, target = transfer_accounts(boundary_engine)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        row = session.get(Account, source)
        assert row is not None
        row.balance_cents += 1
        with pytest.raises(PolicyLifecycleError) as caught:
            assess_transfer_preferences(session, DEMO_USER_ID, source, target, [1, 2], SEED_AS_OF)
        assert caught.value.code == "UNCOMMITTED_ASSESSMENT_INPUT"
        assert row in session.dirty and session.in_transaction()
    assert snapshot(boundary_engine) == before


def funded_goal_asset_intent(engine: Engine) -> tuple[PurchaseIntent, UUID, UUID, UUID]:
    """Public zero-goal creation and real301 allocation, then explicit dual-policy binding."""
    goal_id, _, policy_id, version_id = public_zero_goal(engine)
    intent = GoalIntent(kind="allocate_goal", goal_id=goal_id)
    with Session(engine) as session:
        assessed = assess_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert assessed.decision.level == "AUTO_EXECUTE"
        assert assessed.decision.financial_evaluation == "VERIFIED"
        assert assessed.effect is not None and assessed.effect.amount_cents == 10000
        assert assessed.effect.policy_version_ids == [version_id]
    allocated = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="302-fund-goal", intent=intent),
        SEED_AS_OF,
    )
    done = execute_action(engine, DEMO_USER_ID, allocated.action_id, SEED_AS_OF)
    assert done.status == "SUCCEEDED"
    with Session(engine) as session, session.begin():
        asset_id, asset_version = confirmed_policy(
            session,
            authorization(
                scope="goal",
                goal_id=str(goal_id),
                max_auto_managed_cents=100000,
                single_action_cap_cents=100000,
            ),
        )
        original = session.get(PolicyVersion, version_id)
        assert original is not None
        from app.domain.policy_configuration import validate_configuration

        config = validate_configuration(
            {**original.configuration, "asset_policy_id": str(asset_id)}
        )
        changed = change_policy(
            session,
            DEMO_USER_ID,
            policy_id,
            version_id,
            config,
            configuration_hash(config),
            True,
            "bind actual goal assets",
            "302-goal-asset-link",
            SEED_AS_OF,
        )
        goal = session.get(Goal, goal_id)
        assert goal is not None and goal.allocated_cents == 10000
        assert goal.policy_version_id == changed.current_version_id
        return (
            PurchaseIntent(kind="purchase_asset", policy_id=asset_id),
            goal_id,
            changed.current_version_id,
            asset_version,
        )


def test_real_goal_allocation_then_dual_authorized_asset_purchase_is_auto(
    boundary_engine: Engine,
) -> None:
    intent, goal_id, goal_version, asset_version = funded_goal_asset_intent(boundary_engine)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        assessed = assess_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert assessed.decision.level == "AUTO_EXECUTE", assessed.decision.reasons
        assert assessed.decision.financial_evaluation == "VERIFIED"
        assert assessed.decision.execution_eligible
        assert assessed.effect is not None and assessed.effect.goal_id == goal_id
        assert assessed.effect.amount_cents == 10000
        assert set(assessed.effect.policy_version_ids) == {goal_version, asset_version}
    assert snapshot(boundary_engine) == before
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="302-goal-purchase", intent=intent),
        SEED_AS_OF,
    )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        assessed = assess_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
        assert assessed.decision.level == "AUTO_EXECUTE", assessed.decision.reasons
        assert assessed.effect == prepared.effect
    assert snapshot(boundary_engine) == before


@pytest.mark.parametrize("change", ["suspend", "revise"])
def test_goal_asset_original_action_cannot_borrow_new_or_suspended_goal_authority(
    boundary_engine: Engine, change: str
) -> None:
    intent, goal_id, goal_version, _ = funded_goal_asset_intent(boundary_engine)
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(idempotency_key="302-goal-original", intent=intent),
        SEED_AS_OF,
    )
    now = SEED_AS_OF + timedelta(minutes=1)
    with Session(boundary_engine) as session, session.begin():
        goal = session.get(Goal, goal_id)
        version = session.get(PolicyVersion, goal_version)
        assert goal is not None and version is not None
        if change == "suspend":
            suspend_policy(session, DEMO_USER_ID, goal.policy_id, goal_version, now)
        else:
            from app.domain.policy_configuration import validate_configuration

            config = validate_configuration(
                {**version.configuration, "name": "New goal authorization"}
            )
            change_policy(
                session,
                DEMO_USER_ID,
                goal.policy_id,
                goal_version,
                config,
                configuration_hash(config),
                True,
                "changed goal",
                "302-goal-changed",
                now,
            )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        assessed = assess_action(session, DEMO_USER_ID, prepared.action_id, now)
        assert assessed.decision.level == "BLOCKED"
        assert not assessed.decision.execution_eligible
        assert assessed.effect is None or goal_version in assessed.effect.policy_version_ids
        if change == "suspend":
            fresh = assess_intent(session, DEMO_USER_ID, intent, now)
            assert fresh.decision.level in {"BLOCKED", "ADVISE_ONLY"}
            assert not fresh.decision.execution_eligible
    assert snapshot(boundary_engine) == before
