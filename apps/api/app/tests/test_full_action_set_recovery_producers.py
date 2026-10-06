"""Synthetic source-shaped direct risks, never actual banking or formal case evidence."""

import copy
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.domain.autonomy_types import AuthorityAssessment, AutonomyFacts
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence, TracePolicy
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionContext
from app.domain.full_action_set_boundary import (
    ActionSetInput,
    CandidateInput,
    unsupported_producers,
)
from app.domain.full_action_set_boundary_actual import (
    REQUIRED_TABLES,
    ActualActionSetInput,
    actual_producer_keys,
)
from app.domain.full_action_set_recovery_producers import (
    ALGORITHM,
    RecoveryActionSetInput,
    RecoveryOriginalCommand,
    RecoveryProducerInput,
    _funding_destination,
    derive_recovery_producers,
    verify_frozen_recovery_producer_inputs,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_recovery_execution import build_full_recovery_effect
from app.domain.full_recovery_planning import plan_full_recovery
from app.domain.multi_goal_allocation import SourceReference
from app.domain.policy_configuration import configuration_hash
from app.services.full_action_set_recovery_producers import verify_frozen_recovery_producers
from app.services.full_policy_lifecycle import FullPolicyView, FullVersionView
from app.services.full_recovery_planning import FullRecoveryPlanningResponse
from app.services.product_catalog import (
    CatalogProductBinding,
    CatalogReadResponse,
    CatalogVersionView,
)
from app.tests.test_full_action_set_boundary_actual import coverage
from app.tests.test_full_recovery_execution import EPOCH
from app.tests.test_full_recovery_execution import fixture as execution_fixture
from app.tests.test_full_recovery_planning import holding
from app.tests.test_full_recovery_planning import inputs as planning_fixture
from app.tests.test_recovery import NOW, USER


def fixture(*, delay: int = 0, cash: int = 70000) -> RecoveryActionSetInput:
    planning = planning_fixture([holding(delay=delay)])
    metadata = planning.holdings[0]
    original = metadata.original
    authorization = original.original_authorization
    assert authorization is not None and original.quote is not None
    raw: dict[str, list[dict[str, Any]]] = {name: [] for name in REQUIRED_TABLES}
    raw["users"] = [{"id": str(USER), "is_simulated": True}]
    raw["accounts"] = [
        {
            "id": str(original.destination_account_id),
            "user_id": str(USER),
            "account_type": "CASH",
            "balance_cents": cash,
        },
        {
            "id": str(original.account_id),
            "user_id": str(USER),
            "account_type": "ASSET",
            "balance_cents": 0,
        },
    ]
    bank_id, quote_id, full_evidence_id, funding_id = (UUID(int=key) for key in range(610, 614))
    transaction_id = UUID(int=614)
    quote = original.quote.model_copy(update={"evidence_ids": [quote_id]})
    original = original.model_copy(update={"quote": quote, "evidence_ids": [bank_id]})
    metadata = metadata.model_copy(update={"original": original})
    product = original.product
    canonical = product.model_dump(mode="json", exclude={"product_id", "terms_digest"}) | {
        "id": str(product.product_id),
        "early_withdrawal_rule": {},
        "currency": "CNY",
    }
    product_hash = configuration_hash(canonical)
    metadata = metadata.model_copy(update={"product_record_hash": product_hash})
    raw["asset_products"] = [canonical]
    raw["product_catalog_versions"] = [
        {
            "id": str(metadata.catalogue_version_id),
            "product_id": str(product.product_id),
            "product_code": product.product_code,
            "version_number": product.version_number,
            "protocol_version": "product-catalog-v1",
            "canonical_product": canonical,
            "product_hash": product_hash,
            "terms_digest": product.terms_digest,
        }
    ]
    raw["asset_positions"] = [
        {
            "id": str(original.position_id),
            "user_id": str(USER),
            "product_id": str(product.product_id),
            "account_id": str(original.account_id),
            "goal_id": None,
            "status": "HELD",
            "policy_version_id": str(authorization.version_id),
            "principal_cents": quote.principal_cents,
            "purchased_at": original.purchased_at.isoformat(),
            "maturity_at": None,
        }
    ]
    versions = [*planning.boundary_versions, authorization]
    for version in versions:
        raw["policies"].append(
            {"id": str(version.policy_id), "user_id": str(USER), "status": "ACTIVE"}
        )
        raw["policy_versions"].append(
            {
                "id": str(version.version_id),
                "user_id": str(USER),
                "policy_id": str(version.policy_id),
                "version_number": 1,
                "configuration": version.configuration,
                "content_hash": version.content_hash,
                "confirmed_at": version.confirmed_at.isoformat(),
                "valid_from": version.valid_from.isoformat(),
                "valid_until": None,
            }
        )
    bank_content = {
        **raw["asset_positions"][0],
        "position_id": str(original.position_id),
        "return_account_id": str(original.destination_account_id),
        "acquisition": "synthetic_auto_purchase",
        "fixture_tier": "SYNTHETIC_TOOL_ONLY",
        "purchase_transaction_id": str(transaction_id),
    }
    transaction = {
        "id": str(transaction_id),
        "user_id": str(USER),
        "account_id": str(original.destination_account_id),
        "direction": "DEBIT",
        "amount_cents": quote.principal_cents,
        "balance_after_cents": cash,
        "occurred_at": original.purchased_at.isoformat(),
        "observed_at": NOW.isoformat(),
        "evidence_id": str(funding_id),
    }
    raw["transactions"] = [transaction]
    quote_content = {
        "protocol": "recovery-quote-v1",
        "user_id": str(USER),
        "position_id": str(original.position_id),
        "destination_account_id": str(original.destination_account_id),
        "goal_id": None,
        "quote": quote.model_dump(mode="json"),
    }
    for identity, source, content in (
        (bank_id, "SIMULATED_BANK_POSITION", bank_content),
        (quote_id, "SIMULATED_REDEMPTION_QUOTE", quote_content),
        (full_evidence_id, "SYNTHETIC_USER_DECLARATION", {"fixture_tier": "SYNTHETIC_TOOL_ONLY"}),
        (
            funding_id,
            "SIMULATED_BANK_TRANSACTION",
            {
                **transaction,
                "transaction_id": str(transaction_id),
                "economic_role": "ASSET_PURCHASE",
            },
        ),
    ):
        raw["evidence_items"].append(
            {
                "id": str(identity),
                "user_id": str(USER),
                "source_type": source,
                "source_ref": "synthetic:no-bank",
                "content": content,
                "content_hash": configuration_hash(content),
                "status": "VALID",
                "evidence_level": "USER_DECLARED"
                if identity == full_evidence_id
                else "BANK_CONFIRMED",
                "observed_at": NOW.isoformat(),
                "valid_from": (NOW - timedelta(days=30)).isoformat(),
                "valid_to": None,
            }
        )
    config = planning.configuration.model_dump(mode="json")
    config_hash = configuration_hash(config)
    full_version = FullVersionView(
        version_id=planning.policy_version_id,
        policy_id=planning.policy_id,
        version_number=1,
        configuration=config,
        content_hash=config_hash,
        previous_hash=None,
        summary="synthetic",
        confirmation={"accepted": True, "reviewed_hash": config_hash},
        confirmed_at=planning.confirmed_at,
        valid_from=planning.valid_from,
        valid_until=None,
        change_reason="synthetic",
        evidence_ids=[full_evidence_id],
        impact_analysis={},
        confirmation_evidence_status="CURRENT_EVIDENCE_MATCHED",
    )
    full = FullPolicyView(
        policy_id=planning.policy_id,
        epoch_id=EPOCH,
        template_name="RecoveryPolicy",
        name="synthetic",
        status="ACTIVE",
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        current_version=full_version,
        updated_at=NOW,
    )
    raw["full_policies"] = [
        {
            "id": str(full.policy_id),
            "user_id": str(USER),
            "epoch_id": str(EPOCH),
            "template_name": "RecoveryPolicy",
            "status": "ACTIVE",
        }
    ]
    raw["full_policy_versions"] = [
        {
            "id": str(full_version.version_id),
            "user_id": str(USER),
            "policy_id": str(full.policy_id),
            "version_number": 1,
            "configuration": config,
            "content_hash": config_hash,
            "confirmation": full_version.confirmation,
            "impact_analysis": {},
            "previous_hash": None,
            "evidence_ids": [str(full_evidence_id)],
            "confirmed_at": planning.confirmed_at.isoformat(),
            "valid_from": planning.valid_from.isoformat(),
            "valid_until": None,
        }
    ]
    source_ids = [bank_id, quote_id, full_evidence_id, funding_id]
    digest = configuration_hash(
        {
            "user_id": str(USER),
            "as_of": NOW.isoformat(),
            "sources": [
                {"id": row["id"], "hash": row["content_hash"]}
                for row in sorted(raw["evidence_items"], key=lambda row: row["id"])
            ],
            "issues": [],
        }
    )
    planning = planning.model_copy(
        update={
            "snapshot": planning.snapshot.model_copy(
                update={
                    "source_digest": digest,
                    "cash_accounts": [
                        planning.snapshot.cash_accounts[0].model_copy(
                            update={"balance_cents": cash}
                        )
                    ],
                }
            ),
            "boundary_versions": versions,
            "holdings": [metadata],
        }
    )
    plan = plan_full_recovery(planning)
    catalogue = CatalogReadResponse(
        state="REGISTERED",
        versions=[
            CatalogVersionView(
                id=metadata.catalogue_version_id,
                product_id=product.product_id,
                product_code=product.product_code,
                version_number=product.version_number,
                original_product=canonical,
                product_hash=product_hash,
                terms_digest=product.terms_digest,
                observed_at=NOW - timedelta(days=30),
                effective_from=product.effective_from,
                effective_until=product.effective_until,
                current_source_matched=True,
            )
        ],
        unregistered_product_ids=[],
        issues=[],
        complete_within_registered_capacity=True,
    )
    response_hash = configuration_hash(
        {
            "financial_source_digest": digest,
            "policy_version_id": str(full_version.version_id),
            "plan_input_hash": plan.input_hash,
            "catalogue": catalogue.model_dump(mode="json"),
            "planning_deadline_at": None,
        }
    )
    response = FullRecoveryPlanningResponse(
        user_id=USER,
        policy_id=full.policy_id,
        as_of=NOW,
        state="COMPUTED",
        plan=plan,
        catalogue=catalogue,
        catalogue_bindings=[
            CatalogProductBinding(
                product_id=product.product_id,
                catalogue_version_id=metadata.catalogue_version_id,
                product_record_hash=product_hash,
                terms_digest=product.terms_digest,
            )
        ],
        source_evidence_ids=source_ids,
        source_issues=[],
        input_hash=response_hash,
        limitations=[],
    )
    ctx = ExecutionContext(
        user_id=USER,
        snapshot=planning.snapshot,
        versions=versions,
        positions=planning.positions,
        boundary_products=[],
        products=[product],
        redemption_quote=quote,
        requires_confirmation=True,
    )
    financial = {
        "user_id": str(USER),
        "as_of": NOW.isoformat(),
        "snapshot": ctx.snapshot.model_dump(mode="json"),
        "versions": [row.model_dump(mode="json") for row in versions],
        "positions": [row.model_dump(mode="json") for row in planning.positions],
    }
    financial_hash = configuration_hash(financial)
    original_execution = execution_fixture(delay).model_copy(
        update={
            "candidate": plan.candidates[0],
            "deadline_at": plan.deadline_at,
            "original_effect": execution_fixture(delay).original_effect.model_copy(
                update={"quote_id": quote.quote_id}
            ),
            "planning_input_hash": plan.input_hash,
            "planning_response_hash": response_hash,
            "candidate_position_ids": [row.position_id for row in plan.candidates],
            "selected_position_ids": [row.position_id for row in plan.lossless_steps],
            "source_refs": [
                SourceReference(
                    user_id=USER,
                    evidence_id=bank_id,
                    content_hash=raw["evidence_items"][0]["content_hash"],
                )
            ],
        }
    )
    # Parse the complete strict input; model_copy never substitutes for type validation.
    original_execution = type(original_execution).model_validate(original_execution.model_dump())
    producer = RecoveryProducerInput(
        full_policy_id=full.policy_id,
        full_policy=full,
        planning_inputs=planning,
        original_planning=response,
    )
    old_effect = original_execution.original_effect
    old_facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type="REDEEM_ASSET",
        initiation="CONFIRMED_POLICY",
        authority=AuthorityAssessment(
            status="AUTHORIZED",
            policy_version_ids=old_effect.policy_version_ids,
            evidence_ids=[bank_id],
        ),
        effect=old_effect,
        validation=revalidate_execution(old_effect, ctx),
        source_context_hash=financial_hash,
        source_evidence_ids=[bank_id, quote_id],
    )
    old_candidate = CandidateInput(
        candidate_key="redemption:" + str(original.position_id),
        facts=old_facts,
        execution_context=ctx,
    )
    if plan.lossless_steps and delay == 0:
        effect, _ = build_full_recovery_effect(original_execution)
        validation = revalidate_execution(effect, ctx)
        facts = old_facts.model_copy(update={"effect": effect, "validation": validation})
        candidate = CandidateInput(
            candidate_key=old_candidate.candidate_key,
            facts=facts,
            execution_context=ctx,
            full_protection=validate_full_execution_protection(effect, ctx, validation, []),
        )
        producer = producer.model_copy(
            update={"execution_inputs": original_execution, "candidate": candidate}
        )
    base = ActionSetInput(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        original_inventory=raw,
        inventory_reasons=[],
        expected_candidate_keys=actual_producer_keys(raw),
        candidates=[
            old_candidate,
            CandidateInput(candidate_key="purchase:" + str(authorization.policy_id)),
        ],
        financial_input_hash=financial_hash,
        financial_basis=financial,
        audit_verified=True,
        source_reasons=[],
        unsupported_producers=unsupported_producers(raw, EPOCH),
    )
    actual = ActualActionSetInput(
        base=base, table_coverage=coverage(raw), dynamic_goals=[], assets=[]
    )
    return RecoveryActionSetInput(
        original_actual_input=actual,
        expected_full_policy_ids=[full.policy_id],
        original_position_ids=[original.position_id],
        original_action_ids=[],
        original_commands=[],
        producers=[producer],
    )


