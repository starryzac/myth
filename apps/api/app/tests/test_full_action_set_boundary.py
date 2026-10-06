"""Synthetic direct risks, not original financial experiments or runtime acceptance."""

import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid5

import pytest
from app.domain.autonomy_types import AuthorityAssessment, AutonomyFacts
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence, TracePolicy
from app.domain.execution import revalidate_execution
from app.domain.execution_types import CashUse
from app.domain.full_action_set_boundary import (
    ALGORITHM,
    REQUIRED_INVENTORY,
    ActionSetInput,
    CandidateInput,
    GlobalBoundaryObservation,
    GlobalBoundaryObserveRequest,
    compare_action_sets,
    derive_action_set,
    required_producer_keys,
    unsupported_producers,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_boundary as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_execution_domain import EVIDENCE, NOW, POLICY, USER, VERSION, goal_example
from pydantic import ValidationError

EPOCH = UUID(int=20204)
EVIDENCE_CONTENT = {"accepted": True, "purpose": "SYNTHETIC_DIRECT_TEST_ONLY"}


def fixture(amount: int = 300) -> ActionSetInput:
    effect, context = goal_example()
    effect = effect.model_copy(
        update={
            "amount_cents": amount,
            "cash_uses": [CashUse(account_id=effect.cash_uses[0].account_id, amount_cents=amount)],
            "income_uses": [
                use.model_copy(update={"amount_cents": amount}) for use in effect.income_uses
            ],
        }
    )
    version = context.versions[0]
    inventory: dict[str, list[dict[str, Any]]] = {name: [] for name in REQUIRED_INVENTORY}
    inventory["users"] = [{"id": str(USER), "is_simulated": True}]
    inventory["accounts"] = [
        {
            "id": str(cash.account_id),
            "user_id": str(USER),
            "balance_cents": cash.balance_cents,
            "account_type": cash.account_type,
        }
        for cash in context.snapshot.cash_accounts
    ]
    inventory["policies"] = [{"id": str(POLICY), "user_id": str(USER), "status": "ACTIVE"}]
    inventory["policy_versions"] = [
        {
            "id": str(VERSION),
            "user_id": str(USER),
            "policy_id": str(POLICY),
            "version_number": 1,
            "configuration": version.configuration,
            "content_hash": version.content_hash,
            "valid_from": version.valid_from.isoformat() if version.valid_from else None,
            "valid_until": version.valid_until.isoformat() if version.valid_until else None,
        }
    ]
    inventory["goals"] = [
        {"id": str(effect.goal_id), "user_id": str(USER), "policy_id": str(POLICY)}
    ]
    inventory["evidence_items"] = [
        {
            "id": str(EVIDENCE),
            "user_id": str(USER),
            "content": EVIDENCE_CONTENT,
            "content_hash": configuration_hash(EVIDENCE_CONTENT),
            "status": "VALID",
            "source_type": "SYNTHETIC_POLICY",
            "source_ref": "synthetic:policy",
            "evidence_level": "USER_CONFIRMED_POLICY",
        }
    ]
    basis = {
        "user_id": str(USER),
        "as_of": NOW.isoformat(),
        "snapshot": context.snapshot.model_dump(mode="json"),
        "evidence": [{"id": str(EVIDENCE), "hash": configuration_hash(EVIDENCE_CONTENT)}],
    }
    digest = configuration_hash(basis)
    facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type="ALLOCATE_GOAL",
        initiation="CONFIRMED_POLICY",
        authority=AuthorityAssessment(
            status="AUTHORIZED", policy_version_ids=[VERSION], evidence_ids=[EVIDENCE]
        ),
        effect=effect,
        validation=revalidate_execution(effect, context),
        source_evidence_ids=[EVIDENCE],
        source_context_hash=digest,
    )
    return ActionSetInput(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        original_inventory=inventory,
        inventory_reasons=[],
        expected_candidate_keys=required_producer_keys(inventory),
        candidates=[
            CandidateInput(
                candidate_key="goal:" + str(effect.goal_id), facts=facts, execution_context=context
            )
        ],
        financial_input_hash=digest,
        financial_basis=basis,
        audit_verified=True,
        source_reasons=[],
        unsupported_producers=[],
    )


