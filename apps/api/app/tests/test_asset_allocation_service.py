"""Real PostgreSQL proofs for read-only asset allocation and exposure imports."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    AssetProduct,
    DecisionRun,
    EvidenceItem,
    Goal,
    PolicyVersion,
    Transaction,
)
from app.domain.asset_exposure import EXPOSURE_SOURCE, asset_exposure_snapshot
from app.domain.policy_configuration import configuration_hash
from app.services.asset_allocation import preview_asset_allocation
from app.services.asset_exposure_import import load_asset_exposure
from app.services.boundary import load_boundary_context
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    change_policy,
    revoke_policy,
    suspend_policy,
)
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import confirmed_policy, goal_fixture, snapshot
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def authorization(**changes: Any) -> dict[str, Any]:
    return {
        "type": "asset_authorization",
        "scope": "general_idle_funds",
        "allowed_asset_classes": ["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
        "max_auto_managed_cents": 1000000,
        "single_action_cap_cents": 1000000,
        "max_redemption_delay_days": 1,
        "max_lock_days": 30,
        "allow_auto_recovery_without_penalty": True,
        **changes,
    }


def asset_policy(session: Session, **changes: Any) -> UUID:
    return confirmed_policy(session, authorization(**changes))[0]


def exposure_statement(
    session: Session, settlements: list[dict[str, Any]] | None = None
) -> EvidenceItem:
    session.flush()
    proof = session.scalar(select(EvidenceItem).where(EvidenceItem.source_type == EXPOSURE_SOURCE))
    if proof is None:
        proof = EvidenceItem(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            evidence_level="BANK_CONFIRMED",
            source_type=EXPOSURE_SOURCE,
            source_ref=str(uuid4()),
            content={},
            content_hash=configuration_hash({}),
            observed_at=SEED_AS_OF,
            valid_from=SEED_AS_OF,
            status="VALID",
        )
        session.add(proof)
        session.flush()
    proof.content = asset_exposure_snapshot(
        DEMO_USER_ID,
        SEED_AS_OF,
        accounts=session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID)),
        positions=session.scalars(
            select(AssetPosition).where(AssetPosition.user_id == DEMO_USER_ID)
        ),
        actions=session.scalars(select(ActionPlan).where(ActionPlan.user_id == DEMO_USER_ID)),
        receipts=session.scalars(
            select(ActionReceipt).where(ActionReceipt.user_id == DEMO_USER_ID)
        ),
        evidence=session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID)),
        settlements=settlements,
    )
    proof.content_hash = configuration_hash(proof.content)
    session.flush()
    return proof


def test_complete_seed_manual_exposure_is_proved_zero_and_read_only(
    boundary_engine: Engine,
) -> None:
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        exposure = load_asset_exposure(session, context, authorization())
        assert context.sources.issues == []
        assert exposure.managed_principal_cents == exposure.pending_purchase_cents == 0
        assert len(exposure.excluded_manual_position_ids) == 3
        assert exposure.reserved_cash_by_account == {}
    assert snapshot(boundary_engine) == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "bad_hash",
        "incomplete",
        "future",
        "old_epoch",
        "omitted_position",
        "manual_unknown",
        "manual_link_missing",
        "unknown_position",
    ],
)
def test_incomplete_or_ambiguous_exposure_never_becomes_precise_zero(
    boundary_engine: Engine, fault: str
) -> None:
    with Session(boundary_engine) as session, session.begin():
        proof = exposure_statement(session)
        if fault == "missing":
            session.delete(proof)
        elif fault == "duplicate":
            session.add(
                EvidenceItem(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    evidence_level="BANK_CONFIRMED",
                    source_type=EXPOSURE_SOURCE,
                    source_ref=str(uuid4()),
                    content=proof.content,
                    content_hash=proof.content_hash,
                    observed_at=SEED_AS_OF,
                    valid_from=SEED_AS_OF,
                    status="VALID",
                )
            )
        elif fault == "future":
            proof.observed_at = SEED_AS_OF + timedelta(seconds=1)
        elif fault in {"manual_unknown", "manual_link_missing", "unknown_position"}:
            position = session.scalar(select(AssetPosition))
            assert position is not None
            bank = session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                    EvidenceItem.content["position_id"].as_string() == str(position.id),
                )
            )
            assert bank is not None
            content = dict(bank.content)
            if fault == "manual_unknown":
                content.pop("acquisition")
            elif fault == "manual_link_missing":
                content.pop("purchase_transaction_id")
            else:
                position.status = "UNKNOWN"
                content["status"] = "UNKNOWN"
            bank.content = content
            bank.content_hash = configuration_hash(content)
            exposure_statement(session)
        else:
            content = dict(proof.content)
            if fault == "incomplete":
                content["complete"] = False
            elif fault == "old_epoch":
                content["as_of"] = (SEED_AS_OF - timedelta(seconds=1)).isoformat()
            elif fault == "omitted_position":
                content["positions"] = content["positions"][:-1]
            else:
                content["user_id"] = str(uuid4())
            proof.content = content
            if fault != "bad_hash":
                proof.content_hash = configuration_hash(content)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        load_asset_exposure(session, context, authorization())
        assert context.sources.issues
    assert snapshot(boundary_engine) == before


def test_missing_exposure_does_not_invent_an_unused_management_limit(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id = asset_policy(session)
        session.execute(
            delete(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE")
        )
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        assert result.allocation.status == "INSUFFICIENT_EVIDENCE"
        assert any(issue.code == "MISSING_ASSET_EXPOSURE" for issue in result.source_issues)
    assert snapshot(boundary_engine) == before


def purchase_action(session: Session, version_id: UUID, amount: int = 70000) -> ActionPlan:
    cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
    product = session.scalar(
        select(AssetProduct).where(
            AssetProduct.asset_class == "CASH_MGMT_T0", AssetProduct.version_number == 2
        )
    )
    assert cash is not None and product is not None
    run = DecisionRun(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        created_at=SEED_AS_OF,
        idempotency_key=str(uuid4()),
        trigger_type="EXPOSURE_TEST",
        algorithm_version="fixture",
        as_of=SEED_AS_OF,
        input_snapshot={},
        snapshot_hash=configuration_hash({}),
        policy_version_ids=[str(version_id)],
    )
    session.add(run)
    session.flush()
    request = {"simulation": True, "amount_cents": amount}
    action = ActionPlan(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        created_at=SEED_AS_OF,
        decision_run_id=run.id,
        policy_version_id=version_id,
        source_account_id=cash.id,
        product_id=product.id,
        action_type="ASSET_PURCHASE",
        amount_cents=amount,
        status="AUTHORIZED",
        idempotency_key=str(uuid4()),
        request=request,
        request_hash=configuration_hash(request),
        authorized_at=SEED_AS_OF,
    )
    session.add(action)
    session.flush()
    return action


def test_pending_purchase_survives_authorization_revision_and_another_policy(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, authorization())
        action = purchase_action(session, version_id)
        statement = {"action_id": str(action.id), "state": "RESERVED_UNDEBITED"}
        config = authorization(max_auto_managed_cents=2000000)
        from app.domain.policy_configuration import validate_configuration

        change_policy(
            session,
            DEMO_USER_ID,
            policy_id,
            version_id,
            config,
            configuration_hash(validate_configuration(config)),
            True,
            "cap change",
            "cap-change",
            SEED_AS_OF,
        )
        # Lifecycle invalidates the old unsubmitted request. The importer explicitly proves
        # no effect before replacing it with another same-scope reservation.
        action.status = "AUTHORIZED"
        other_id, other_version = confirmed_policy(session, authorization())
        other = purchase_action(session, other_version, 30000)
        exposure_statement(
            session, [statement, {"action_id": str(other.id), "state": "RESERVED_UNDEBITED"}]
        )
        assert other_id != policy_id
    with Session(boundary_engine) as session:
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        exposure = load_asset_exposure(session, context, authorization())
        assert context.sources.issues == []
        assert exposure.pending_purchase_cents == 100000
        assert sum(exposure.reserved_cash_by_account.values()) == 100000
        assert len(exposure.counted_action_ids) == 2


@pytest.mark.parametrize(
    "status,receipt_status,executed",
    [
        ("SUBMITTED", None, 0),
        ("UNKNOWN", None, 0),
        ("FAILED", "UNKNOWN", 0),
        ("FAILED", "FAILED", 10000),
        ("SUCCEEDED", "SUCCEEDED", 70000),
    ],
)
def test_unreconciled_or_materialized_effect_is_not_a_fresh_cash_reservation(
    boundary_engine: Engine, status: str, receipt_status: str | None, executed: int
) -> None:
    with Session(boundary_engine) as session, session.begin():
        _, version_id = confirmed_policy(session, authorization())
        action = purchase_action(session, version_id)
        action.status = status
        if receipt_status:
            session.add(
                ActionReceipt(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    created_at=SEED_AS_OF,
                    action_plan_id=action.id,
                    attempt_number=1,
                    receipt_ref=str(uuid4()),
                    status=receipt_status,
                    executed_cents=executed,
                    occurred_at=SEED_AS_OF,
                    response={},
                )
            )
        exposure_statement(session, [{"action_id": str(action.id), "state": "RESERVED_UNDEBITED"}])
    with Session(boundary_engine) as session:
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        load_asset_exposure(session, context, authorization())
        assert any(
            item.code == "EXPOSURE_RECONCILIATION_REQUIRED" for item in context.sources.issues
        )


@pytest.mark.parametrize("operation", ["suspend", "revoke", "future"])
def test_inactive_authorization_cannot_request_new_assets(
    boundary_engine: Engine, operation: str
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(
            session,
            authorization(**({"valid_from": "2026-11-01"} if operation == "future" else {})),
        )
        if operation == "suspend":
            suspend_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
        elif operation == "revoke":
            revoke_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        assert result.allocation.status == "INACTIVE_POLICY"
        assert result.allocation.suggested_cents is None
        with pytest.raises(PolicyLifecycleError, match="资产授权不存在"):
            preview_asset_allocation(session, uuid4(), policy_id, SEED_AS_OF)
    assert snapshot(boundary_engine) == before


def automatic_position(session: Session) -> tuple[UUID, UUID, dict[str, Any]]:
    policy_id, version_id = confirmed_policy(
        session, authorization(), SEED_AS_OF - timedelta(days=40)
    )
    position = session.scalar(select(AssetPosition).order_by(AssetPosition.purchased_at))
    assert position is not None
    proof = session.scalar(
        select(EvidenceItem).where(
            EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
            EvidenceItem.content["position_id"].as_string() == str(position.id),
        )
    )
    assert proof is not None
    action = purchase_action(session, version_id, position.principal_cents)
    action.status = "SUCCEEDED"
    action.product_id = position.product_id
    action.position_id = position.id
    action.created_at = action.authorized_at = position.purchased_at
    position.policy_version_id = version_id
    receipt = ActionReceipt(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        created_at=SEED_AS_OF,
        action_plan_id=action.id,
        attempt_number=1,
        receipt_ref=str(uuid4()),
        status="SUCCEEDED",
        executed_cents=position.principal_cents,
        occurred_at=position.purchased_at,
        response={},
    )
    session.add(receipt)
    proof.content = {
        **proof.content,
        "policy_version_id": str(version_id),
        "acquisition": "synthetic_auto_purchase",
        "purchase_action_id": str(action.id),
        "purchase_receipt_id": str(receipt.id),
    }
    proof.content_hash = configuration_hash(proof.content)
    declaration = {
        "action_id": str(action.id),
        "state": "MATERIALIZED",
        "position_id": str(position.id),
        "receipt_id": str(receipt.id),
        "transaction_id": proof.content["purchase_transaction_id"],
    }
    return policy_id, version_id, declaration


def test_materialized_principal_counts_once_across_revision_and_other_policy(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id, declaration = automatic_position(session)
        from app.domain.policy_configuration import validate_configuration

        updated = authorization(single_action_cap_cents=900000)
        change_policy(
            session,
            DEMO_USER_ID,
            policy_id,
            version_id,
            updated,
            configuration_hash(validate_configuration(updated)),
            True,
            "new limit",
            "new-limit",
            SEED_AS_OF,
        )
        asset_policy(session)
        exposure_statement(session, [declaration])
    with Session(boundary_engine) as session:
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        exposure = load_asset_exposure(session, context, authorization())
        assert context.sources.issues == []
        assert exposure.managed_principal_cents == 250000
        assert exposure.pending_purchase_cents == 0
        assert len(exposure.counted_position_ids) == 1
        assert len(exposure.excluded_manual_position_ids) == 2


@pytest.mark.parametrize(
    "fault", ["wrong_source_account", "duplicate_attempt", "missing_position", "wrong_transaction"]
)
def test_materialization_binding_must_be_unique_and_use_the_actual_bank_source(
    boundary_engine: Engine, fault: str
) -> None:
    with Session(boundary_engine) as session, session.begin():
        _, _, declaration = automatic_position(session)
        action = session.get(ActionPlan, UUID(declaration["action_id"]))
        assert action is not None
        if fault == "wrong_source_account":
            other = session.scalar(select(Account).where(Account.account_type == "GOAL"))
            assert other is not None
            action.source_account_id = other.id
        elif fault == "missing_position":
            action.position_id = None
        elif fault == "wrong_transaction":
            wrong = session.scalar(select(Transaction).where(Transaction.category == "food"))
            assert wrong is not None
            declaration["transaction_id"] = str(wrong.id)
        else:
            session.add(
                ActionReceipt(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    created_at=SEED_AS_OF,
                    action_plan_id=action.id,
                    attempt_number=2,
                    receipt_ref=str(uuid4()),
                    status="SUCCEEDED",
                    executed_cents=250000,
                    occurred_at=SEED_AS_OF,
                    response={},
                )
            )
        exposure_statement(session, [declaration])
    with Session(boundary_engine) as session:
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        load_asset_exposure(session, context, authorization())
        assert any(
            item.code == "EXPOSURE_RECONCILIATION_REQUIRED" for item in context.sources.issues
        )


def linked_goal_policy(session: Session) -> tuple[UUID, UUID]:
    from app.domain.policy_configuration import validate_configuration

    identifier = goal_fixture(session, with_proofs=True)
    goal = session.get(Goal, identifier)
    assert goal is not None
    asset_id = asset_policy(session, scope="goal", goal_id=str(identifier))
    version = session.get(PolicyVersion, goal.policy_version_id)
    assert version is not None
    config = {**version.configuration, "asset_policy_id": str(asset_id)}
    change_policy(
        session,
        DEMO_USER_ID,
        goal.policy_id,
        version.id,
        config,
        configuration_hash(validate_configuration(config)),
        True,
        "asset reference",
        "asset-reference",
        SEED_AS_OF,
    )
    exposure_statement(session)
    return identifier, asset_id


@pytest.mark.parametrize(
    "fault",
    ["missing_reference", "wrong_projection", "stale_version", "target_projection", "paused_goal"],
)
def test_goal_allocation_requires_current_mutual_authority_and_matching_projection(
    boundary_engine: Engine, fault: str
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_id, policy_id = linked_goal_policy(session)
        goal = session.get(Goal, goal_id)
        assert goal is not None
        if fault == "missing_reference":
            from app.domain.policy_configuration import validate_configuration

            version = session.get(PolicyVersion, goal.policy_version_id)
            assert version is not None
            config = {**version.configuration, "asset_policy_id": None}
            change_policy(
                session,
                DEMO_USER_ID,
                goal.policy_id,
                version.id,
                config,
                configuration_hash(validate_configuration(config)),
                True,
                "unlink assets",
                "unlink-assets",
                SEED_AS_OF,
            )
        elif fault == "wrong_projection":
            goal.asset_policy_id = None
        elif fault == "stale_version":
            older = session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.policy_id == goal.policy_id)
                .order_by(PolicyVersion.version_number)
            )
            assert older is not None
            goal.policy_version_id = older.id
        elif fault == "target_projection":
            goal.target_cents += 1
        else:
            suspend_policy(
                session, DEMO_USER_ID, goal.policy_id, goal.policy_version_id, SEED_AS_OF
            )
    with Session(boundary_engine) as session:
        result = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        assert result.allocation.status == "INACTIVE_POLICY"
        assert result.allocation.suggested_cents is None


def test_complete_statement_cannot_hide_a_live_bank_position_by_deleting_its_projection(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        position = session.scalar(select(AssetPosition))
        assert position is not None
        # The independent bank ledger now protects a known projection at the DB layer.
        with (
            pytest.raises(IntegrityError, match="fk_simulated_bank_postings_position_id"),
            session.begin_nested(),
        ):
            session.delete(position)
            session.flush()
        # An imported bank position can still be absent from the local projection.
        # The importer must reject this complete-statement claim independently of FK safety.
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
            )
        )
        assert proof is not None
        missing_id = uuid4()
        content = {**proof.content, "position_id": str(missing_id)}
        session.add(
            EvidenceItem(
                user_id=DEMO_USER_ID,
                source_type="SIMULATED_BANK_POSITION",
                source_ref=f"unprojected-position:{missing_id}",
                evidence_level="BANK_CONFIRMED",
                content=content,
                content_hash=configuration_hash(content),
                observed_at=SEED_AS_OF,
                valid_from=SEED_AS_OF,
                status="VALID",
            )
        )
        exposure_statement(session)
    with Session(boundary_engine) as session:
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        load_asset_exposure(session, context, authorization())
        assert any(
            item.code == "EXPOSURE_RECONCILIATION_REQUIRED" for item in context.sources.issues
        )


def test_general_preview_selects_latest_t1_without_writes_and_survives_clock_advance(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id = asset_policy(session)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = preview_asset_allocation(
            session, DEMO_USER_ID, policy_id, SEED_AS_OF + timedelta(hours=1)
        )
        assert result.source_issues == []
        assert result.allocation.status == "READY"
        assert result.allocation.selected_asset_class == "CASH_MGMT_T1"
        assert result.allocation.suggested_cents == 1000000
        assert result.allocation.net_simulated_yield_cents == 4389
        chosen = session.get(AssetProduct, result.allocation.selected_product_id)
        assert chosen is not None and chosen.version_number == 2
        assert result == preview_asset_allocation(
            session, DEMO_USER_ID, policy_id, SEED_AS_OF + timedelta(hours=1)
        )
    assert snapshot(boundary_engine) == before


def test_goal_preview_places_only_owned_cash_without_increasing_contributions(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        _, policy_id = linked_goal_policy(session)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        result = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        assert result.source_issues == []
        assert result.allocation.status == "READY"
        assert result.allocation.suggested_cents == 160000
        assert result.allocation.candidate_boundary is not None
        assert (
            result.allocation.candidate_boundary.safe_idle_cents
            == result.allocation.baseline_boundary.safe_idle_cents
        )
    assert snapshot(boundary_engine) == before


def test_pending_order_reduces_real_preview_scope_budget(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(
            session, authorization(max_auto_managed_cents=100000, single_action_cap_cents=100000)
        )
        action = purchase_action(session, version_id)
        exposure_statement(session, [{"action_id": str(action.id), "state": "RESERVED_UNDEBITED"}])
    with Session(boundary_engine) as session:
        result = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        assert result.source_issues == []
        assert result.allocation.remaining_managed_cents == 30000
        assert result.allocation.suggested_cents == 30000


def test_display_yield_changes_selection_but_never_cash_boundary(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id = asset_policy(session)
    with Session(boundary_engine) as session:
        before = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        product = session.scalar(
            select(AssetProduct).where(
                AssetProduct.asset_class == "CASH_MGMT_T1", AssetProduct.version_number == 2
            )
        )
        position = session.scalar(select(AssetPosition))
        assert product is not None and position is not None
        product.annual_yield_bps += 100
        product.maturity_rule = {
            **product.maturity_rule,
            "yield_rule": {
                **product.maturity_rule["yield_rule"],
                "annual_yield_bps": product.annual_yield_bps,
            },
        }
        position.accrued_yield_cents += 99000
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                evidence_level="USER_DECLARED",
                source_type="FUTURE_INCOME_PLAN",
                source_ref=str(uuid4()),
                content={"amount_cents": 90000000},
                content_hash=configuration_hash({"amount_cents": 90000000}),
                observed_at=SEED_AS_OF,
                valid_from=SEED_AS_OF,
                status="VALID",
            )
        )
    with Session(boundary_engine) as session:
        after = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        assert after.allocation.suggested_cents == before.allocation.suggested_cents
        assert (
            after.allocation.baseline_boundary.boundary_hash
            == before.allocation.baseline_boundary.boundary_hash
        )
        assert after.allocation.net_simulated_yield_cents is not None
        assert before.allocation.net_simulated_yield_cents is not None
        assert (
            after.allocation.net_simulated_yield_cents > before.allocation.net_simulated_yield_cents
        )
        assert after.allocation.selection_hash != before.allocation.selection_hash