def changed(
    data: RecoveryActionSetInput, mutation: Callable[[dict[str, Any]], None]
) -> RecoveryActionSetInput:
    raw = data.model_dump(mode="json")
    mutation(raw)
    inventory = raw["original_actual_input"]["base"]["original_inventory"]
    raw["original_actual_input"]["table_coverage"] = [
        row.model_dump(mode="json") for row in coverage(inventory)
    ]
    return RecoveryActionSetInput.model_validate_json(json.dumps(raw))


def test_whole_t0_50000_ask_preserves_old_scope_and_never_claims_authority() -> None:
    data = fixture()
    original = data.model_dump_json()
    result = derive_recovery_producers(data)
    assert result.recovery_family_complete, result.model_dump()
    item = result.results[0]
    assert item.view.state == "INCLUDED" and item.view.amount_cents == 50000
    assert item.view.autonomy_level == "ASK_ONCE" and item.requires_new_exact_user_confirmation
    assert item.shadow_original_candidate_key is None  # FULL tightened original arrival bound.
    assert not result.bank_authority and not result.financial_write
    assert result.original_actual_input_hash == configuration_hash(
        data.original_actual_input.model_dump(mode="json")
    )
    assert data.model_dump_json() == original
    assert verify_frozen_recovery_producer_inputs(data.model_dump(mode="json")) == result