def trace_for(
    data: ActionSetInput, *, key: str = "synthetic-original", previous: DecisionTrace | None = None
) -> DecisionTrace:
    request = GlobalBoundaryObserveRequest(
        expected_epoch_id=data.epoch_id,
        previous_observation_run_id=previous.run_id if previous else None,
        idempotency_key=key,
    )
    identity = uuid5(service.NAMESPACE, f"{data.user_id}:{data.epoch_id}:{key}")
    snapshot = derive_action_set(data)
    before = service.verify_frozen_action_set_trace(previous).snapshot if previous else None
    kind, semantic = compare_action_sets(before, snapshot)
    original = GlobalBoundaryObservation(
        user_id=data.user_id,
        epoch_id=data.epoch_id,
        observation_run_id=identity,
        previous_observation_run_id=request.previous_observation_run_id,
        original_request=request,
        request_hash=configuration_hash(request.model_dump(mode="json")),
        snapshot=snapshot,
        kind=kind,
        semantic_key=semantic,
        requires_user_attention=kind == "BoundaryCrossed",
        previous_snapshot_hash=before.snapshot_hash if before else None,
        previous_action_set_signature=before.action_set_signature if before else None,
        global_action_set_complete=snapshot.global_action_set_complete
        and (before is None or before.global_action_set_complete),
    )
    context = data.candidates[0].execution_context
    assert context is not None
    version = context.versions[0]
    source = TraceEvidence(
        id=EVIDENCE,
        user_id=USER,
        evidence_level="USER_CONFIRMED_POLICY",
        source_type="SYNTHETIC_POLICY",
        source_ref="synthetic:policy",
        content=EVIDENCE_CONTENT,
        content_hash=configuration_hash(EVIDENCE_CONTENT),
        captured_content_hash=configuration_hash(EVIDENCE_CONTENT),
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=NOW - timedelta(days=2),
        valid_from=NOW - timedelta(days=2),
    )
    policy = TracePolicy(
        id=VERSION,
        user_id=USER,
        policy_id=POLICY,
        version_number=1,
        configuration=version.configuration,
        configuration_hash=version.content_hash,
        captured_configuration_hash=version.content_hash,
        configuration_integrity="VERIFIED",
        status_at_decision="ACTIVE",
        confirmed_at=version.confirmed_at,
        valid_from=version.valid_from,
        valid_to=version.valid_until,
    )
    return build_trace(
        run_id=identity,
        user_id=data.user_id,
        phase="EVALUATION",
        as_of=data.as_of,
        parent_run_id=request.previous_observation_run_id,
        action_id=None,
        algorithm_versions={"global_action_set": ALGORITHM},
        inputs={
            "original_request": request.model_dump(mode="json"),
            "action_set_input": data.model_dump(mode="json"),
            "previous_snapshot": before.model_dump(mode="json") if before else None,
        },
        sources=[source],
        policies=[policy],
        constraints=[],
        candidates=[],
        outcome={
            "global_boundary_observation": original.model_dump(mode="json"),
            "decision_status": "COMPUTED" if kind else "UNKNOWN",
        },
    )


def test_original_deterministic_goal_amount_and_risk_signature_not_log_label() -> None:
    data = fixture()
    value = derive_action_set(data)
    assert value.global_action_set_complete and value.status == "COMPLETE"
    assert value.candidates[0].state == "INCLUDED" and value.candidates[0].amount_cents == 300
    assert value.candidates[0].autonomy_level == "AUTO_EXECUTE"
    assert compare_action_sets(None, value) == ("BoundaryObserved", None)
    changed = derive_action_set(fixture(200))
    kind, semantic = compare_action_sets(value, changed)
    assert kind == "BoundaryCrossed" and semantic is not None
    assert compare_action_sets(value, changed)[1] == semantic
    assert not value.bank_authority and not value.financial_write and not value.grants_authority


