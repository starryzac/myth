"""Synthetic source/math and direct API gates only; no PG/finance/corpus evidence."""

import copy
import json
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_action_set_boundary_actual as api
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence, TracePolicy
from app.domain.full_action_set_boundary import CandidateInput, GlobalBoundaryObserveRequest
from app.domain.full_action_set_boundary_actual import (
    ALGORITHM,
    MAX_CAPTURE_BYTES,
    NAMESPACE,
    REQUIRED_TABLES,
    ActualActionSetInput,
    ActualGlobalBoundaryObservation,
    ActualTableCoverage,
    actual_producer_keys,
    compare_actual_action_sets,
    derive_actual_action_set,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_boundary_actual as service
from app.tests.test_full_action_set_asset_producers import fixture as asset_fixture
from app.tests.test_full_action_set_boundary_full import fixture as old_fixture
from app.tests.test_full_dynamic_goal_execution import EPOCH, NOW, POLICY, USER, VERSION
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def coverage(raw: dict[str, list[dict[str, Any]]]) -> list[ActualTableCoverage]:
    return [
        ActualTableCoverage(
            table=key,
            actual_count=len(rows),
            captured_count=len(rows),
            complete=True,
            rows_hash=configuration_hash({"rows": rows}),
        )
        for key, rows in sorted(raw.items())
    ]


def fixture() -> ActualActionSetInput:
    old = old_fixture()
    inventory = copy.deepcopy(old.base.original_inventory)
    inventory.pop("simulated_bank_ledger_heads")
    for name in REQUIRED_TABLES:
        inventory.setdefault(name, [])
    base = old.base.model_copy(
        update={
            "original_inventory": inventory,
            "expected_candidate_keys": actual_producer_keys(inventory),
        }
    )
    return ActualActionSetInput(
        base=base, table_coverage=coverage(inventory), dynamic_goals=old.dynamic_goals, assets=[]
    )


def trace_for(data: ActualActionSetInput, previous: DecisionTrace | None = None) -> DecisionTrace:
    snapshot = derive_actual_action_set(data)
    before = service.verify_frozen_actual_action_set_trace(previous).snapshot if previous else None
    body = GlobalBoundaryObserveRequest(
        expected_epoch_id=data.base.epoch_id,
        previous_observation_run_id=previous.run_id if previous else None,
        idempotency_key="synthetic-full:" + snapshot.snapshot_hash[:16],
    )
    identity = uuid5(NAMESPACE, f"{USER}:{EPOCH}:{body.idempotency_key}")
    kind, semantic = compare_actual_action_sets(before, snapshot)
    value = ActualGlobalBoundaryObservation(
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
            "actual_action_set_input": data.model_dump(mode="json"),
            "previous_snapshot": before.model_dump(mode="json") if before else None,
        },
        sources=sources,
        policies=policies,
        constraints=[],
        candidates=[],
        outcome={
            "actual_global_boundary_observation": value.model_dump(mode="json"),
            "decision_status": "COMPUTED" if kind else "UNKNOWN",
        },
    )


def test_true_tables_and_307_replacement_exceed_old_capacity_without_changing_old_bytes() -> None:
    old = old_fixture()
    old_bytes = old.model_dump_json()
    value = fixture()
    original = value.model_dump_json()
    result = derive_actual_action_set(value)
    assert result.global_action_set_complete, result.reasons
    assert result.algorithm_version == ALGORITHM and len(result.candidates) == 1
    assert result.candidates[0].state == "INCLUDED" and result.candidates[0].amount_cents == 150000
    assert "simulated_bank_ledger_heads" not in value.base.original_inventory
    assert result.bank_authority is False and result.financial_write is False
    assert value.model_dump_json() == original and old.model_dump_json() == old_bytes
    # These are additional actual-shaped synthetic originals, not financial facts.
    inventory = copy.deepcopy(value.base.original_inventory)
    inventory["evidence_items"].extend(
        {
            "id": str(UUID(int=10000 + i)),
            "user_id": str(USER),
            "source_type": "TOOL_ONLY",
            "source_ref": "not-an-oracle",
            "status": "INVALID",
            "content": {"padding": "x" * 1600},
            "content_hash": configuration_hash({"padding": "x" * 1600}),
        }
        for i in range(350)
    )
    enlarged = value.model_copy(
        update={
            "base": value.base.model_copy(update={"original_inventory": inventory}),
            "table_coverage": coverage(inventory),
        }
    )
    assert len(enlarged.base.model_dump_json().encode()) > 524288
    result = derive_actual_action_set(enlarged)
    assert result.global_action_set_complete, result.reasons
    assert result.action_set_signature == derive_actual_action_set(value).action_set_signature


