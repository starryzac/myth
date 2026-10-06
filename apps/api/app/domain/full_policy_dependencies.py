"""Current FULL declaration dependencies; cycles request review, never prove insolvency."""

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_policy_configuration import TemplateName, validate_full_configuration
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictBool, StrictInt

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_POLICIES = 64
MAX_REFERENCES = 512
Kind = Literal["GOAL", "ACCOUNT", "EVIDENCE", "MVP_POLICY", "FULL_POLICY"]


class DependencyPolicyOriginal(BoundaryModel):
    policy_id: UUID
    epoch_id: UUID
    version_id: UUID
    configuration_hash: Hash
    configuration: dict[str, Any]
    name: str
    template_name: TemplateName
    effective_status: str
    reference_validation: str
    planning_confirmation_valid: StrictBool
    recorded_references: list[dict[str, Any]]
    current_references: list[dict[str, Any]] | None
    reference_effective_statuses: dict[str, str] = Field(default_factory=dict)
    source_issues: list[str] = Field(default_factory=list)


class DependencyReviewInput(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    selected_policy_id: UUID
    as_of: datetime
    current_policy_count: Annotated[StrictInt, Field(ge=0)]
    current_policy_ids: list[UUID]
    archived_policy_count: Annotated[StrictInt, Field(ge=0)]
    policies: list[DependencyPolicyOriginal]
    audit_status: Literal["VALID", "INVALID", "MISSING"]
    audit_source_hash: Hash | None
    source_issues: list[str] = Field(default_factory=list)


class DependencyEdge(BoundaryModel):
    source_policy_id: UUID
    role: str
    kind: Kind
    target_id: UUID
    status: Literal["UNCHANGED", "CHANGED", "ADDED", "UNAVAILABLE"]
    original_binding_hash: Hash | None
    current_binding_hash: Hash | None
    original_reference: dict[str, Any] | None
    current_reference: dict[str, Any] | None
    current_target_status: str | None
    bank_authority: Literal[False] = False


class DependencyCycle(BoundaryModel):
    policy_ids: list[UUID]
    example_path: list[UUID]
    meaning: Literal["DECLARED_DEPENDENCY_CYCLE_REQUIRES_REVIEW"] = (
        "DECLARED_DEPENDENCY_CYCLE_REQUIRES_REVIEW"
    )
    financial_infeasibility_proven: Literal[False] = False


class FullPolicyDependencyReview(BoundaryModel):
    protocol: Literal["full-policy-dependency-review-v1"] = "full-policy-dependency-review-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    selected_policy_id: UUID
    as_of: datetime
    status: Literal["COMPLETE_CURRENT_DECLARATION_GRAPH", "UNKNOWN"]
    review_required: StrictBool
    current_policy_count: StrictInt
    current_policy_ids: list[UUID]
    captured_policy_count: StrictInt
    archived_policy_count: StrictInt
    policies: list[DependencyPolicyOriginal]
    edges: list[DependencyEdge]
    cyclic_components: list[DependencyCycle]
    reasons: list[str]
    input_hash: Hash
    review_hash: Hash
    bank_authority: Literal[False] = False
    writes_policy_or_finance: Literal[False] = False
    all_template_action_rechecks_supported: Literal[False] = False
    position_and_boundary_recovery_verified: Literal[False] = False
    financial_conflict_solver_applied: Literal[False] = False
    limitations: list[str]


def _refs(refs: list[dict[str, Any]], user: UUID) -> dict[tuple[str, str, UUID], dict[str, Any]]:
    result = {}
    roles = {
        "goal": {"GOAL"},
        "source_account": {"ACCOUNT"},
        "payee_source": {"EVIDENCE"},
        "protected_policy": {"MVP_POLICY", "FULL_POLICY"},
        "asset_policy": {"MVP_POLICY", "FULL_POLICY"},
    }
    for row in refs:
        role, kind = row["role"], row["kind"]
        identity = UUID(row["id"])
        snapshot = row["snapshot"]
        if (
            role not in roles
            or kind not in roles[role]
            or str(identity) != row["id"]
            or snapshot["id"] != row["id"]
            or snapshot["user_id"] != str(user)
            or len(row["binding_hash"]) != 64
            or any(c not in "0123456789abcdef" for c in row["binding_hash"])
            or (role, kind, identity) in result
        ):
            raise ValueError("Dependency reference original identity/owner/hash differs")
        result[role, kind, identity] = row
    return result


def _reference_denominator(
    configuration: dict[str, Any], refs: dict[tuple[str, str, UUID], dict[str, Any]]
) -> None:
    expected: set[tuple[str, UUID]] = set()
    goals = set(configuration.get("goal_ids", [])) | set(configuration.get("source_goal_ids", []))
    if configuration.get("goal_id"):
        goals.add(configuration["goal_id"])
    expected.update(("goal", UUID(value)) for value in goals)
    if configuration.get("source_account_id"):
        expected.add(("source_account", UUID(configuration["source_account_id"])))
    expected.update(
        ("protected_policy", UUID(value))
        for value in configuration.get("must_not_reduce_policy_ids", [])
    )
    if configuration.get("asset_policy_id"):
        expected.add(("asset_policy", UUID(configuration["asset_policy_id"])))
    actual = [(role, identity) for role, _, identity in refs if role != "payee_source"]
    payees = [key for key in refs if key[0] == "payee_source"]
    if (
        len(actual) != len(set(actual))
        or set(actual) != expected
        or len(payees) != bool(configuration.get("source_account_id"))
    ):
        raise ValueError("Declared dependency denominator differs from originals")


def _cycles(graph: dict[UUID, set[UUID]]) -> list[DependencyCycle]:
    """Exact strongly-connected components, plus one deterministic real cycle each."""
    number = 0
    indices: dict[UUID, int] = {}
    low: dict[UUID, int] = {}
    stack: list[UUID] = []
    active: set[UUID] = set()
    components: list[set[UUID]] = []

    def visit(node: UUID) -> None:
        nonlocal number
        indices[node] = low[node] = number
        number += 1
        stack.append(node)
        active.add(node)
        for other in sorted(graph[node], key=str):
            if other not in indices:
                visit(other)
                low[node] = min(low[node], low[other])
            elif other in active:
                low[node] = min(low[node], indices[other])
        if low[node] == indices[node]:
            component: set[UUID] = set()
            while True:
                other = stack.pop()
                active.remove(other)
                component.add(other)
                if other == node:
                    break
            if len(component) > 1 or node in graph[node]:
                components.append(component)

    for node in sorted(graph, key=str):
        if node not in indices:
            visit(node)
    result = []
    for component in sorted(components, key=lambda row: min(map(str, row))):
        first = min(component, key=str)

        def path(
            node: UUID, used: list[UUID], allowed: set[UUID], start: UUID
        ) -> list[UUID] | None:
            for other in sorted(graph[node] & allowed, key=str):
                if other == start:
                    return [*used, start]
                if other not in used:
                    found = path(other, [*used, other], allowed, start)
                    if found:
                        return found
            return None

        example = path(first, [first], component, first)
        if example is None:
            raise ValueError("Strong component has no cycle")
        result.append(DependencyCycle(policy_ids=sorted(component, key=str), example_path=example))
    return result


def review_dependencies(data: DependencyReviewInput) -> FullPolicyDependencyReview:
    if data.as_of.tzinfo is None or data.as_of.utcoffset() is None:
        raise ValueError("Current source clock must be aware")
    if len(data.current_policy_ids) != len(set(data.current_policy_ids)):
        raise ValueError("Current policy identities are duplicated")
    reasons = set(data.source_issues)
    items = {row.policy_id: row for row in data.policies}
    if len(items) != len(data.policies) or any(
        row.epoch_id != data.epoch_id for row in items.values()
    ):
        raise ValueError("Current policy originals are duplicated or cross-epoch")
    if data.current_policy_count != len(data.current_policy_ids) or set(items) != set(
        data.current_policy_ids
    ):
        reasons.add("CURRENT_POLICY_DENOMINATOR_INCOMPLETE")
    if data.selected_policy_id not in items:
        reasons.add("SELECTED_CURRENT_POLICY_ORIGINAL_UNAVAILABLE")
    if data.audit_status != "VALID" or data.audit_source_hash is None:
        reasons.add("CURRENT_AUDIT_ORIGINAL_NOT_VERIFIED")
    edges: list[DependencyEdge] = []
    if len(items) > MAX_POLICIES:
        reasons.add("DEPENDENCY_POLICY_CAPACITY_EXCEEDED")
    reference_count = sum(
        len(row.recorded_references) + len(row.current_references or []) for row in items.values()
    )
    if reference_count > MAX_REFERENCES:
        reasons.add("DEPENDENCY_REFERENCE_CAPACITY_EXCEEDED")
    capacity = len(items) <= MAX_POLICIES and reference_count <= MAX_REFERENCES
    graph: dict[UUID, set[UUID]] = {identity: set() for identity in items}
    review = bool(reasons)
    for row in data.policies:
        if (
            validate_full_configuration(row.template_name, row.configuration) != row.configuration
            or configuration_hash(row.configuration) != row.configuration_hash
        ):
            raise ValueError("Current configuration hash differs")
        reasons.update(row.source_issues)
        if not capacity:
            continue
        old = _refs(row.recorded_references, data.user_id)
        _reference_denominator(row.configuration, old)
        fresh = (
            _refs(row.current_references, data.user_id)
            if row.current_references is not None
            else {}
        )
        if row.current_references is not None:
            _reference_denominator(row.configuration, fresh)
        graph[row.policy_id] = {
            identity
            for role, kind, identity in fresh
            if role in {"protected_policy", "asset_policy"}
            and kind == "FULL_POLICY"
            and identity in items
        }
        mvp_targets = {str(identity) for _, kind, identity in fresh if kind == "MVP_POLICY"}
        if set(row.reference_effective_statuses) != mvp_targets:
            raise ValueError("MVP effective-state denominator differs from current references")
        if row.current_references is None:
            reasons.add("CURRENT_DEPENDENCIES_UNAVAILABLE:" + str(row.policy_id))
        for role, kind, identity in sorted(
            old.keys() | fresh.keys(), key=lambda key: tuple(map(str, key))
        ):
            original, current = old.get((role, kind, identity)), fresh.get((role, kind, identity))
            status: Literal["UNCHANGED", "CHANGED", "ADDED", "UNAVAILABLE"] = (
                "UNAVAILABLE"
                if current is None
                else "ADDED"
                if original is None
                else "UNCHANGED"
                if original["binding_hash"] == current["binding_hash"]
                else "CHANGED"
            )
            target_status = None
            if current is not None:
                target_status = current["snapshot"].get("status")
                if kind == "FULL_POLICY" and identity in items:
                    target_status = items[identity].effective_status
                elif kind == "MVP_POLICY":
                    target_status = row.reference_effective_statuses[str(identity)]
            if target_status is not None and type(target_status) is not str:
                raise ValueError("Original dependency status is not text")
            review |= status != "UNCHANGED" or target_status in {
                "SUSPENDED",
                "EXPIRED",
                "REVOKED",
                "CONFLICTED",
                "ARCHIVED",
                "PROPOSED",
                "DISCOVERED",
                "MODIFIED",
            }
            edges.append(
                DependencyEdge(
                    source_policy_id=row.policy_id,
                    role=role,
                    kind=cast(Kind, kind),
                    target_id=identity,
                    status=status,
                    original_binding_hash=original["binding_hash"] if original else None,
                    current_binding_hash=current["binding_hash"] if current else None,
                    original_reference=original,
                    current_reference=current,
                    current_target_status=target_status,
                )
            )
        review |= row.reference_validation != "CURRENT" or not row.planning_confirmation_valid
    cycles = _cycles(graph) if capacity else []
    review |= bool(cycles) or bool(reasons)
    value = FullPolicyDependencyReview(
        user_id=data.user_id,
        epoch_id=data.epoch_id,
        selected_policy_id=data.selected_policy_id,
        as_of=data.as_of,
        status="UNKNOWN" if reasons else "COMPLETE_CURRENT_DECLARATION_GRAPH",
        review_required=review,
        current_policy_count=data.current_policy_count,
        current_policy_ids=data.current_policy_ids,
        captured_policy_count=len(items),
        archived_policy_count=data.archived_policy_count,
        policies=data.policies,
        edges=edges,
        cyclic_components=cycles,
        reasons=sorted(reasons),
        input_hash=configuration_hash(data.model_dump(mode="json")),
        review_hash="0" * 64,
        limitations=[
            "CURRENT_FULL_DECLARATIONS_ONLY_MVP_AND_GOAL_MODELS_ARE_REFERENCE_TARGETS",
            "DECLARED_CYCLES_ARE_NOT_FINANCIAL_INFEASIBILITY_OR_MINIMAL_CONFLICT_SETS",
            "CURRENT_READ_IS_NOT_A_BANK_PERMISSION_OR_A_POLICY_CHANGE_CONFIRMATION",
            "ACTION_INFLIGHT_POSITION_AND_BOUNDARY_RECOVERY_ARE_NOT_RECHECKED_HERE",
            "ARCHIVED_CONFIRMATIONS_ARE_NOT_CURRENT_DEPENDENCIES",
        ],
    )
    return value.model_copy(
        update={
            "review_hash": configuration_hash(
                value.model_dump(mode="json", exclude={"review_hash"})
            )
        }
    )
