"""Deterministic financial differences between independently verified captured inputs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundarySnapshot,
)
from app.domain.policy_configuration import configuration_hash


@dataclass(frozen=True)
class VerifiedBoundaryInput:
    run_id: UUID
    user_id: UUID
    trace_hash: str
    input_path: str
    snapshot: BoundarySnapshot
    versions: tuple[BoundaryPolicyVersion, ...]
    positions: tuple[BoundaryPosition, ...]
    products: tuple[BoundaryProduct, ...]
    source_refs: tuple[tuple[str, str], ...]


class BoundaryFactChange(BoundaryModel):
    component: str
    before_value: Any
    after_value: Any
    before_value_hash: str
    after_value_hash: str
    before_paths: list[str]
    after_paths: list[str]


class BoundaryDifference(BoundaryModel):
    status: Literal["RECOMPUTED", "UNKNOWN"]
    scope: Literal["FINANCIAL_SAFE_IDLE_FUNDS_ORIGINAL_MVP"] = (
        "FINANCIAL_SAFE_IDLE_FUNDS_ORIGINAL_MVP"
    )
    financial_only: Literal[True] = True
    authority_granted: Literal[False] = False
    evaluation_only: Literal[True] = True
    before_run_id: UUID
    after_run_id: UUID
    before_trace_hash: str | None
    after_trace_hash: str | None
    before_as_of: datetime | None
    after_as_of: datetime | None
    before_safe_idle_cents: int | None
    after_safe_idle_cents: int | None
    delta_cents: int | None
    before_boundary_hash: str | None
    after_boundary_hash: str | None
    changes: list[BoundaryFactChange]
    before_source_refs: list[tuple[str, str]]
    after_source_refs: list[tuple[str, str]]
    attribution: Literal[
        "UNCHANGED",
        "SINGLE_CHANGED_COMPONENT",
        "JOINT_CHANGES_NOT_INDIVIDUALLY_ATTRIBUTED",
        "UNKNOWN",
    ]
    reasons: list[str]
    explanation: str
    autonomy_action_set_difference: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


def unknown_difference(before: UUID, after: UUID, reasons: list[str]) -> BoundaryDifference:
    return BoundaryDifference(
        status="UNKNOWN",
        before_run_id=before,
        after_run_id=after,
        before_trace_hash=None,
        after_trace_hash=None,
        before_as_of=None,
        after_as_of=None,
        before_safe_idle_cents=None,
        after_safe_idle_cents=None,
        delta_cents=None,
        before_boundary_hash=None,
        after_boundary_hash=None,
        changes=[],
        before_source_refs=[],
        after_source_refs=[],
        attribution="UNKNOWN",
        reasons=reasons,
        explanation="原决策快照或其证据未完整验证，未生成金额或因果结论。",
    )


def _components(value: VerifiedBoundaryInput) -> dict[str, Any]:
    snapshot = value.snapshot.model_dump(mode="json")
    # Source digests and evidence identifiers are provenance, not causal cash amounts.
    return {
        "clock": snapshot["as_of"],
        "projection_window": {
            "timezone": snapshot["timezone"],
            "horizon_days": snapshot["horizon_days"],
        },
        **{
            key: snapshot[key]
            for key in (
                "cash_accounts",
                "bills",
                "occurrence_settlements",
                "goals",
                "goal_month_contributions",
                "living_reserves",
                "unassigned_goal_cash",
            )
        },
        "versions": [row.model_dump(mode="json") for row in value.versions],
        "positions": [row.model_dump(mode="json") for row in value.positions],
        "products": [row.model_dump(mode="json") for row in value.products],
    }


def _paths(value: VerifiedBoundaryInput, key: str) -> list[str]:
    if key == "clock":
        return [value.input_path + ".snapshot.as_of"]
    if key == "projection_window":
        return [
            value.input_path + ".snapshot.timezone",
            value.input_path + ".snapshot.horizon_days",
        ]
    return [
        value.input_path + (".snapshot." if key in BoundarySnapshot.model_fields else ".") + key
    ]


def _same_facts(before: Any, after: Any) -> bool:
    # A row enumeration order is not a new financial fact. Responses still retain
    # each original value and path; no captured bytes or hashes are rewritten.
    if isinstance(before, list) and isinstance(after, list):
        return sorted(configuration_hash({"row": row}) for row in before) == sorted(
            configuration_hash({"row": row}) for row in after
        )
    return bool(before == after)


def compare_verified_boundaries(
    before: VerifiedBoundaryInput, after: VerifiedBoundaryInput
) -> BoundaryDifference:
    """Recompute both actual worlds; never invent an unobserved marginal effect.

    Source-bound components are deterministic explanations of observed changes.
    Multiple changes are joint: this function does not choose a causal story or
    distribute the delta across correlated facts. No candidate reaches execution.
    """
    if before.user_id != after.user_id or before.snapshot.as_of > after.snapshot.as_of:
        raise ValueError("Actual owner and chronological order must match")
    results = [
        compute_boundary(
            value.snapshot, list(value.versions), list(value.positions), list(value.products)
        )
        for value in (before, after)
    ]
    if any(result.status == "INSUFFICIENT_EVIDENCE" for result in results):
        return unknown_difference(
            before.run_id, after.run_id, ["ORIGINAL_BOUNDARY_EVIDENCE_INCOMPLETE"]
        )
    first, last = results
    old, new = _components(before), _components(after)
    changes = [
        BoundaryFactChange(
            component=key,
            before_value=old[key],
            after_value=new[key],
            before_value_hash=configuration_hash({"value": old[key]}),
            after_value_hash=configuration_hash({"value": new[key]}),
            before_paths=_paths(before, key),
            after_paths=_paths(after, key),
        )
        for key in sorted(old)
        if not _same_facts(old[key], new[key])
    ]
    x, y = first.safe_idle_cents, last.safe_idle_cents
    if x is None or y is None:
        return unknown_difference(
            before.run_id, after.run_id, ["ORIGINAL_SAFE_IDLE_AMOUNT_NOT_PROVEN"]
        )
    attribution: Literal[
        "UNCHANGED",
        "SINGLE_CHANGED_COMPONENT",
        "JOINT_CHANGES_NOT_INDIVIDUALLY_ATTRIBUTED",
        "UNKNOWN",
    ] = (
        "UNCHANGED"
        if not changes
        else "SINGLE_CHANGED_COMPONENT"
        if len(changes) == 1
        else "JOINT_CHANGES_NOT_INDIVIDUALLY_ATTRIBUTED"
    )
    labels = {
        "clock": "可信业务时点",
        "projection_window": "投影窗口",
        "cash_accounts": "现金账户原事实",
        "bills": "账单原事实",
        "occurrence_settlements": "义务结算原事实",
        "goals": "目标归属原事实",
        "goal_month_contributions": "目标月贡献原事实",
        "living_reserves": "生活准备金原估计",
        "unassigned_goal_cash": "未归属目标现金原事实",
        "versions": "已确认策略版本",
        "positions": "持仓本金及可用时间原事实",
        "products": "原产品合同",
    }
    explanation = f"原金融边界重算：安全闲置资金由{x}分变为{y}分，差额{y - x}分。"
    if changes:
        explanation += (
            "实际变化来源：" + "、".join(labels[item.component] for item in changes) + "。"
        )
    if len(changes) > 1:
        explanation += "多个原事实共同变化，未将总差额伪分配为单事实贡献。"
    return BoundaryDifference(
        status="RECOMPUTED",
        before_run_id=before.run_id,
        after_run_id=after.run_id,
        before_trace_hash=before.trace_hash,
        after_trace_hash=after.trace_hash,
        before_as_of=before.snapshot.as_of,
        after_as_of=after.snapshot.as_of,
        before_safe_idle_cents=x,
        after_safe_idle_cents=y,
        delta_cents=y - x,
        before_boundary_hash=first.boundary_hash,
        after_boundary_hash=last.boundary_hash,
        changes=changes,
        before_source_refs=list(before.source_refs),
        after_source_refs=list(after.source_refs),
        attribution=attribution,
        reasons=[],
        explanation=explanation,
    )