@pytest.mark.parametrize(
    "mode",
    [
        "missing_table",
        "virtual_table",
        "count",
        "coverage",
        "row_hash",
        "duplicate",
        "owner",
        "audit",
        "source",
        "dynamic_drop",
        "dynamic_duplicate",
        "clock",
        "capacity",
    ],
)
def test_complete_is_never_inferred_from_truncated_or_unbound_originals(mode: str) -> None:
    value = fixture()
    raw = value.model_dump(mode="json")
    inventory = raw["base"]["original_inventory"]
    if mode == "missing_table":
        del inventory["simulated_bank_postings"]
    elif mode == "virtual_table":
        inventory["simulated_bank_ledger_heads"] = []
    elif mode == "count":
        raw["table_coverage"][0]["actual_count"] += 1
    elif mode == "coverage":
        raw["table_coverage"] = raw["table_coverage"][1:]
    elif mode == "row_hash":
        raw["table_coverage"][0]["rows_hash"] = "0" * 64
    elif mode == "duplicate":
        inventory["accounts"].append(copy.deepcopy(inventory["accounts"][0]))
    elif mode == "owner":
        inventory["accounts"][0]["user_id"] = str(UUID(int=988))
    elif mode == "audit":
        raw["base"]["audit_verified"] = False
    elif mode == "source":
        inventory["evidence_items"] = []
    elif mode == "dynamic_drop":
        raw["dynamic_goals"] = []
    elif mode == "dynamic_duplicate":
        raw["dynamic_goals"].append(copy.deepcopy(raw["dynamic_goals"][0]))
    elif mode == "clock":
        raw["dynamic_goals"][0]["data"]["context"]["snapshot"]["as_of"] = (
            NOW + timedelta(seconds=1)
        ).isoformat()
    elif mode == "capacity":
        inventory["accounts"] = [
            {"id": str(UUID(int=20000 + i)), "user_id": str(USER)} for i in range(4097)
        ]
    result = derive_actual_action_set(ActualActionSetInput.model_validate_json(json.dumps(raw)))
    assert result.status == "UNKNOWN" and not result.global_action_set_complete
    assert result.action_set_signature is None and result.reasons


def test_16mib_capacity_keeps_missing_result_not_empty_action_set() -> None:
    value = fixture()
    inventory = copy.deepcopy(value.base.original_inventory)
    inventory["external_bank_facts"] = [
        {
            "id": str(UUID(int=8877)),
            "user_id": str(USER),
            "original_payload": "x" * MAX_CAPTURE_BYTES,
        }
    ]
    value = value.model_copy(
        update={
            "base": value.base.model_copy(update={"original_inventory": inventory}),
            "table_coverage": coverage(inventory),
        }
    )
    result = derive_actual_action_set(value)
    assert not result.global_action_set_complete
    assert "ACTUAL_CAPTURE_CAPACITY_EXCEEDED" in result.reasons
    assert (
        value.base.original_inventory["external_bank_facts"][0]["original_payload"]
        == "x" * MAX_CAPTURE_BYTES
    )


