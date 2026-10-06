"""Synthetic producer/source risks only; no financial experiment or bank run."""

import copy
import json
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.domain.autonomy_types import AuthorityAssessment, AutonomyFacts
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence, TracePolicy
from app.domain.execution import revalidate_execution
from app.domain.full_action_set_boundary import (
    REQUIRED_INVENTORY,
    ActionSetInput,
    CandidateInput,
    GlobalBoundaryObserveRequest,
    derive_action_set,
    required_producer_keys,
    unsupported_producers,
)
from app.domain.full_action_set_boundary_full import (
    ALGORITHM,
    NAMESPACE,
    DynamicGoalProducerInput,
    FullActionSetInput,
    FullGlobalBoundaryObservation,
    compare_full_action_sets,
    derive_full_action_set,
)
from app.domain.full_dynamic_goal_execution import build_full_dynamic_goal_effect
from app.domain.multi_goal_allocation import SourceReference
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_boundary_full as service
from app.services.decision_recording import DecisionCapture
from app.services.full_action_set_boundary import ActionSetCapture
from app.tests.test_full_dynamic_goal_execution import (
    BANK,
    CASH,
    EPOCH,
    GOAL,
    INCOME,
    MODEL,
    MONTH,
    NOW,
    OWN,
    POLICY,
    USER,
    VERSION,
)
from app.tests.test_full_dynamic_goal_execution import (
    fixture as dynamic_fixture,
)
from sqlalchemy.orm import Session

CONFIRM = UUID(int=140)


def fixture() -> FullActionSetInput:
    data = dynamic_fixture()
    version = data.context.versions[0]
    assert version.confirmed_at is not None
    confirmation = {
        "user_id": str(USER),
        "policy_id": str(POLICY),
        "version_id": str(VERSION),
        "reviewed_hash": version.content_hash,
        "confirmed_at": version.confirmed_at.isoformat(),
        "accepted": True,
    }
    contents: dict[UUID, dict[str, Any]] = {
        MODEL: data.model_original,
        BANK: {"purpose": "SYNTHETIC_BANK_ORIGINAL_ONLY"},
        OWN: {"purpose": "SYNTHETIC_OWNERSHIP_ONLY"},
        MONTH: {"purpose": "SYNTHETIC_MONTH_ONLY"},
        CONFIRM: confirmation,
    }
    ledger = data.income.model_copy(
        update={
            "origins": tuple(
                row.model_copy(update={"bank_evidence_hash": configuration_hash(contents[BANK])})
                for row in data.income.origins
            )
        }
    )
    contents[INCOME] = ledger.model_dump(mode="json")
    context = data.context.model_copy(
        update={"versions": [version.model_copy(update={"evidence_ids": [CONFIRM]})]}
    )
    data = data.model_copy(
        update={
            "context": context,
            "income": ledger,
            "income_evidence_hash": configuration_hash(contents[INCOME]),
            "source_refs": [
                SourceReference(
                    user_id=USER, evidence_id=identity, content_hash=configuration_hash(content)
                )
                for identity, content in sorted(contents.items())
            ],
        }
    )
    inventory: dict[str, list[dict[str, Any]]] = {name: [] for name in REQUIRED_INVENTORY}
    inventory["users"] = [{"id": str(USER), "is_simulated": True}]
    inventory["accounts"] = [
        {
            "id": str(row.account_id),
            "user_id": str(USER),
            "balance_cents": row.balance_cents,
            "account_type": row.account_type,
        }
        for row in context.snapshot.cash_accounts
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
            "valid_from": version.valid_from.isoformat(),
            "valid_until": None,
            "confirmed_at": version.confirmed_at.isoformat(),
            "confirmation": confirmation,
            "evidence_ids": [str(CONFIRM)],
        }
    ]
    inventory["goals"] = [{"id": str(GOAL), "user_id": str(USER), "policy_id": str(POLICY)}]
    inventory["evidence_items"] = [
        {
            "id": str(identity),
            "user_id": str(USER),
            "status": "VALID",
            "content": content,
            "content_hash": configuration_hash(content),
            "evidence_level": "USER_CONFIRMED_POLICY"
            if identity in {MODEL, CONFIRM}
            else "BANK_CONFIRMED",
            "source_type": "FULL_GOAL_MODEL_V1"
            if identity == MODEL
            else "POLICY_CONFIRMATION"
            if identity == CONFIRM
            else "SYNTHETIC_TOOL_ONLY",
            "source_ref": str(GOAL)
            if identity == MODEL
            else str(VERSION)
            if identity == CONFIRM
            else "synthetic-only",
            "observed_at": NOW.isoformat(),
            "valid_from": (NOW - timedelta(days=2)).isoformat(),
            "valid_until": None,
        }
        for identity, content in sorted(contents.items())
    ]
    basis = {
        "user_id": str(USER),
        "as_of": NOW.isoformat(),
        "snapshot": context.snapshot.model_dump(mode="json"),
        "income": ledger.model_dump(mode="json"),
        "evidence": [
            {"id": str(identity), "hash": configuration_hash(content)}
            for identity, content in sorted(contents.items())
        ],
    }
    authority = AuthorityAssessment(
        status="AUTHORIZED", policy_version_ids=[VERSION], evidence_ids=[CONFIRM]
    )
    # Preserve an old nominal refusal in the base. The full producer must replace
    # it using original typed math, never turn this old label into success.
    effect, _ = build_full_dynamic_goal_effect(data, UUID(int=190))
    facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type="ALLOCATE_GOAL",
        initiation="CONFIRMED_POLICY",
        authority=authority,
        effect=effect,
        validation=revalidate_execution(effect, context),
        source_context_hash=configuration_hash(basis),
        source_evidence_ids=[CONFIRM],
    )
    base = ActionSetInput(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        original_inventory=inventory,
        inventory_reasons=[],
        expected_candidate_keys=required_producer_keys(inventory),
        candidates=[
            CandidateInput(
                candidate_key="goal:" + str(GOAL), facts=facts, execution_context=context
            )
        ],
        financial_input_hash=configuration_hash(basis),
        financial_basis=basis,
        audit_verified=True,
        source_reasons=[],
        unsupported_producers=unsupported_producers(inventory, EPOCH),
    )
    return FullActionSetInput(
        base=base,
        dynamic_goals=[
            DynamicGoalProducerInput(
                candidate_key="goal:" + str(GOAL),
                model_evidence_ids=[MODEL],
                data=data,
                authority=authority,
            )
        ],
    )