def test_numeric_raw_balance_change_without_action_change_only_observed() -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_inventory)
    raw["accounts"][0]["balance_cents"] += 100
    row = data.candidates[0]
    assert (
        row.execution_context is not None and row.facts is not None and row.facts.effect is not None
    )
    context = row.execution_context.model_copy(
        update={
            "snapshot": row.execution_context.snapshot.model_copy(
                update={
                    "cash_accounts": [
                        row.execution_context.snapshot.cash_accounts[0].model_copy(
                            update={"balance_cents": 1100}
                        ),
                        row.execution_context.snapshot.cash_accounts[1],
                    ]
                }
            )
        }
    )
    basis = {**data.financial_basis, "snapshot": context.snapshot.model_dump(mode="json")}
    digest = configuration_hash(basis)
    row = row.model_copy(
        update={
            "execution_context": context,
            "facts": row.facts.model_copy(
                update={
                    "validation": revalidate_execution(row.facts.effect, context),
                    "source_context_hash": digest,
                }
            ),
        }
    )
    after = derive_action_set(
        data.model_copy(
            update={
                "original_inventory": raw,
                "financial_basis": basis,
                "financial_input_hash": digest,
                "candidates": [row],
            }
        )
    )
    before = derive_action_set(data)
    assert before.original_inventory_hash != after.original_inventory_hash
    assert before.action_set_signature == after.action_set_signature
    assert compare_action_sets(before, after)[0] == "BoundaryObserved"


@pytest.mark.parametrize(
    "risk",
    [
        "missing-table",
        "foreign-owner",
        "missing-source",
        "source-hash",
        "source-clock",
        "source-basis",
        "policy-binding",
        "wrong-action",
        "omitted-candidate",
        "duplicate-candidate",
        "audit",
        "source-issue",
        "capacity",
        "inactive-lie",
        "full-producer",
        "hidden-full-producer",
        "dynamic-producer",
    ],
)
def test_missing_or_unsupported_never_becomes_empty_complete_set(risk: str) -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_inventory)
    row = data.candidates[0]
    assert row.facts is not None and row.facts.effect is not None
    if risk == "missing-table":
        raw.pop("bank_operations")
    elif risk == "foreign-owner":
        raw["goals"][0]["user_id"] = str(UUID(int=999))
    elif risk == "missing-source":
        raw["evidence_items"] = []
    elif risk == "source-hash":
        raw["evidence_items"][0]["content_hash"] = "f" * 64
    elif risk == "source-clock":
        data = data.model_copy(
            update={
                "candidates": [
                    row.model_copy(
                        update={
                            "facts": row.facts.model_copy(
                                update={"as_of": NOW + timedelta(seconds=1)}
                            )
                        }
                    )
                ]
            }
        )
    elif risk == "source-basis":
        data = data.model_copy(update={"financial_input_hash": "b" * 64})
    elif risk == "policy-binding":
        raw["policy_versions"][0]["content_hash"] = "e" * 64
    elif risk == "wrong-action":
        data = data.model_copy(
            update={
                "candidates": [row.model_copy(update={"candidate_key": "purchase:" + str(POLICY)})],
                "expected_candidate_keys": ["purchase:" + str(POLICY)],
            }
        )
    elif risk == "omitted-candidate":
        data = data.model_copy(update={"candidates": [], "expected_candidate_keys": []})
    elif risk == "duplicate-candidate":
        data = data.model_copy(update={"candidates": [row, row]})
    elif risk == "audit":
        data = data.model_copy(update={"audit_verified": False})
    elif risk == "source-issue":
        data = data.model_copy(update={"source_reasons": ["INDEPENDENT_BANK_MISSING"]})
    elif risk == "capacity":
        raw["accounts"] = [
            {"id": str(UUID(int=i + 1000)), "user_id": str(USER)} for i in range(201)
        ]
    elif risk == "inactive-lie":
        data = data.model_copy(
            update={
                "candidates": [
                    CandidateInput(candidate_key=row.candidate_key, excluded_by_current_policy=True)
                ]
            }
        )
    elif risk in {"full-producer", "hidden-full-producer"}:
        raw["full_policies"] = [
            {
                "id": str(UUID(int=90)),
                "user_id": str(USER),
                "epoch_id": str(EPOCH),
                "status": "CONFIRMED",
                "template_name": "AssetAllocationPolicy",
            }
        ]
        if risk == "full-producer":
            data = data.model_copy(
                update={"unsupported_producers": unsupported_producers(raw, EPOCH)}
            )
    else:
        raw["evidence_items"].append(
            {
                "id": str(UUID(int=91)),
                "user_id": str(USER),
                "source_type": "FULL_GOAL_MODEL_V1",
                "source_ref": "original:model",
                "status": "VALID",
            }
        )
        data = data.model_copy(update={"unsupported_producers": unsupported_producers(raw, EPOCH)})
    value = derive_action_set(data.model_copy(update={"original_inventory": raw}))
    assert (
        value.status == "UNKNOWN"
        and value.action_set_signature is None
        and not value.global_action_set_complete
    )
    assert compare_action_sets(derive_action_set(fixture()), value) == (None, None)


