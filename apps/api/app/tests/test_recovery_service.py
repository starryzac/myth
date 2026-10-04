"""Recovery orchestration against actual isolated PostgreSQL."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    AssetProduct,
    EvidenceItem,
    Policy,
    PolicyVersion,
    Transaction,
)
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.policy_lifecycle import change_policy, revoke_policy
from app.services.recovery import get_recovery_run, preview_recovery, run_recovery
from app.tests.test_asset_allocation_service import (
    automatic_position,
    exposure_statement,
    purchase_action,
)
from app.tests.test_boundary_service import boundary_engine, confirmed_policy, snapshot
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def test_seed_preview_is_read_only_and_never_adopts_manual_assets(boundary_engine: Engine) -> None:
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        assert result.plan.status == "NO_RECOVERY_NEEDED"
        assert result.plan.steps == []
    assert snapshot(boundary_engine) == before


def test_t1_get_does_not_settle_and_same_post_advances_after_revocation(
    boundary_engine: Engine,
) -> None:
    policy_id, position_id, principal = recovery_fixture(boundary_engine, delay=1)
    first = run_recovery(boundary_engine, DEMO_USER_ID, "t1-recovery", SEED_AS_OF)
    assert first.status == "PENDING_SETTLEMENT"
    assert first.actual_boundary.minimum_margin_cents == -30000
    with Session(boundary_engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.status == "REDEEMING"
        version = session.scalar(select(PolicyVersion).where(PolicyVersion.policy_id == policy_id))
        assert version is not None
        revoke_policy(
            session, DEMO_USER_ID, policy_id, version.id, now=SEED_AS_OF + timedelta(minutes=1)
        )
    later = SEED_AS_OF + timedelta(days=1)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        read = get_recovery_run(session, DEMO_USER_ID, first.run_id, later)
        assert read.status == "PENDING_SETTLEMENT"
        assert read.actions[0].receipt_id is None
    assert snapshot(boundary_engine) == before
    settled = run_recovery(boundary_engine, DEMO_USER_ID, "t1-recovery", later)
    assert settled.status == "RECOVERED"
    assert settled.actual_boundary.minimum_margin_cents == principal - 30000
    with Session(boundary_engine) as session:
        preview = preview_recovery(session, DEMO_USER_ID, later)
        assert preview.source_issues == []
        assert preview.plan.status == "NO_RECOVERY_NEEDED"
        assert preview.plan.steps == []


def test_loss_quote_is_saved_as_ask_once_without_any_bank_request(boundary_engine: Engine) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        preview = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        action = preview.plan.steps[0]
        quote = action.quote.model_copy(update={"fee_cents": 100, "net_cents": principal - 100})
        payload = {
            "simulation": True,
            "protocol": "recovery-quote-v1",
            "user_id": str(DEMO_USER_ID),
            "position_id": str(position_id),
            "destination_account_id": str(action.destination_account_id),
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
                source_ref="loss-quote",
                content=payload,
                content_hash=configuration_hash(payload),
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
                status="VALID",
            )
        )
    result = run_recovery(boundary_engine, DEMO_USER_ID, "loss-proposal", SEED_AS_OF)
    assert result.status == "ASK_ONCE" and result.actions == []
    proposal = next(item.action for item in result.plan.candidates if item.decision == "ASK_ONCE")
    assert proposal is not None and proposal.quote.fee_cents == 100
    assert proposal.request_hash == configuration_hash(
        proposal.model_dump(mode="json", exclude={"request_hash"})
    )
    with Session(boundary_engine) as session:
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.status == "HELD"


def recovery_fixture(engine: Engine, *, delay: int = 0) -> tuple[UUID, UUID, int]:
    with Session(engine) as session, session.begin():
        policy_id, _, declaration = automatic_position(session)
        position = session.get(AssetPosition, UUID(declaration["position_id"]))
        assert position is not None
        product = session.scalar(
            select(AssetProduct).where(
                AssetProduct.asset_class == ("CASH_MGMT_T1" if delay else "CASH_MGMT_T0"),
                AssetProduct.version_number == 2,
            )
        )
        assert product is not None
        # This fixture represents a proven purchase after these exact versioned terms existed.
        product = AssetProduct(
            **{
                column.name: getattr(product, column.name)
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
        action = session.get(ActionPlan, UUID(declaration["action_id"]))
        assert action is not None
        action.product_id = product.id
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
            )
        )
        assert proof is not None
        proof.content = {**proof.content, "product_id": str(product.id)}
        proof.content_hash = configuration_hash(proof.content)
        confirmed_policy(session, {"type": "emergency_buffer", "amount_cents": 3187400})
        exposure_statement(session, [declaration])
        return policy_id, position.id, position.principal_cents


def mature_recovery_fixture(engine: Engine) -> tuple[UUID, UUID, int]:
    policy_id, position_id, principal = recovery_fixture(engine)
    with Session(engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None
        template = session.scalar(
            select(AssetProduct).where(
                AssetProduct.asset_class == "FIXED_DEPOSIT", AssetProduct.version_number == 2
            )
        )
        assert template is not None
        maturity = SEED_AS_OF - timedelta(days=1)
        position.purchased_at = maturity - timedelta(days=30)
        position.maturity_at = position.available_at = maturity
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
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
            )
        )
        assert proof is not None
        action = session.get(ActionPlan, UUID(proof.content["purchase_action_id"]))
        assert action is not None
        action.product_id = product.id
        action.created_at = action.authorized_at = position.purchased_at
        proof.content = {
            **proof.content,
            "product_id": str(product.id),
            "purchased_at": position.purchased_at.isoformat(),
            "maturity_at": maturity.isoformat(),
            "available_at": maturity.isoformat(),
        }
        proof.content_hash = configuration_hash(proof.content)
        transaction = session.get(Transaction, UUID(proof.content["purchase_transaction_id"]))
        assert transaction is not None
        transaction.occurred_at = position.purchased_at
        transaction_proof = session.get(EvidenceItem, transaction.evidence_id)
        assert transaction_proof is not None
        transaction_proof.content = {
            **transaction_proof.content,
            "occurred_at": position.purchased_at.isoformat(),
        }
        transaction_proof.content_hash = configuration_hash(transaction_proof.content)
        version = session.scalar(select(PolicyVersion).where(PolicyVersion.policy_id == policy_id))
        assert version is not None
        revoke_policy(session, DEMO_USER_ID, policy_id, version.id, now=SEED_AS_OF)
        old_exposure = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE")
        )
        assert old_exposure is not None
        exposure_statement(session, old_exposure.content["settlements"])
    return policy_id, position_id, principal


def test_mature_contract_returns_once_even_if_authorization_was_revoked(
    boundary_engine: Engine,
) -> None:
    _, _, principal = mature_recovery_fixture(boundary_engine)
    result = run_recovery(boundary_engine, DEMO_USER_ID, "mature-settlement", SEED_AS_OF)
    assert result.status == "RECOVERED"
    assert result.actual_boundary.minimum_margin_cents == principal - 30000
    assert len(result.actions) == 1 and result.actions[0].receipt_id is not None
    before = snapshot(boundary_engine)
    run_recovery(boundary_engine, DEMO_USER_ID, "mature-settlement", SEED_AS_OF)
    assert snapshot(boundary_engine) == before


def test_t0_recovery_commits_bank_cash_receipt_and_local_notification(
    boundary_engine: Engine,
) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    with Session(boundary_engine) as session:
        preview = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        assert preview.plan.actual_boundary.minimum_margin_cents == -30000
        assert len(preview.plan.steps) == 1
    result = run_recovery(boundary_engine, DEMO_USER_ID, "t0-recovery", SEED_AS_OF)
    assert result.status == "RECOVERED"
    assert result.actual_boundary.minimum_margin_cents == principal - 30000
    assert len(result.actions) == 1 and result.actions[0].receipt_id is not None
    assert any(item.code == "PRINCIPAL_RECEIVED" for item in result.notifications)
    with Session(boundary_engine) as session:
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.status == "REDEEMED"
        receipt = session.scalar(
            select(ActionReceipt).where(ActionReceipt.id == result.actions[0].receipt_id)
        )
        assert receipt is not None and receipt.executed_cents == principal
        transaction = session.scalar(
            select(Transaction).where(Transaction.category == "principal_return")
        )
        assert transaction is not None and transaction.amount_cents == principal
        proof = session.get(EvidenceItem, transaction.evidence_id)
        assert proof is not None and proof.content["economic_role"] == "PRINCIPAL_RETURN"
    before = snapshot(boundary_engine)
    again = run_recovery(boundary_engine, DEMO_USER_ID, "t0-recovery", SEED_AS_OF)
    assert again.run_id == result.run_id
    assert snapshot(boundary_engine) == before


def multiple_recovery_fixture(engine: Engine, second_delay: int = 0) -> tuple[UUID, int, int]:
    policy_id, _, first_principal = recovery_fixture(engine)
    with Session(engine) as session, session.begin():
        version = session.scalar(select(PolicyVersion).where(PolicyVersion.policy_id == policy_id))
        assert version is not None
        position = session.scalar(
            select(AssetPosition)
            .join(AssetProduct)
            .where(AssetProduct.asset_class == "CASH_MGMT_T1")
        )
        assert position is not None
        principal = position.principal_cents
        template = session.scalar(
            select(AssetProduct).where(
                AssetProduct.version_number == 2,
                AssetProduct.asset_class == ("CASH_MGMT_T1" if second_delay else "CASH_MGMT_T0"),
            )
        )
        assert template is not None
        product = AssetProduct(
            **{
                column.name: getattr(template, column.name)
                for column in AssetProduct.__table__.columns
                if column.name not in {"id", "version_number", "created_at", "effective_from"}
            },
            id=uuid4(),
            version_number=4,
            created_at=position.purchased_at - timedelta(days=1),
            effective_from=position.purchased_at - timedelta(days=1),
        )
        session.add(product)
        session.flush()
        position.product_id = product.id
        position.policy_version_id = version.id
        action = purchase_action(session, version.id, principal)
        action.product_id, action.position_id, action.status = product.id, position.id, "SUCCEEDED"
        action.created_at = action.authorized_at = position.purchased_at
        receipt = ActionReceipt(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            created_at=SEED_AS_OF,
            action_plan_id=action.id,
            attempt_number=1,
            receipt_ref=str(uuid4()),
            status="SUCCEEDED",
            executed_cents=principal,
            occurred_at=position.purchased_at,
            response={},
        )
        session.add(receipt)
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
            )
        )
        assert proof is not None
        proof.content = {
            **proof.content,
            "product_id": str(product.id),
            "policy_version_id": str(version.id),
            "acquisition": "synthetic_auto_purchase",
            "purchase_action_id": str(action.id),
            "purchase_receipt_id": str(receipt.id),
        }
        proof.content_hash = configuration_hash(proof.content)
        emergency = session.scalar(
            select(PolicyVersion)
            .join(Policy, Policy.id == PolicyVersion.policy_id)
            .where(Policy.policy_type == "emergency_buffer")
        )
        assert emergency is not None
        configuration = {**emergency.configuration, "amount_cents": 3457400}
        change_policy(
            session,
            DEMO_USER_ID,
            emergency.policy_id,
            emergency.id,
            configuration,
            configuration_hash(configuration),
            True,
            "larger cash risk",
            "two-position-risk",
            SEED_AS_OF,
        )
        exposure = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE")
        )
        assert exposure is not None
        exposure_statement(
            session,
            [
                *exposure.content["settlements"],
                {
                    "action_id": str(action.id),
                    "state": "MATERIALIZED",
                    "position_id": str(position.id),
                    "receipt_id": str(receipt.id),
                    "transaction_id": proof.content["purchase_transaction_id"],
                },
            ],
        )
    return policy_id, first_principal, principal


@pytest.mark.parametrize("second_delay", [0, 1])
def test_multiple_whole_positions_follow_bank_cash_sequence_and_wait_for_t1(
    boundary_engine: Engine,
    second_delay: int,
) -> None:
    _, first_principal, principal = multiple_recovery_fixture(boundary_engine, second_delay)
    first = run_recovery(boundary_engine, DEMO_USER_ID, "two-whole-positions", SEED_AS_OF)
    assert len(first.actions) == 2
    assert first.status == ("PENDING_SETTLEMENT" if second_delay else "RECOVERED")
    if second_delay:
        assert first.actual_boundary.minimum_margin_cents == first_principal - 300000
    final = run_recovery(
        boundary_engine,
        DEMO_USER_ID,
        "two-whole-positions",
        SEED_AS_OF + timedelta(days=second_delay),
    )
    assert final.status == "RECOVERED"
    assert final.actual_boundary.minimum_margin_cents == first_principal + principal - 300000


@pytest.mark.parametrize(
    "mode",
    ["manual", "revoked", "recovery_disabled", "old_terms", "mature_missing_terms", "not_mature"],
)
def test_known_ineligible_positions_never_create_bank_effects(
    boundary_engine: Engine, mode: str
) -> None:
    if mode == "manual":
        with Session(boundary_engine) as session, session.begin():
            confirmed_policy(session, {"type": "emergency_buffer", "amount_cents": 3187400})
    else:
        fixture = (
            mature_recovery_fixture
            if mode.startswith("mature") or mode == "not_mature"
            else recovery_fixture
        )
        policy_id, position_id, _ = fixture(boundary_engine)
        with Session(boundary_engine) as session, session.begin():
            position = session.get(AssetPosition, position_id)
            version = session.scalar(
                select(PolicyVersion).where(PolicyVersion.policy_id == policy_id)
            )
            assert position is not None and version is not None
            if mode == "revoked":
                revoke_policy(session, DEMO_USER_ID, policy_id, version.id, now=SEED_AS_OF)
            elif mode == "recovery_disabled":
                configuration = {
                    **version.configuration,
                    "allow_auto_recovery_without_penalty": False,
                }
                change_policy(
                    session,
                    DEMO_USER_ID,
                    policy_id,
                    version.id,
                    configuration,
                    configuration_hash(configuration),
                    True,
                    "disable new recovery",
                    "disable-recovery",
                    SEED_AS_OF,
                )
            else:
                product = session.get(AssetProduct, position.product_id)
                assert product is not None
                if mode == "not_mature":
                    product.maturity_rule = {**product.maturity_rule, "term_days": 90}
                    position.maturity_at = position.available_at = (
                        position.purchased_at + timedelta(days=90)
                    )
                    proof = session.scalar(
                        select(EvidenceItem).where(
                            EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                            EvidenceItem.content["position_id"].as_string() == str(position.id),
                        )
                    )
                    assert proof is not None
                    proof.content = {
                        **proof.content,
                        "maturity_at": position.maturity_at.isoformat(),
                        "available_at": position.available_at.isoformat(),
                    }
                    proof.content_hash = configuration_hash(proof.content)
                    old_exposure = session.scalar(
                        select(EvidenceItem).where(
                            EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE"
                        )
                    )
                    assert old_exposure is not None
                    exposure_statement(session, old_exposure.content["settlements"])
                else:
                    product.maturity_rule = {"kind": "RETURN_TO_CASH", "auto_rollover": False}
    from app.db.models import SimulatedBankPosting, SimulatedBankRedemption

    with Session(boundary_engine) as session:
        before_bank = list(session.scalars(select(SimulatedBankPosting.id)))
    result = run_recovery(boundary_engine, DEMO_USER_ID, f"ineligible:{mode}", SEED_AS_OF)
    assert result.actions == [] and result.status != "RECOVERED"
    with Session(boundary_engine) as session:
        assert list(session.scalars(select(SimulatedBankRedemption))) == []
        assert list(session.scalars(select(SimulatedBankPosting.id))) == before_bank