def trace_for(data: FullActionSetInput, previous: DecisionTrace | None = None) -> DecisionTrace:
    snapshot = derive_full_action_set(data)
    before = service.verify_frozen_full_action_set_trace(previous).snapshot if previous else None
    body = GlobalBoundaryObserveRequest(
        expected_epoch_id=data.base.epoch_id,
        previous_observation_run_id=previous.run_id if previous else None,
        idempotency_key="synthetic-full:" + snapshot.snapshot_hash[:16],
    )
    identity = uuid5(NAMESPACE, f"{USER}:{EPOCH}:{body.idempotency_key}")
    kind, semantic = compare_full_action_sets(before, snapshot)
    value = FullGlobalBoundaryObservation(
        user_id=USER,
        epoch_id=EPOCH,
        observation_run_id=identity,
        previous_observation_run_id=body.previous_observation_run_id,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        snapshot=snapshot,
        kind=kind,
        semantic_key=semantic,
        requires_user_attention=kind == "BoundaryCrossed",
        previous_snapshot_hash=before.snapshot_hash if before else None,
        previous_action_set_signature=before.action_set_signature if before else None,
        global_action_set_complete=snapshot.global_action_set_complete
        and (before is None or before.global_action_set_complete),
    )
    sources = [
        TraceEvidence(
            id=UUID(row["id"]),
            user_id=USER,
            evidence_level=row["evidence_level"],
            source_type=row["source_type"],
            source_ref=row["source_ref"],
            content=row["content"],
            content_hash=row["content_hash"],
            captured_content_hash=row["content_hash"],
            content_integrity="VERIFIED",
            status_at_decision="VALID",
            observed_at=NOW,
            valid_from=NOW - timedelta(days=2),
        )
        for row in data.base.original_inventory["evidence_items"]
    ]
    original_version = data.base.original_inventory["policy_versions"][0]
    policies = [
        TracePolicy(
            id=VERSION,
            user_id=USER,
            policy_id=POLICY,
            version_number=1,
            configuration=original_version["configuration"],
            configuration_hash=original_version["content_hash"],
            captured_configuration_hash=original_version["content_hash"],
            configuration_integrity="VERIFIED",
            status_at_decision="ACTIVE",
            confirmed_at=NOW - timedelta(days=2),
            valid_from=NOW - timedelta(days=2),
        )
    ]
    return build_trace(
        run_id=identity,
        user_id=USER,
        as_of=NOW,
        phase="EVALUATION",
        parent_run_id=body.previous_observation_run_id,
        action_id=None,
        algorithm_versions={"global_action_set": ALGORITHM},
        inputs={
            "original_request": body.model_dump(mode="json"),
            "full_action_set_input": data.model_dump(mode="json"),
            "previous_snapshot": before.model_dump(mode="json") if before else None,
        },
        sources=sources,
        policies=policies,
        constraints=[],
        candidates=[],
        outcome={
            "full_global_boundary_observation": value.model_dump(mode="json"),
            "decision_status": "COMPUTED" if kind else "UNKNOWN",
        },
    )