def test_actual_inactive_policy_is_verified_empty_not_unknown() -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_inventory)
    raw["policies"][0]["status"] = "SUSPENDED"
    value = derive_action_set(
        data.model_copy(
            update={
                "original_inventory": raw,
                "candidates": [
                    CandidateInput(
                        candidate_key=data.candidates[0].candidate_key,
                        excluded_by_current_policy=True,
                    )
                ],
            }
        )
    )
    assert value.status == "COMPLETE" and value.candidates[0].state == "EXCLUDED"
    assert compare_action_sets(value, derive_action_set(fixture()))[0] == "BoundaryCrossed"


@pytest.mark.parametrize("risk", ["owner", "epoch", "backward-clock", "unknown-before"])
def test_comparison_cannot_join_different_original_scope(risk: str) -> None:
    before = derive_action_set(fixture())
    if risk == "owner":
        before = before.model_copy(update={"user_id": UUID(int=99)})
    elif risk == "epoch":
        before = before.model_copy(update={"epoch_id": UUID(int=99)})
    elif risk == "backward-clock":
        before = before.model_copy(update={"as_of": NOW + timedelta(seconds=1)})
    else:
        before = before.model_copy(update={"global_action_set_complete": False})
    assert compare_action_sets(before, derive_action_set(fixture())) == (None, None)


def test_original_frozen_trace_reproduces_exact_math_and_source_refs() -> None:
    first = trace_for(fixture())
    second = trace_for(fixture(200), key="second-original", previous=first)
    value = service.verify_frozen_action_set_trace(second)
    assert value.kind == "BoundaryCrossed" and value.requires_user_attention
    assert value.previous_observation_run_id == first.run_id


@pytest.mark.parametrize(
    "risk", ["amount", "previous-signature", "owner", "request", "source", "policy", "run-id"]
)
def test_rehashed_fake_outcome_or_refs_cannot_validate(risk: str) -> None:
    trace = trace_for(fixture())
    values: dict[str, Any] = {
        name: getattr(trace, name)
        for name in (
            "run_id",
            "user_id",
            "phase",
            "as_of",
            "parent_run_id",
            "action_id",
            "algorithm_versions",
            "inputs",
            "sources",
            "policies",
            "constraints",
            "candidates",
            "outcome",
        )
    }
    values["outcome"] = copy.deepcopy(values["outcome"])
    if risk == "amount":
        values["outcome"]["global_boundary_observation"]["snapshot"]["candidates"][0][
            "amount_cents"
        ] = 999
    elif risk == "previous-signature":
        values["outcome"]["global_boundary_observation"]["previous_action_set_signature"] = "a" * 64
    elif risk == "owner":
        values["user_id"] = UUID(int=999)
    elif risk == "request":
        values["inputs"] = copy.deepcopy(values["inputs"])
        values["inputs"]["original_request"]["idempotency_key"] = "different-key"
    elif risk == "source":
        values["sources"] = []
    elif risk == "policy":
        values["policies"] = []
    else:
        values["run_id"] = UUID(int=999)
    with pytest.raises(ValueError):
        service.verify_frozen_action_set_trace(build_trace(**values))


def test_public_request_uuid_json_is_strict_no_financial_inputs() -> None:
    body = {"expected_epoch_id": str(EPOCH), "idempotency_key": "original-id"}
    assert (
        GlobalBoundaryObserveRequest.model_validate_json(json.dumps(body)).expected_epoch_id
        == EPOCH
    )
    for field in (
        "amount_cents",
        "authority",
        "user_id",
        "clock",
        "original_inventory",
        "action_set_signature",
        "facts",
    ):
        with pytest.raises(ValidationError):
            GlobalBoundaryObserveRequest.model_validate_json(json.dumps({**body, field: 1}))


