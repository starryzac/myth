"""Independent autonomy oracle: labels never manufacture evidence or continuing authority.

The boundary results below are literal, already-verified adapter inputs. These tests audit
classification and economic identity; they do not claim to independently recompute cash flow.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

import pytest
from app.db.models import ActionResourceReservation, EvidenceItem
from app.domain.asset_allocation_types import PlannedExit
from app.domain.autonomy import classify_autonomy, economic_signature
from app.domain.autonomy_types import (
    AuthorityAssessment,
    AutonomyFacts,
    AutonomyWorld,
    FiniteUserVariable,
    PayeeAssessment,
)
from app.domain.boundary_types import BoundaryResult, BoundarySnapshot, CashFact, SourceIssue
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import (
    BillReference,
    CashUse,
    ConfirmationGrant,
    ExecutionEffect,
    ExecutionValidation,
)
from app.domain.income_ledger import IncomeUse
from app.services.action_contracts import PurchaseIntent
from app.services.autonomy import assess_action, assess_intent
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution_bank import process_operation
from app.services.policy_lifecycle import PolicyLifecycleError, suspend_policy
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import confirmed_policy, snapshot
from app.tests.test_execution_bank import redemption_command
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 4, 2, tzinfo=UTC)
USER, SOURCE, DESTINATION = UUID(int=1), UUID(int=2), UUID(int=3)
POLICY, VERSION = UUID(int=4), UUID(int=5)
AUTH_EVIDENCE, BANK_EVIDENCE, VARIABLE_EVIDENCE = UUID(int=6), UUID(int=7), UUID(int=8)
CONTEXT_HASH = "a" * 64


def payment(amount: int = 200) -> ExecutionEffect:
    return ExecutionEffect(
        operation_id=UUID(int=10),
        user_id=USER,
        business_key="bill:independent-audit",
        action_type="PAY_RECURRING",
        amount_cents=amount,
        cash_uses=[CashUse(account_id=SOURCE, amount_cents=amount)],
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        liability=BillReference(bill_id=UUID(int=11), evidence_ids=[BANK_EVIDENCE]),
        payee_id="synthetic:known-creditor",
        payee_evidence_id=BANK_EVIDENCE,
        valid_from=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def redemption(fee: int = 0, loss: int = 0) -> ExecutionEffect:
    return ExecutionEffect(
        operation_id=UUID(int=12),
        user_id=USER,
        business_key="position:independent-audit",
        action_type="REDEEM_ASSET",
        amount_cents=1000,
        destination_account_id=SOURCE,
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        product_id=UUID(int=13),
        product_version_number=1,
        terms_digest="b" * 64,
        position_id=UUID(int=14),
        position_account_id=UUID(int=15),
        original_policy_version_id=UUID(int=16),
        fee_cents=fee,
        loss_cents=loss,
        net_cents=1000 - fee - loss,
        quote_id=UUID(int=17),
        latest_arrival_at=NOW + timedelta(days=1),
        settlement_delay_days=1,
        valid_from=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def purchase() -> ExecutionEffect:
    return ExecutionEffect(
        operation_id=UUID(int=20),
        user_id=USER,
        business_key="asset:independent-audit",
        action_type="PURCHASE_ASSET",
        amount_cents=200,
        cash_uses=[
            CashUse(account_id=SOURCE, amount_cents=100),
            CashUse(account_id=DESTINATION, amount_cents=100),
        ],
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        product_id=UUID(int=13),
        product_version_number=1,
        terms_digest="b" * 64,
        position_id=UUID(int=21),
        position_account_id=UUID(int=15),
        return_account_id=SOURCE,
        purchase_exit=PlannedExit(
            kind="PLANNED_REDEMPTION",
            request_at=NOW + timedelta(days=30),
            principal_available_at=NOW + timedelta(days=31),
            earning_days=30,
            liquidity_days=31,
            terms_digest="b" * 64,
        ),
        latest_arrival_at=NOW + timedelta(days=31),
        settlement_delay_days=1,
        valid_from=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def boundary(margin: int = 800) -> BoundaryResult:
    return BoundaryResult(
        algorithm_version="independent-literal-classification-fixture",
        status="READY" if margin >= 0 else "LIQUIDITY_RISK",
        safe_idle_cents=max(0, margin),
        minimum_margin_cents=margin,
        deficit_cents=max(0, -margin),
        protected_cents_by_reason={},
        max_allocatable_by_product={},
        blocking_constraints=[],
        calculation_trace=[],
        boundary_hash="c" * 64,
    )


def validation(
    effect: ExecutionEffect,
    status: Literal["READY", "CONFIRMATION_REQUIRED", "BLOCKED", "INSUFFICIENT_EVIDENCE"] = "READY",
    *,
    margin: int = 800,
    reasons: list[str] | None = None,
) -> ExecutionValidation:
    return ExecutionValidation(
        status=status,
        effect_hash=execution_effect_hash(effect),
        baseline_boundary=boundary(),
        projected_boundary=boundary(margin),
        projected_snapshot=BoundarySnapshot(
            as_of=NOW,
            timezone="UTC",
            cash_accounts=[
                CashFact(
                    account_id=SOURCE,
                    account_type="CASH",
                    balance_cents=max(0, margin),
                    observed_at=NOW,
                    evidence_ids=[BANK_EVIDENCE],
                )
            ],
        ),
        reasons=reasons or [],
    )


def facts(effect: ExecutionEffect | None = None) -> AutonomyFacts:
    effect = effect or payment()
    return AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type=effect.action_type,
        initiation="CONFIRMED_POLICY",
        authority=AuthorityAssessment(
            status="AUTHORIZED", policy_version_ids=[VERSION], evidence_ids=[AUTH_EVIDENCE]
        ),
        effect=effect,
        validation=validation(effect),
        payee=PayeeAssessment(status="EXISTING_CONFIRMED", evidence_ids=[BANK_EVIDENCE])
        if effect.action_type == "PAY_RECURRING"
        else PayeeAssessment(),
        source_evidence_ids=[BANK_EVIDENCE],
        source_context_hash=CONTEXT_HASH,
    )


def variable(first: AutonomyFacts, second: AutonomyFacts) -> FiniteUserVariable:
    return FiniteUserVariable(
        variable_id="confirmed-user-preference",
        kind="USER_PREFERENCE",
        completeness="COMPLETE",
        evidence_ids=[VARIABLE_EVIDENCE],
        source_context_hash=CONTEXT_HASH,
        worlds=[
            AutonomyWorld(candidate_key="first", facts=first),
            AutonomyWorld(candidate_key="second", facts=second),
        ],
    )


def grant(effect: ExecutionEffect) -> ConfirmationGrant:
    return ConfirmationGrant(
        user_id=USER,
        operation_id=effect.operation_id,
        effect_hash=execution_effect_hash(effect),
        evidence_id=UUID(int=30),
        confirmed_at=NOW,
        expires_at=effect.expires_at,
    )


def test_audit_old_effect_cannot_inherit_an_expanded_new_policy() -> None:
    original = facts()
    new_authority = original.authority.model_copy(update={"policy_version_ids": [UUID(int=99)]})
    result = classify_autonomy(original.model_copy(update={"authority": new_authority}))
    assert result.level == "BLOCKED"
    assert result.execution_eligible is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("OUTSIDE_AUTHORITY", "ADVISE_ONLY"),
        ("MISSING_EVIDENCE", "BLOCKED"),
        ("STALE_VERSION", "BLOCKED"),
    ],
)
def test_audit_complete_denial_and_missing_authority_are_not_the_same_question(
    status: str, expected: str
) -> None:
    original = facts()
    authority = AuthorityAssessment.model_validate(
        {**original.authority.model_dump(), "status": status}
    )
    result = classify_autonomy(original.model_copy(update={"authority": authority}))
    assert result.level == expected
    assert result.execution_eligible is False
    assert result.confirmation_required is False


def test_audit_uncomputed_policy_advice_must_not_claim_financial_verification() -> None:
    original = facts()
    original = original.model_copy(
        update={
            "effect": None,
            "validation": None,
            "authority": original.authority.model_copy(update={"status": "OUTSIDE_AUTHORITY"}),
        }
    )
    result = classify_autonomy(original)
    assert (result.level, result.financial_evaluation) == ("ADVISE_ONLY", "NOT_EVALUATED")
    assert result.execution_eligible is False and result.effect_hash is None


@pytest.mark.parametrize("missing", ["validation", "source", "effect_hash"])
def test_audit_consent_cannot_repair_unverified_bank_or_financial_facts(missing: str) -> None:
    effect = redemption(10, 20)
    original = facts(effect).model_copy(update={"confirmation": grant(effect)})
    if missing == "validation":
        original = original.model_copy(update={"validation": None})
    elif missing == "source":
        original = original.model_copy(
            update={"source_issues": [SourceIssue(code="QUOTE_MISSING", entity_type="bank_quote")]}
        )
    else:
        original = original.model_copy(update={"validation": validation(payment())})
    result = classify_autonomy(original)
    assert result.level == "BLOCKED" and result.execution_eligible is False


@pytest.mark.parametrize("fee,loss", [(1, 0), (0, 1), (100, 200)])
def test_audit_any_explicit_principal_cost_needs_exact_one_shot_consent(
    fee: int, loss: int
) -> None:
    effect = redemption(fee, loss)
    original = facts(effect)
    before = classify_autonomy(original)
    after = classify_autonomy(original.model_copy(update={"confirmation": grant(effect)}))
    assert before.level == after.level == "ASK_ONCE"
    assert before.execution_eligible is False and before.confirmation_required is True
    assert after.execution_eligible is True and after.confirmation_satisfied is True
    changed = effect.model_copy(update={"operation_id": UUID(int=31)})
    replay = facts(changed).model_copy(update={"confirmation": grant(effect)})
    result = classify_autonomy(replay)
    assert result.level != "AUTO_EXECUTE" and result.execution_eligible is False


@pytest.mark.parametrize("status", ["AGENT_NEW", "MISSING_IDENTITY", "UNSUPPORTED"])
def test_audit_new_or_unidentified_payee_is_not_created_by_confirmation(status: str) -> None:
    original = facts()
    assert original.effect is not None
    original = original.model_copy(
        update={
            "payee": PayeeAssessment.model_validate({"status": status, "evidence_ids": []}),
            "confirmation": grant(original.effect),
        }
    )
    result = classify_autonomy(original)
    assert result.level == "BLOCKED" and result.execution_eligible is False


def test_audit_user_requested_new_payee_is_unsupported_even_after_exact_consent() -> None:
    original = facts()
    assert original.effect is not None
    original = original.model_copy(
        update={
            "initiation": "USER_EXPLICIT",
            "payee": PayeeAssessment(status="USER_INITIATED_NEW", evidence_ids=[BANK_EVIDENCE]),
            "confirmation": grant(original.effect),
        }
    )
    result = classify_autonomy(original)
    assert result.level == "BLOCKED" and result.execution_eligible is False
    assert "UNSUPPORTED_PAYEE_RELATIONSHIP" in result.reasons


@pytest.mark.property
@settings(max_examples=100, derandomize=True, database=None, deadline=None)
@given(
    fee=st.integers(0, 300), loss=st.integers(0, 300), consent=st.booleans(), denial=st.booleans()
)
def test_audit_hard_financial_block_dominates_permission_cost_and_consent(
    fee: int, loss: int, consent: bool, denial: bool
) -> None:
    effect = redemption(fee, loss)
    original = facts(effect)
    original = original.model_copy(
        update={
            "authority": original.authority.model_copy(
                update={"status": "OUTSIDE_AUTHORITY" if denial else "AUTHORIZED"}
            ),
            "validation": validation(
                effect, "BLOCKED", margin=-1, reasons=["RECOVERY_WORSENS_PROTECTED_BOUNDARY"]
            ),
            "hard_block_reasons": ["RECOVERY_WORSENS_PROTECTED_BOUNDARY"],
            "confirmation": grant(effect) if consent else None,
        }
    )
    result = classify_autonomy(original)
    assert result.level == "BLOCKED" and result.execution_eligible is False


def test_audit_complete_user_worlds_distinguish_safe_divergence_from_bank_uncertainty() -> None:
    first, second = facts(), facts(payment(300))
    uncertain = variable(first, second)
    result = classify_autonomy(first, uncertain)
    assert result.level == "ASK_ONCE" and result.uncertainty_status == "DIVERGENT"
    assert result.execution_eligible is False and result.effect_hash is None
    assert (
        classify_autonomy(first, uncertain.model_copy(update={"kind": "BANK_FACT"})).level
        == "BLOCKED"
    )
    assert (
        classify_autonomy(first, uncertain.model_copy(update={"completeness": "INCOMPLETE"})).level
        == "BLOCKED"
    )


def test_audit_one_user_world_financially_unsafe_requires_answer_not_execution() -> None:
    first = facts()
    second = facts(payment(900))
    assert second.effect is not None
    second = second.model_copy(
        update={
            "validation": validation(
                second.effect, "BLOCKED", margin=-100, reasons=["UNAVAILABLE_SOURCE_CASH"]
            )
        }
    )
    result = classify_autonomy(first, variable(first, second))
    assert result.level == "ASK_ONCE" and result.execution_eligible is False
    assert result.effect_hash is None


def test_audit_every_world_with_the_same_hard_block_remains_blocked() -> None:
    first, second = facts(), facts(payment(900))
    worlds = []
    for original in (first, second):
        assert original.effect is not None
        worlds.append(
            original.model_copy(
                update={
                    "validation": validation(
                        original.effect, "BLOCKED", margin=-100, reasons=["UNAVAILABLE_SOURCE_CASH"]
                    )
                }
            )
        )
    result = classify_autonomy(worlds[0], variable(worlds[0], worlds[1]))
    assert result.level == "BLOCKED" and result.execution_eligible is False
    assert result.confirmation_required is False


@pytest.mark.parametrize("defect", ["source", "context", "old_version", "other_user", "new_payee"])
def test_audit_a_fundamentally_invalid_world_cannot_be_selected_away(defect: str) -> None:
    first, second = facts(), facts(payment(300))
    if defect == "source":
        second = second.model_copy(
            update={
                "source_issues": [
                    SourceIssue(code="BANK_BALANCE_MISSING", entity_type="bank_balance")
                ]
            }
        )
    elif defect == "context":
        second = second.model_copy(update={"source_context_hash": "d" * 64})
    elif defect == "old_version":
        second = second.model_copy(
            update={"authority": second.authority.model_copy(update={"status": "STALE_VERSION"})}
        )
    elif defect == "other_user":
        second = second.model_copy(update={"user_id": UUID(int=999)})
    else:
        second = second.model_copy(update={"payee": PayeeAssessment(status="AGENT_NEW")})
    result = classify_autonomy(first, variable(first, second))
    assert result.level == "BLOCKED" and result.execution_eligible is False


def test_audit_asking_about_preferences_cannot_enlarge_authority() -> None:
    first, second = facts(), facts(payment(300))
    second = second.model_copy(
        update={"authority": second.authority.model_copy(update={"status": "OUTSIDE_AUTHORITY"})}
    )
    result = classify_autonomy(first, variable(first, second))
    assert result.level in {"ADVISE_ONLY", "BLOCKED"}
    assert result.execution_eligible is False and result.confirmation_required is False


@pytest.mark.property
@settings(max_examples=100, derandomize=True, database=None, deadline=None)
@given(identity=st.integers(1000, 1000000), margin=st.integers(0, 1000000))
def test_audit_non_economic_identity_order_and_surplus_do_not_create_questions(
    identity: int, margin: int
) -> None:
    original = purchase()
    changed = original.model_copy(
        update={
            "operation_id": UUID(int=identity),
            "position_id": UUID(int=identity + 1),
            "cash_uses": list(reversed(original.cash_uses)),
        }
    )
    assert economic_signature(original) == economic_signature(changed)
    first, second = facts(original), facts(changed)
    second = second.model_copy(update={"validation": validation(changed, margin=margin)})
    result = classify_autonomy(first, variable(first, second))
    assert result.level == "AUTO_EXECUTE" and result.uncertainty_status == "STABLE"


@pytest.mark.property
@settings(max_examples=100, derandomize=True, database=None, deadline=None)
@given(amount=st.integers(1, 999999), extra=st.integers(1, 999999))
def test_audit_a_one_cent_or_larger_economic_change_is_never_stable(
    amount: int, extra: int
) -> None:
    first, second = facts(payment(amount)), facts(payment(amount + extra))
    assert first.effect is not None and second.effect is not None
    assert economic_signature(first.effect) != economic_signature(second.effect)
    result = classify_autonomy(first, variable(first, second))
    assert result.level == "ASK_ONCE" and result.execution_eligible is False


def test_audit_signature_retains_return_destination_exit_and_existing_position_identity() -> None:
    original = purchase()
    changed_return = original.model_copy(update={"return_account_id": DESTINATION})
    assert original.purchase_exit is not None
    changed_exit = original.model_copy(
        update={
            "purchase_exit": original.purchase_exit.model_copy(
                update={"request_at": NOW + timedelta(days=29), "earning_days": 29}
            )
        }
    )
    assert economic_signature(original) != economic_signature(changed_return)
    assert economic_signature(original) != economic_signature(changed_exit)
    existing = redemption()
    changed_position = existing.model_copy(update={"position_id": UUID(int=1000)})
    assert economic_signature(existing) != economic_signature(changed_position)


def test_audit_signature_retains_exact_cash_split_and_income_origin_fragment() -> None:
    original = purchase()
    changed_split = original.model_copy(
        update={
            "cash_uses": [
                CashUse(account_id=SOURCE, amount_cents=99),
                CashUse(account_id=DESTINATION, amount_cents=101),
            ]
        }
    )
    assert economic_signature(original) != economic_signature(changed_split)
    effects = [
        payment().model_copy(
            update={
                "income_uses": [
                    IncomeUse(
                        fragment_id=uuid5(origin, "income-location:" + str(SOURCE)),
                        origin_transaction_id=origin,
                        account_id=SOURCE,
                        amount_cents=50,
                    )
                ]
            }
        )
        for origin in (UUID(int=101), UUID(int=102))
    ]
    assert economic_signature(effects[0]) != economic_signature(effects[1])


@pytest.mark.parametrize("changed_field", ["return_account", "exit", "source", "payee", "origin"])
def test_audit_amount_preference_cannot_smuggle_an_unrelated_economic_change(
    changed_field: str,
) -> None:
    original = purchase() if changed_field in {"return_account", "exit"} else payment()
    if changed_field == "return_account":
        changed = original.model_copy(update={"return_account_id": DESTINATION})
    elif changed_field == "exit":
        assert original.purchase_exit is not None
        changed = original.model_copy(
            update={
                "purchase_exit": original.purchase_exit.model_copy(
                    update={"request_at": NOW + timedelta(days=29), "earning_days": 29}
                )
            }
        )
    elif changed_field == "source":
        changed = original.model_copy(
            update={
                "cash_uses": [CashUse(account_id=DESTINATION, amount_cents=original.amount_cents)]
            }
        )
    elif changed_field == "payee":
        changed = original.model_copy(update={"payee_id": "synthetic:different-creditor"})
    else:

        def use(origin: UUID) -> IncomeUse:
            return IncomeUse(
                fragment_id=uuid5(origin, "income-location:" + str(SOURCE)),
                origin_transaction_id=origin,
                account_id=SOURCE,
                amount_cents=50,
            )

        original = original.model_copy(update={"income_uses": [use(UUID(int=101))]})
        changed = original.model_copy(update={"income_uses": [use(UUID(int=102))]})
    first, second = facts(original), facts(changed)
    result = classify_autonomy(first, variable(first, second))
    assert result.level == "BLOCKED" and result.execution_eligible is False
    assert result.confirmation_required is False


def test_audit_stable_worlds_do_not_erase_an_existing_cost_question() -> None:
    effect = redemption(10, 20)
    first, second = facts(effect), facts(effect)
    result = classify_autonomy(first, variable(first, second))
    assert result.level == "ASK_ONCE" and result.uncertainty_status == "STABLE"
    assert result.execution_eligible is False


@pytest.mark.integration
def test_audit_pg_known_paused_authority_does_not_hide_a_later_bad_bank_proof(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, authorization())
        suspend_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
    intent = PurchaseIntent(kind="purchase_asset", policy_id=policy_id)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        advised = assess_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert advised.decision.level == "ADVISE_ONLY"
        assert advised.decision.financial_evaluation == "NOT_EVALUATED"
        assert advised.decision.execution_eligible is False and advised.effect is None
    assert snapshot(boundary_engine) == before
    with Session(boundary_engine) as session, session.begin():
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == DEMO_USER_ID,
                EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                EvidenceItem.status == "VALID",
            )
        ).first()
        assert proof is not None
        proof.content = {**proof.content, "balance_cents": proof.content["balance_cents"] + 1}
        # Deliberately retain the original hash: explicit source corruption, not a new fact.
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        blocked = assess_intent(session, DEMO_USER_ID, intent, SEED_AS_OF)
        assert blocked.decision.level == "BLOCKED"
        assert blocked.decision.execution_eligible is False and blocked.effect is None
    assert snapshot(boundary_engine) == before


@pytest.mark.integration
def test_audit_pg_bank_accepted_action_cannot_be_reclassified_or_release_claims(
    boundary_engine: Engine,
) -> None:
    action_id, _, _ = redemption_command(boundary_engine, delay=1)
    bank = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert bank.status == "ACCEPTED"
    with Session(boundary_engine) as session:
        claims = list(
            session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.action_plan_id == action_id,
                    ActionResourceReservation.status == "RESERVED",
                )
            )
        )
        assert claims and all(claim.amount_cents > 0 for claim in claims)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        with pytest.raises(PolicyLifecycleError) as caught:
            assess_action(session, DEMO_USER_ID, action_id, SEED_AS_OF)
        assert caught.value.code == "NO_RECLASSIFICATION_AFTER_ACCEPTANCE"
        assert caught.value.status_code == 409
    assert snapshot(boundary_engine) == before