def test_actual_dynamic_math_replaces_nominal_and_preserves_original_bytes() -> None:
    data = fixture()
    before = data.model_dump_json()
    old = derive_action_set(data.base)
    assert not old.global_action_set_complete
    result = derive_full_action_set(data)
    assert result.global_action_set_complete, result.reasons
    assert len(result.candidates) == 1 and result.dynamic_candidate_keys == ["goal:" + str(GOAL)]
    assert result.candidates[0].amount_cents == 150000
    assert result.candidates[0].state == "INCLUDED"
    assert result.candidates[0].autonomy_level in {"AUTO_EXECUTE", "ASK_ONCE"}
    assert data.model_dump_json() == before
    assert result.bank_authority is False and result.financial_write is False


@pytest.mark.parametrize(
    "mode",
    [
        "missing_source",
        "hash",
        "model_body",
        "income",
        "owner",
        "epoch",
        "clock",
        "claim",
        "confirmation",
        "authority",
        "drop",
        "duplicate",
        "denominator",
        "raw_owner",
        "audit",
        "missing_table",
        "remaining_family",
        "full_floor",
        "future_source",
    ],
)
def test_no_source_permission_claim_or_family_omission_becomes_complete(mode: str) -> None:
    data = fixture()
    raw = data.model_dump(mode="json")
    source = raw["base"]["original_inventory"]["evidence_items"][0]
    dynamic = raw["dynamic_goals"][0]
    if mode == "missing_source":
        raw["base"]["original_inventory"]["evidence_items"] = [
            row
            for row in raw["base"]["original_inventory"]["evidence_items"]
            if row["id"] != str(BANK)
        ]
    elif mode == "hash":
        source["content_hash"] = "0" * 64
    elif mode == "model_body":
        dynamic["data"]["model_original"]["accepted"] = False
    elif mode == "income":
        raw["base"]["financial_basis"]["income"] = {}
        raw["base"]["financial_input_hash"] = configuration_hash(raw["base"]["financial_basis"])
    elif mode == "owner":
        dynamic["data"]["context"]["user_id"] = str(UUID(int=900))
    elif mode == "epoch":
        dynamic["data"]["epoch_id"] = str(UUID(int=900))
    elif mode == "clock":
        dynamic["data"]["context"]["snapshot"]["as_of"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mode == "claim":
        raw["base"]["original_inventory"]["action_resource_reservations"] = [
            {
                "id": str(UUID(int=901)),
                "user_id": str(USER),
                "status": "RESERVED",
                "resource_kind": "CASH",
                "resource_key": str(CASH),
                "amount_cents": 300000,
                "created_at": NOW.isoformat(),
            }
        ]
    elif mode == "confirmation":
        raw["base"]["original_inventory"]["policy_versions"][0]["confirmation"]["accepted"] = False
    elif mode == "authority":
        dynamic["authority"]["evidence_ids"] = []
    elif mode == "drop":
        raw["dynamic_goals"] = []
    elif mode == "duplicate":
        raw["dynamic_goals"].append(copy.deepcopy(dynamic))
    elif mode == "denominator":
        dynamic["model_evidence_ids"] = []
    elif mode == "raw_owner":
        source["user_id"] = str(UUID(int=900))
    elif mode == "audit":
        raw["base"]["audit_verified"] = False
    elif mode == "missing_table":
        del raw["base"]["original_inventory"]["bank_operations"]
    elif mode == "remaining_family":
        raw["base"]["original_inventory"]["full_policies"] = [
            {
                "id": str(UUID(int=902)),
                "user_id": str(USER),
                "epoch_id": str(EPOCH),
                "status": "ACTIVE",
                "template_name": "AssetAuthorizationPolicy",
            }
        ]
        raw["base"]["unsupported_producers"] = unsupported_producers(
            raw["base"]["original_inventory"], EPOCH
        )
    elif mode == "full_floor":
        raw["base"]["original_inventory"]["full_policies"] = [
            {
                "id": str(UUID(int=902)),
                "user_id": str(USER),
                "epoch_id": str(EPOCH),
                "status": "ACTIVE",
                "template_name": "DatedExpensePolicy",
            }
        ]
        raw["base"]["unsupported_producers"] = unsupported_producers(
            raw["base"]["original_inventory"], EPOCH
        )
    elif mode == "future_source":
        source["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    parsed = FullActionSetInput.model_validate_json(json.dumps(raw))
    result = derive_full_action_set(parsed)
    assert not result.global_action_set_complete and result.status == "UNKNOWN"
    assert result.action_set_signature is None and result.reasons


def test_original_deterministic_nominal_refusal_is_not_used_as_dynamic_grant() -> None:
    data = fixture()
    dynamic = data.dynamic_goals[0]
    assert dynamic.data is not None
    effect, proof = build_full_dynamic_goal_effect(dynamic.data, UUID(int=190))
    assert revalidate_execution(effect, dynamic.data.context).status == "BLOCKED"
    assert (
        revalidate_execution(effect, dynamic.data.context, full_dynamic_goal_proof=proof).status
        == "READY"
    )
    damaged = data.model_copy(update={"dynamic_goals": [dynamic.model_copy(update={"data": None})]})
    assert not derive_full_action_set(damaged).global_action_set_complete


def test_semantic_comparison_retains_owner_epoch_and_complete_set() -> None:
    first = derive_full_action_set(fixture())
    assert compare_full_action_sets(None, first) == ("BoundaryObserved", None)
    assert compare_full_action_sets(first, first)[0] == "BoundaryObserved"
    assert compare_full_action_sets(
        first.model_copy(update={"epoch_id": UUID(int=999)}), first
    ) == (None, None)
    unknown = first.model_copy(update={"global_action_set_complete": False})
    assert compare_full_action_sets(unknown, first) == (None, None)
    changed = first.model_copy(update={"action_set_signature": "0" * 64})
    assert compare_full_action_sets(first, changed)[0] == "BoundaryCrossed"


def test_real_recomputed_dynamic_amount_and_numeric_cash_silence() -> None:
    data = fixture()
    first = derive_full_action_set(data)
    producer = data.dynamic_goals[0]
    assert producer.data is not None
    context = producer.data.context
    snapshot = context.snapshot.model_copy(
        update={
            "cash_accounts": [
                row.model_copy(update={"balance_cents": row.balance_cents + 10000})
                if row.account_id == CASH
                else row
                for row in context.snapshot.cash_accounts
            ]
        }
    )
    raw = copy.deepcopy(data.base.original_inventory)
    for account in raw["accounts"]:
        if account["id"] == str(CASH):
            account["balance_cents"] += 10000
    basis = {**data.base.financial_basis, "snapshot": snapshot.model_dump(mode="json")}
    changed = data.model_copy(
        update={
            "base": data.base.model_copy(
                update={
                    "original_inventory": raw,
                    "financial_basis": basis,
                    "financial_input_hash": configuration_hash(basis),
                }
            ),
            "dynamic_goals": [
                producer.model_copy(
                    update={
                        "data": producer.data.model_copy(
                            update={"context": context.model_copy(update={"snapshot": snapshot})}
                        )
                    }
                )
            ],
        }
    )
    second = derive_full_action_set(changed)
    assert second.global_action_set_complete, second.reasons
    assert second.candidates[0].amount_cents == first.candidates[0].amount_cents == 150000
    assert first.financial_input_hash != second.financial_input_hash
    assert compare_full_action_sets(first, second)[0] == "BoundaryObserved"


def test_current_original_policy_denial_excludes_both_nominal_and_dynamic() -> None:
    data = fixture()
    raw = copy.deepcopy(data.base.original_inventory)
    raw["policies"][0]["status"] = "SUSPENDED"
    changed = data.model_copy(
        update={
            "base": data.base.model_copy(
                update={
                    "original_inventory": raw,
                    "candidates": [
                        CandidateInput(
                            candidate_key="goal:" + str(GOAL), excluded_by_current_policy=True
                        )
                    ],
                }
            ),
            "dynamic_goals": [
                data.dynamic_goals[0].model_copy(
                    update={
                        "data": None,
                        "authority": None,
                        "excluded_by_current_policy": True,
                    }
                )
            ],
        }
    )
    result = derive_full_action_set(changed)
    assert result.global_action_set_complete and len(result.candidates) == 1
    assert result.candidates[0].state == "EXCLUDED" and result.candidates[0].signature is None
    assert compare_full_action_sets(derive_full_action_set(data), result)[0] == "BoundaryCrossed"


def test_frozen_trace_recomputes_math_and_requires_original_dynamic_sources() -> None:
    trace = trace_for(fixture())
    value = service.verify_frozen_full_action_set_trace(trace)
    assert value.global_action_set_complete and value.kind == "BoundaryObserved"
    raw = trace.model_dump(mode="json")
    raw["sources"] = [row for row in raw["sources"] if row["id"] != str(BANK)]
    with pytest.raises(ValueError):
        service.verify_frozen_full_action_set_trace(
            DecisionTrace.model_validate_json(json.dumps(raw))
        )
    raw = trace.model_dump(mode="json")
    raw["outcome"]["full_global_boundary_observation"]["snapshot"]["candidates"][0][
        "amount_cents"
    ] = 100000
    with pytest.raises(ValueError):
        service.verify_frozen_full_action_set_trace(
            DecisionTrace.model_validate_json(json.dumps(raw))
        )


def test_same_session_producer_reads_fresh_originals_and_restores_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    session = MagicMock(spec=Session)
    prior = object()
    session.info = {"bounded_funds_decision_capture": prior}
    base = ActionSetCapture(data.base, derive_action_set(data.base), DecisionCapture())
    producer = data.dynamic_goals[0]
    assert producer.data is not None
    from types import SimpleNamespace

    monkeypatch.setattr(service, "_snapshot", lambda actual: None)
    monkeypatch.setattr(service, "capture_evidence", lambda *args: None)
    monkeypatch.setattr(service, "capture_versions", lambda *args: None)
    monkeypatch.setattr(
        service,
        "read_full_goal_model",
        lambda *args: SimpleNamespace(
            status="VERIFIED",
            evidence_id=MODEL,
            evidence_hash=producer.data.model_evidence_hash,
            base_policy_version_id=VERSION,
        ),
    )
    calls: list[Any] = []

    def read(actual: Session, user_id: UUID, body: Any, now: Any) -> Any:
        calls.append((actual, user_id, body, now))
        return producer.data

    monkeypatch.setattr(service, "read_full_dynamic_goal_inputs", read)
    monkeypatch.setattr(service, "_authority", lambda *args, **kwargs: producer.authority)
    for _ in range(2):
        captured = service.capture_full_action_set(session, USER, NOW, base)
        assert captured.snapshot.global_action_set_complete
        assert captured.inputs.base == base.inputs
        assert session.info["bounded_funds_decision_capture"] is prior
    assert len(calls) == 2 and all(row[0] is session for row in calls)