@pytest.mark.parametrize("risk", ["missing-parent", "different-parent-original", "cycle"])
def test_read_requires_actual_original_ancestry_not_only_saved_signatures(
    monkeypatch: pytest.MonkeyPatch, risk: str
) -> None:
    first = trace_for(fixture())
    second = trace_for(fixture(200), key="second", previous=first)
    if risk == "different-parent-original":
        first = trace_for(fixture(250))
    elif risk == "cycle":
        raw = second.model_dump(mode="json")
        raw["inputs"]["previous_snapshot"] = raw["outcome"]["global_boundary_observation"][
            "snapshot"
        ]
        raw["parent_run_id"] = str(second.run_id)
        # Even a rehashed cycle must fail the original envelope/bindings, never
        # become a successful current observation.
        values = {
            name: getattr(second, name)
            for name in (
                "run_id",
                "user_id",
                "phase",
                "as_of",
                "parent_run_id",
                "action_id",
                "algorithm_versions",
                "inputs",
                "sources",
                "policies",
                "constraints",
                "candidates",
                "outcome",
            )
        }
        values["parent_run_id"] = second.run_id
        with pytest.raises(ValueError, match="own parent"):
            build_trace(**values)
        return
    originals = {second.run_id: second, first.run_id: first}
    if risk == "missing-parent":
        originals.pop(first.run_id)

    def read(session: Any, user_id: UUID, run_id: UUID, now: Any) -> Any:
        assert user_id == USER
        return SimpleNamespace(
            trace=originals.get(run_id), completeness="COMPLETE", audit_chain_status="VALID"
        )

    monkeypatch.setattr(service, "get_decision_trace", read)
    with pytest.raises(PolicyLifecycleError) as error:
        service.read_global_boundary_observation(cast(Any, object()), USER, second.run_id, NOW)
    assert error.value.code == "GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED"


def test_global_intervention_source_requires_complete_real_parent_and_attention_from_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = trace_for(fixture())
    second = trace_for(fixture(200), key="second", previous=first)
    originals = {first.run_id: first, second.run_id: second}
    monkeypatch.setattr(
        service,
        "get_decision_trace",
        lambda session, user, run_id, now: SimpleNamespace(
            trace=originals[run_id], completeness="COMPLETE", audit_chain_status="VALID"
        ),
    )
    source = service.global_boundary_intervention_source(
        cast(Any, object()), USER, second.run_id, NOW
    )
    assert source.attention and source.boundary is not None
    assert (
        source.boundary["kind"] == "BoundaryCrossed"
        and source.boundary["global_action_set_complete"]
    )
    with pytest.raises(PolicyLifecycleError) as error:
        service.global_boundary_intervention_source(cast(Any, object()), USER, first.run_id, NOW)
    assert error.value.code == "GLOBAL_BOUNDARY_SOURCE_UNKNOWN"


def test_manual_position_no_policy_exclusion_keeps_manual_scope_uncovered() -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_inventory)
    identity = str(UUID(int=1234))
    raw["asset_positions"] = [
        {"id": identity, "user_id": str(USER), "policy_version_id": None, "status": "HELD"}
    ]
    candidate = CandidateInput(
        candidate_key="redemption:" + identity, excluded_by_current_policy=True
    )
    value = derive_action_set(
        data.model_copy(
            update={
                "original_inventory": raw,
                "expected_candidate_keys": required_producer_keys(raw),
                "candidates": [*data.candidates, candidate],
            }
        )
    )
    assert value.global_action_set_complete and value.candidates[1].state == "EXCLUDED"
    assert value.arbitrary_manual_intents_covered is False
    raw["asset_positions"][0].pop("policy_version_id")
    missing = derive_action_set(
        data.model_copy(
            update={
                "original_inventory": raw,
                "expected_candidate_keys": required_producer_keys(raw),
                "candidates": [*data.candidates, candidate],
            }
        )
    )
    assert missing.status == "UNKNOWN" and missing.action_set_signature is None