def test_complete_no_need_keeps_original_holding_and_amount_without_fake_cash() -> None:
    data = fixture(cash=110000)
    result = derive_recovery_producers(data)
    assert result.recovery_family_complete, result.reasons
    assert result.results[0].view.state == "EXCLUDED"
    assert result.results[0].view.amount_cents is None
    assert result.original_position_ids and data.producers[0].original_planning is not None
    assert data.producers[0].original_planning.plan is not None
    assert data.producers[0].original_planning.plan.required_recovery_cents == 0


def test_t1_scope_only_proof_is_not_actual_supported_recovery_family() -> None:
    result = derive_recovery_producers(fixture(delay=1))
    assert not result.recovery_family_complete and result.results[0].view.state == "UNKNOWN"
    assert result.results[0].unsupported_position_ids


@pytest.mark.parametrize(
    "mode",
    [
        "drop_position",
        "drop_command",
        "owner",
        "epoch",
        "audit",
        "cash",
        "source_hash",
        "quote",
        "expired_quote",
        "catalogue",
        "catalogue_drop",
        "full_version",
        "full_hash",
        "deadline",
        "old_confirmation",
        "ask",
        "full_protection",
        "capacity",
    ],
)
def test_missing_or_changed_originals_are_unknown_and_do_not_clear_missing_family(
    mode: str,
) -> None:
    def mutation(raw: dict[str, Any]) -> None:
        item = raw["producers"][0]
        inventory = raw["original_actual_input"]["base"]["original_inventory"]
        if mode == "drop_position":
            raw["original_position_ids"] = []
        elif mode == "drop_command":
            inventory["action_plans"].append(
                {
                    "id": str(UUID(int=888)),
                    "user_id": str(USER),
                    "action_type": "ASSET_REDEEM",
                    "request": {},
                }
            )
        elif mode == "owner":
            inventory["asset_positions"][0]["user_id"] = str(UUID(int=999))
        elif mode == "epoch":
            item["full_policy"]["epoch_id"] = str(UUID(int=999))
        elif mode == "audit":
            raw["original_actual_input"]["base"]["audit_verified"] = False
        elif mode == "cash":
            item["planning_inputs"]["snapshot"]["cash_accounts"][0]["balance_cents"] += 1
        elif mode == "source_hash":
            inventory["evidence_items"][0]["content_hash"] = "0" * 64
        elif mode == "quote":
            inventory["evidence_items"][1]["content"]["quote"]["net_cents"] -= 1
        elif mode == "expired_quote":
            item["planning_inputs"]["holdings"][0]["original"]["quote"]["expires_at"] = (
                NOW.isoformat()
            )
            item["planning_inputs"]["holdings"][0]["original"]["quote"]["request_at"] = (
                NOW - timedelta(minutes=5)
            ).isoformat()
        elif mode == "catalogue":
            inventory["asset_products"][0]["risk_level"] = 1
        elif mode == "catalogue_drop":
            inventory["product_catalog_versions"] = []
        elif mode == "full_version":
            item["execution_inputs"]["full_policy_version_id"] = str(UUID(int=999))
        elif mode == "full_hash":
            item["full_policy"]["current_version"]["content_hash"] = "f" * 64
        elif mode == "deadline":
            item["execution_inputs"]["deadline_at"] = (NOW + timedelta(minutes=1)).isoformat()
        elif mode == "old_confirmation":
            item["candidate"]["facts"]["confirmation"] = {
                "user_id": str(USER),
                "operation_id": item["candidate"]["facts"]["effect"]["operation_id"],
                "effect_hash": item["candidate"]["facts"]["validation"]["effect_hash"],
                "evidence_id": str(UUID(int=991)),
                "confirmed_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            }
        elif mode == "ask":
            item["candidate"]["execution_context"]["requires_confirmation"] = False
        elif mode == "full_protection":
            item["candidate"]["full_protection"] = None
        elif mode == "capacity":
            raw["source_reasons"] = ["RECOVERY_CAPTURE_CAPACITY_EXCEEDED"]

    result = derive_recovery_producers(changed(fixture(), mutation))
    assert not result.recovery_family_complete
    assert result.handled_unsupported_codes == []
    assert any("RecoveryPolicy" in reason for reason in result.remaining_unsupported_producers)


def test_suspended_policy_does_not_hide_any_unresolved_original_bank_command() -> None:
    data = fixture()
    actual = data.original_actual_input
    inventory = copy.deepcopy(actual.base.original_inventory)
    action_id = UUID(int=887)
    inventory["action_plans"] = [
        {
            "id": str(action_id),
            "user_id": str(USER),
            "action_type": "ASSET_REDEEM",
            "request": {},
            "status": "UNKNOWN",
        }
    ]
    inventory["full_policies"][0]["status"] = "SUSPENDED"
    item = data.producers[0]
    assert item.full_policy is not None
    data = data.model_copy(
        update={
            "original_actual_input": actual.model_copy(
                update={
                    "base": actual.base.model_copy(update={"original_inventory": inventory}),
                    "table_coverage": coverage(inventory),
                }
            ),
            "original_action_ids": [action_id],
            "original_commands": [RecoveryOriginalCommand(action_id=action_id)],
            "producers": [
                item.model_copy(
                    update={
                        "full_policy": item.full_policy.model_copy(
                            update={"status": "SUSPENDED", "effective_status": "SUSPENDED"}
                        )
                    }
                )
            ],
        }
    )
    result = derive_recovery_producers(data)
    assert result.unresolved_original_action_ids == [action_id]
    assert not result.recovery_family_complete and result.results[0].view.state == "UNKNOWN"


def test_exact_same_effect_shadow_is_unique_but_different_deadline_is_retained() -> None:
    data = fixture()
    producer = data.producers[0]
    assert producer.candidate is not None
    old = data.original_actual_input
    candidates = [
        producer.candidate if row.candidate_key == producer.candidate.candidate_key else row
        for row in old.base.candidates
    ]
    same = data.model_copy(
        update={
            "original_actual_input": old.model_copy(
                update={"base": old.base.model_copy(update={"candidates": candidates})}
            )
        }
    )
    assert (
        derive_recovery_producers(same).results[0].shadow_original_candidate_key
        == producer.candidate.candidate_key
    )
    assert derive_recovery_producers(data).results[0].shadow_original_candidate_key is None


def test_empty_complete_current_policy_inventory_is_a_real_finite_family_not_global_complete() -> (
    None
):
    data = fixture()
    actual = data.original_actual_input
    raw = copy.deepcopy(actual.base.original_inventory)
    raw["full_policies"] = []
    raw["full_policy_versions"] = []
    empty = data.model_copy(
        update={
            "expected_full_policy_ids": [],
            "producers": [],
            "original_actual_input": actual.model_copy(
                update={
                    "base": actual.base.model_copy(
                        update={"original_inventory": raw, "unsupported_producers": []}
                    ),
                    "table_coverage": coverage(raw),
                }
            ),
        }
    )
    result = derive_recovery_producers(empty)
    assert result.recovery_family_complete and not result.full_global_adapter_installed
    assert result.original_position_ids and not result.handled_unsupported_codes


def test_legacy_bank_debit_pointer_resolves_without_inventing_return_account() -> None:
    data = fixture().original_actual_input
    position = data.base.original_inventory["asset_positions"][0]
    content = copy.deepcopy(data.base.original_inventory["evidence_items"][0]["content"])
    content.pop("return_account_id")
    content["acquisition"] = "synthetic_user_manual_purchase"
    assert _funding_destination(data, position, content) == UUID(
        data.base.original_inventory["transactions"][0]["account_id"]
    )


@pytest.mark.parametrize(
    "mode",
    ["missing_pointer", "wrong_owner", "credit", "amount", "bank_type", "bank_identity", "time"],
)
def test_legacy_funding_requires_original_bank_debit_and_exact_principal(mode: str) -> None:
    data = fixture().original_actual_input
    inventory = copy.deepcopy(data.base.original_inventory)
    position = inventory["asset_positions"][0]
    content = inventory["evidence_items"][0]["content"]
    transaction = inventory["transactions"][0]
    proof = inventory["evidence_items"][-1]
    if mode == "missing_pointer":
        content["purchase_transaction_id"] = str(UUID(int=999))
    elif mode == "wrong_owner":
        transaction["user_id"] = str(UUID(int=999))
    elif mode == "credit":
        transaction["direction"] = "CREDIT"
        proof["content"]["direction"] = "CREDIT"
    elif mode == "amount":
        transaction["amount_cents"] -= 1
        proof["content"]["amount_cents"] -= 1
    elif mode == "bank_type":
        proof["source_type"] = "SYNTHETIC_USER_DECLARATION"
    elif mode == "bank_identity":
        proof["content"]["transaction_id"] = str(UUID(int=999))
    elif mode == "time":
        transaction["occurred_at"] = (NOW - timedelta(days=90)).isoformat()
        proof["content"]["occurred_at"] = transaction["occurred_at"]
    proof["content_hash"] = configuration_hash(proof["content"])
    data = data.model_copy(
        update={"base": data.base.model_copy(update={"original_inventory": inventory})}
    )
    with pytest.raises((ValueError, StopIteration)):
        _funding_destination(data, position, content)


def test_original_maturity_action_stays_in_historical_and_unresolved_denominator() -> None:
    def mutation(raw: dict[str, Any]) -> None:
        raw["original_actual_input"]["base"]["original_inventory"]["action_plans"].append(
            {
                "id": str(UUID(int=889)),
                "user_id": str(USER),
                "action_type": "ASSET_MATURITY",
                "request": {},
                "status": "UNKNOWN",
            }
        )
        raw["original_action_ids"] = [str(UUID(int=889))]
        raw["original_commands"] = [{"action_id": str(UUID(int=889))}]

    result = derive_recovery_producers(changed(fixture(), mutation))
    assert result.original_action_ids == result.unresolved_original_action_ids == [UUID(int=889)]
    assert not result.recovery_family_complete and not result.handled_unsupported_codes


@pytest.mark.parametrize("table", ["bank_operations", "simulated_bank_redemptions"])
def test_orphan_original_redemption_does_not_disappear_from_complete_family(table: str) -> None:
    def mutation(raw: dict[str, Any]) -> None:
        raw["original_actual_input"]["base"]["original_inventory"][table].append(
            {
                "id": str(UUID(int=892)),
                "user_id": str(USER),
                "action_plan_id": str(UUID(int=893)),
                "operation_type": "REDEEM_ASSET",
            }
        )

    result = derive_recovery_producers(changed(fixture(), mutation))
    assert not result.recovery_family_complete and not result.handled_unsupported_codes
    assert any("WITHOUT_CAPTURED_ACTION" in reason for reason in result.reasons)


def original_trace(data: RecoveryActionSetInput) -> DecisionTrace:
    raw = data.original_actual_input.base.original_inventory
    result = derive_recovery_producers(data)
    sources = [
        TraceEvidence(
            id=UUID(row["id"]),
            user_id=USER,
            evidence_level=cast(Any, row["evidence_level"]),
            source_type=row["source_type"],
            source_ref=row["source_ref"],
            content=row["content"],
            content_hash=row["content_hash"],
            captured_content_hash=row["content_hash"],
            content_integrity="VERIFIED",
            status_at_decision="VALID",
            observed_at=datetime.fromisoformat(row["observed_at"]),
            valid_from=datetime.fromisoformat(row["valid_from"]),
            valid_to=None,
        )
        for row in raw["evidence_items"]
    ]
    policies = [
        TracePolicy(
            id=UUID(row["id"]),
            user_id=USER,
            policy_id=UUID(row["policy_id"]),
            version_number=row["version_number"],
            configuration=row["configuration"],
            configuration_hash=row["content_hash"],
            captured_configuration_hash=row["content_hash"],
            configuration_integrity="VERIFIED",
            status_at_decision="ACTIVE",
            confirmed_at=datetime.fromisoformat(row["confirmed_at"]),
            valid_from=datetime.fromisoformat(row["valid_from"]),
            valid_to=None,
        )
        for row in raw["policy_versions"]
    ]
    return build_trace(
        run_id=UUID(int=990),
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        algorithm_versions={"recovery_action_producers": ALGORITHM},
        inputs={"recovery_action_set_input": data.model_dump(mode="json")},
        sources=sources,
        policies=policies,
        outcome={"recovery_action_set_result": result.model_dump(mode="json")},
    )


def test_exact_new_frozen_trace_recomputes_family_and_original_sources() -> None:
    data = fixture()
    assert verify_frozen_recovery_producers(original_trace(data)) == derive_recovery_producers(data)


@pytest.mark.parametrize("mode", ["missing_source", "old_algorithm", "result"])
def test_frozen_trace_cannot_relabel_old_algorithm_or_drop_required_originals(mode: str) -> None:
    trace = original_trace(fixture())
    fields = trace.model_dump(exclude={"input_hash", "outcome_hash", "trace_hash"})
    if mode == "missing_source":
        fields["sources"] = [row for row in trace.sources if row.id != UUID(int=612)]
    elif mode == "old_algorithm":
        fields["algorithm_versions"] = {
            "global_action_set": "full-policy-action-set-boundary-actual-v2"
        }
    else:
        fields["outcome"]["recovery_action_set_result"]["bank_authority"] = True
    with pytest.raises(ValueError):
        verify_frozen_recovery_producers(build_trace(**fields))
