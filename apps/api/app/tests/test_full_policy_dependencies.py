"""Synthetic declaration-source and cycle risks, not financial or PG acceptance."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.domain.full_policy_configuration import validate_full_configuration
from app.domain.full_policy_dependencies import (
    DependencyPolicyOriginal,
    DependencyReviewInput,
    review_dependencies,
)
from app.domain.policy_configuration import configuration_hash

USER, EPOCH, FIRST, SECOND = (UUID(int=i) for i in (600, 601, 602, 603))
NOW = datetime(2026, 10, 6, tzinfo=UTC)


def reference(identity: UUID, *, digest: str = "a" * 64) -> dict[str, Any]:
    return {
        "role": "protected_policy",
        "kind": "FULL_POLICY",
        "id": str(identity),
        "binding_hash": digest,
        "snapshot": {
            "id": str(identity),
            "user_id": str(USER),
            "status": "ACTIVE",
        },
        "version_ids": [str(UUID(int=610))],
    }


def original(identity: UUID, dependencies: list[UUID]) -> DependencyPolicyOriginal:
    config = validate_full_configuration(
        "DatedExpensePolicy",
        {
            "type": "dated_expense",
            "name": "合成依赖风险",
            "window": {"start": "2026-11-01", "end": "2026-11-02"},
            "amount": {"min_cents": 100, "target_cents": 100, "max_cents": 100},
            "must_not_reduce_policy_ids": list(map(str, dependencies)),
        },
    )
    refs = [reference(value) for value in dependencies]
    return DependencyPolicyOriginal(
        policy_id=identity,
        epoch_id=EPOCH,
        version_id=UUID(int=identity.int + 100),
        configuration_hash=configuration_hash(config),
        configuration=config,
        name=config["name"],
        template_name="DatedExpensePolicy",
        effective_status="ACTIVE",
        reference_validation="CURRENT",
        planning_confirmation_valid=True,
        recorded_references=refs,
        current_references=deepcopy(refs),
    )


def fixture(*, cycle: bool = False) -> DependencyReviewInput:
    return DependencyReviewInput(
        user_id=USER,
        epoch_id=EPOCH,
        selected_policy_id=FIRST,
        as_of=NOW,
        current_policy_count=2,
        current_policy_ids=[FIRST, SECOND],
        archived_policy_count=3,
        policies=[original(FIRST, [SECOND]), original(SECOND, [FIRST] if cycle else [])],
        audit_status="VALID",
        audit_source_hash="b" * 64,
    )


def test_current_source_has_explicit_complete_denominator_and_no_authority() -> None:
    data = fixture()
    result = review_dependencies(data)
    assert result.status == "COMPLETE_CURRENT_DECLARATION_GRAPH"
    assert not result.review_required and not result.cyclic_components
    assert result.current_policy_count == result.captured_policy_count == 2
    assert result.archived_policy_count == 3 and result.edges[0].status == "UNCHANGED"
    assert result.edges[0].original_reference == data.policies[0].recorded_references[0]
    assert result.input_hash == configuration_hash(data.model_dump(mode="json"))
    assert result.review_hash == configuration_hash(
        result.model_dump(mode="json", exclude={"review_hash"})
    )
    assert not result.bank_authority and not result.financial_conflict_solver_applied
    assert not result.position_and_boundary_recovery_verified


def test_mvp_reference_uses_verified_effective_state_without_rewriting_original() -> None:
    data = fixture()
    raw = data.model_dump(mode="json")
    row = raw["policies"][0]
    for key in ("recorded_references", "current_references"):
        row[key][0]["kind"] = "MVP_POLICY"
    row["reference_effective_statuses"] = {str(SECOND): "EXPIRED"}
    parsed = DependencyReviewInput.model_validate_json(json.dumps(raw))
    result = review_dependencies(parsed)
    assert result.status == "COMPLETE_CURRENT_DECLARATION_GRAPH" and result.review_required
    assert result.edges[0].current_target_status == "EXPIRED"
    assert result.edges[0].current_reference == row["current_references"][0]
    assert result.edges[0].current_reference is not None
    assert result.edges[0].current_reference["snapshot"]["status"] == "ACTIVE"
    row["reference_effective_statuses"] = {}
    with pytest.raises(ValueError, match="effective-state denominator"):
        review_dependencies(DependencyReviewInput.model_validate_json(json.dumps(raw)))


def test_same_uuid_mvp_reference_is_not_an_edge_to_a_full_policy_root() -> None:
    raw = fixture(cycle=True).model_dump(mode="json")
    first = raw["policies"][0]
    for key in ("recorded_references", "current_references"):
        first[key][0]["kind"] = "MVP_POLICY"
    first["reference_effective_statuses"] = {str(SECOND): "ACTIVE"}
    result = review_dependencies(DependencyReviewInput.model_validate_json(json.dumps(raw)))
    assert result.status == "COMPLETE_CURRENT_DECLARATION_GRAPH"
    assert not result.cyclic_components and not result.review_required
    assert len(result.edges) == 2 and result.edges[0].kind == "MVP_POLICY"
    assert result.edges[0].target_id == SECOND


@pytest.mark.parametrize("cycle", ["mutual", "self"])
def test_cycles_are_exact_declared_edges_and_review_only(cycle: str) -> None:
    data = fixture(cycle=True)
    if cycle == "self":
        data = data.model_copy(
            update={"policies": [original(FIRST, [FIRST]), original(SECOND, [])]}
        )
    result = review_dependencies(data)
    assert result.status == "COMPLETE_CURRENT_DECLARATION_GRAPH" and result.review_required
    assert len(result.cyclic_components) == 1
    found = result.cyclic_components[0]
    assert found.example_path[0] == found.example_path[-1] == FIRST
    assert found.policy_ids == ([FIRST, SECOND] if cycle == "mutual" else [FIRST])
    actual_edges = {(row.source_policy_id, row.target_id) for row in result.edges}
    assert all(
        edge in actual_edges
        for edge in zip(found.example_path, found.example_path[1:], strict=False)
    )
    assert not found.financial_infeasibility_proven


@pytest.mark.parametrize("change", ["version", "unavailable", "revoked"])
def test_changed_or_unavailable_dependency_is_never_a_reusable_confirmation(change: str) -> None:
    data = fixture()
    if change == "version":
        data.policies[0].current_references[0]["binding_hash"] = "c" * 64  # type: ignore[index]
    elif change == "unavailable":
        data = data.model_copy(
            update={
                "policies": [
                    data.policies[0].model_copy(update={"current_references": None}),
                    data.policies[1],
                ]
            }
        )
    else:
        data = data.model_copy(
            update={
                "policies": [
                    data.policies[0],
                    data.policies[1].model_copy(
                        update={"effective_status": "REVOKED", "planning_confirmation_valid": False}
                    ),
                ]
            }
        )
    result = review_dependencies(data)
    assert result.review_required
    assert not result.bank_authority
    if change == "unavailable":
        assert result.status == "UNKNOWN" and result.edges[0].status == "UNAVAILABLE"
    elif change == "version":
        assert result.edges[0].status == "CHANGED"
    else:
        assert result.edges[0].current_target_status == "REVOKED"


@pytest.mark.parametrize("change", ["audit", "drop-policy", "missing-count", "capacity"])
def test_incomplete_sources_keep_actual_denominator_unknown(change: str) -> None:
    data = fixture()
    updates: dict[str, Any] = {}
    if change == "audit":
        updates.update(audit_status="INVALID", audit_source_hash=None)
    elif change == "drop-policy":
        updates["policies"] = data.policies[:1]
    elif change == "missing-count":
        updates["current_policy_count"] = 3
    else:
        updates.update(
            current_policy_count=65,
            current_policy_ids=[],
            policies=[],
            source_issues=["DEPENDENCY_POLICY_CAPACITY_EXCEEDED"],
        )
    result = review_dependencies(data.model_copy(update=updates))
    assert result.status == "UNKNOWN" and result.review_required
    assert result.current_policy_count == (
        65 if change == "capacity" else 3 if change == "missing-count" else 2
    )
    assert not result.bank_authority


@pytest.mark.parametrize(
    "change", ["owner", "duplicate", "hash", "missing-ref", "extra-ref", "config", "epoch", "clock"]
)
def test_inconsistent_originals_cannot_be_rehashed_into_a_complete_review(change: str) -> None:
    data = fixture()
    raw = data.model_dump(mode="json")
    row = raw["policies"][0]
    if change == "owner":
        row["current_references"][0]["snapshot"]["user_id"] = str(UUID(int=999))
    elif change == "duplicate":
        row["current_references"].append(deepcopy(row["current_references"][0]))
    elif change == "hash":
        row["configuration_hash"] = "0" * 64
    elif change == "missing-ref":
        row["recorded_references"] = []
        row["current_references"] = []
    elif change == "extra-ref":
        row["current_references"].append(reference(UUID(int=999)))
    elif change == "config":
        row["configuration"]["amount"]["max_cents"] = 0
        row["configuration_hash"] = configuration_hash(row["configuration"])
    elif change == "epoch":
        row["epoch_id"] = str(UUID(int=999))
    else:
        raw["as_of"] = "2026-10-06T00:00:00"
    with pytest.raises(ValueError):
        review_dependencies(
            DependencyReviewInput.model_validate_json(__import__("json").dumps(raw))
        )