def test_asset_per_world_replay_does_not_use_old_global_missing_table_verifier() -> None:
    value = asset_fixture()
    inventory = copy.deepcopy(value.base.original_inventory)
    inventory.pop("simulated_bank_ledger_heads")
    for table in REQUIRED_TABLES:
        inventory.setdefault(table, [])
    base = value.base.model_copy(
        update={
            "original_inventory": inventory,
            "expected_candidate_keys": actual_producer_keys(inventory),
        }
    )
    # The nominal adapter is genuinely absent from this synthetic asset fixture;
    # it remains UNKNOWN while both independent whole producers keep their result.
    actual = ActualActionSetInput(
        base=base, table_coverage=coverage(inventory), dynamic_goals=[], assets=value.producers
    )
    result = derive_actual_action_set(actual)
    whole = [row for row in result.candidates if row.candidate_key.startswith("full-asset:")]
    assert len(whole) == 2 and any(
        row.state == "INCLUDED" and row.autonomy_level == "ASK_ONCE" for row in whole
    )
    assert result.status == "UNKNOWN" and not result.global_action_set_complete
    assert not any("simulated_bank_ledger_heads" in reason for reason in result.reasons)


def test_original_version_boundary_comparison_and_trace_replay() -> None:
    value = fixture()
    first = trace_for(value)
    original = service.verify_frozen_actual_action_set_trace(first)
    assert original.kind == "BoundaryObserved" and original.semantic_key is None
    numeric = value.model_copy(
        update={
            "base": value.base.model_copy(
                update={
                    "financial_basis": {**value.base.financial_basis, "diagnostic_non_economic": 1}
                }
            )
        }
    )
    numeric = numeric.model_copy(
        update={
            "base": numeric.base.model_copy(
                update={"financial_input_hash": configuration_hash(numeric.base.financial_basis)}
            )
        }
    )
    # Dynamic source binding is recomputed from complete original context; this
    # added non-economic diagnostic does not create a financial amount change.
    after = derive_actual_action_set(numeric)
    assert after.global_action_set_complete, after.reasons
    assert compare_actual_action_sets(original.snapshot, after)[0] == "BoundaryObserved"
    raw = copy.deepcopy(value.base.original_inventory)
    raw["policies"][0]["status"] = "SUSPENDED"
    denied = value.model_copy(
        update={
            "base": value.base.model_copy(
                update={
                    "original_inventory": raw,
                    "candidates": [
                        CandidateInput(
                            candidate_key=value.base.expected_candidate_keys[0],
                            excluded_by_current_policy=True,
                        )
                    ],
                }
            ),
            "table_coverage": coverage(raw),
            "dynamic_goals": [
                value.dynamic_goals[0].model_copy(
                    update={"data": None, "authority": None, "excluded_by_current_policy": True}
                )
            ],
        }
    )
    changed = derive_actual_action_set(denied)
    assert changed.global_action_set_complete, changed.reasons
    assert len(changed.candidates) == 1 and changed.candidates[0].state == "EXCLUDED"
    assert compare_actual_action_sets(original.snapshot, changed)[0] == "BoundaryCrossed"
    dirty = first.model_copy(deep=True)
    dirty.algorithm_versions["global_action_set"] = "full-policy-action-set-boundary-v1"
    with pytest.raises(ValueError):
        service.verify_frozen_actual_action_set_trace(dirty)


@pytest.mark.parametrize("mode", ["outcome", "count", "source", "parent", "owner"])
def test_new_verifier_recomputes_complete_original_new_protocol(mode: str) -> None:
    trace = trace_for(fixture())
    original = trace.model_dump(mode="json")
    if mode == "outcome":
        original["outcome"]["actual_global_boundary_observation"]["snapshot"]["candidates"][0][
            "amount_cents"
        ] += 1
    elif mode == "count":
        original["inputs"]["actual_action_set_input"]["table_coverage"][0]["actual_count"] += 1
    elif mode == "source":
        original["sources"] = original["sources"][1:]
    elif mode == "parent":
        original["parent_run_id"] = str(UUID(int=900))
    elif mode == "owner":
        original["user_id"] = str(UUID(int=900))
    # Recompute generic trace hashes: the new replay must reject the business
    # mismatch even when a malicious replacement is generically well hashed.
    with pytest.raises(ValueError):
        dirty = build_trace(
            run_id=UUID(original["run_id"]),
            user_id=UUID(original["user_id"]),
            as_of=trace.as_of,
            phase=trace.phase,
            parent_run_id=UUID(original["parent_run_id"]) if original["parent_run_id"] else None,
            action_id=None,
            algorithm_versions=trace.algorithm_versions,
            inputs=original["inputs"],
            sources=[
                TraceEvidence.model_validate_json(json.dumps(row)) for row in original["sources"]
            ],
            policies=trace.policies,
            constraints=[],
            candidates=[],
            outcome=original["outcome"],
        )
        service.verify_frozen_actual_action_set_trace(dirty)


def test_direct_api_strict_json_identity_and_current_read_has_no_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    user = MagicMock()
    user.id = USER
    app.dependency_overrides[get_demo_user] = lambda: user
    snapshot = derive_actual_action_set(fixture())
    monkeypatch.setattr(api, "read_current_actual_action_set", lambda *_: snapshot)
    observed = service.verify_frozen_actual_action_set_trace(trace_for(fixture()))
    calls: list[GlobalBoundaryObserveRequest] = []

    def observe(
        _engine: Any, _user: UUID, body: GlobalBoundaryObserveRequest, _now: Any
    ) -> ActualGlobalBoundaryObservation:
        calls.append(body)
        return observed

    monkeypatch.setattr(api, "observe_actual_global_boundary", observe)
    from app.api.dependencies import get_engine

    app.dependency_overrides[get_engine] = lambda: MagicMock()
    with TestClient(app) as client:
        assert client.get(
            "/api/v1/boundary/actual-action-set/current"
        ).json() == snapshot.model_dump(mode="json")
        body = observed.original_request.model_dump(mode="json")
        assert (
            client.post("/api/v1/boundary/actual-action-set/observe", json=body).status_code == 200
        )
        assert len(calls) == 1
        for field in ("amount_cents", "now", "user_id", "facts", "authority", "result"):
            assert (
                client.get(
                    "/api/v1/boundary/actual-action-set/current", params={field: "fake"}
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/v1/boundary/actual-action-set/observe", json={**body, field: "fake"}
                ).status_code
                == 422
            )
        assert len(calls) == 1


def test_sql_inventory_reads_actual_counts_complete_columns_and_missing_physical_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)

    def scalar(query: Any, parameters: Any = None) -> Any:
        if parameters is not None:
            return None if parameters["name"].endswith("bank_operations") else parameters["name"]
        return 4098 if "evidence_items" in str(query) else 0

    session.scalar.side_effect = scalar

    def execute(query: Any) -> Any:
        result = MagicMock()
        result.mappings.return_value = (
            [
                {
                    "id": UUID(int=40000 + i),
                    "user_id": USER,
                    "original_null": None,
                    "original_integer": i,
                    "canonical_text": " preserved bytes ",
                }
                for i in range(4097)
            ]
            if "evidence_items" in str(query)
            else []
        )
        return result

    session.execute.side_effect = execute
    raw, reasons, proof = service._inventory(session, USER)
    bytable = {row.table: row for row in proof}
    assert "simulated_bank_ledger_heads" not in raw
    assert (
        bytable["bank_operations"].actual_count is None and not bytable["bank_operations"].complete
    )
    assert (
        bytable["evidence_items"].actual_count == 4098
        and bytable["evidence_items"].captured_count == 4097
        and not bytable["evidence_items"].complete
    )
    assert (
        raw["evidence_items"][0]["original_null"] is None
        and type(raw["evidence_items"][0]["original_integer"]) is int
    )
    assert raw["evidence_items"][0]["canonical_text"] == " preserved bytes "
    assert reasons
    session.commit.assert_not_called()
    session.add.assert_not_called()
