"""Content verification and deterministic explanations, without execution or fresh reads."""

import hashlib
import json
from typing import Any

from app.domain.decision_trace_types import (
    MAX_TRACE_BYTES,
    DecisionTrace,
    TraceExplanation,
    TraceReason,
    _TraceContent,
)
from app.domain.policy_configuration import configuration_hash
from pydantic import BaseModel

# This new protocol retains the whole joint plan and its complete planning
# inputs in one trace. Existing algorithms keep their original 10 MiB cap;
# the new cap also fits the existing 16 MiB raw audit-subject contract.
MAX_FULL_JOINT_TRACE_BYTES = 16 * 1024 * 1024

_REASONS = {
    "EXPLICIT_TRANSFER_CONFIRMATION_REQUIRED": "内部转账需要用户对本次经济后果明确确认。",
    "EXPLICIT_PAYMENT_CONFIRMATION_REQUIRED": "本次付款需要用户明确确认。",
    "EXPLICIT_COST_CONFIRMATION_REQUIRED": "本次操作涉及已记录的费用或损失，需要用户确认。",
    "EXPLICIT_CONFIRMATION_REQUIRED": "本次动作需要用户明确确认。",
    "OUTSIDE_AUTHORITY": "本次动作超出当时保存的授权范围。",
    "STALE_ACTION_POLICY_VERSION": "动作绑定的策略版本与当时有效版本不一致。",
    "MISSING_FORMAL_POLICY_VERSION": "缺少可核对的正式策略版本。",
    "INVALID_FORMAL_POLICY_EVIDENCE": "策略确认凭证未通过当时的核验。",
    "LIQUIDITY_RISK": "保存的边界计算显示流动性缺口。",
    "INSUFFICIENT_EVIDENCE": "当时的证据不足，无法完成资金边界核验。",
    "MISSING_BALANCE_EVIDENCE": "缺少可核对的余额证据，约束满足情况未知。",
    "CASH_SUFFICIENT": "保存的约束计算显示可用现金满足所需金额。",
    "BELOW_MINIMUM_PURCHASE": "候选金额低于产品最低购买金额。",
    "MATURITY_AFTER_OBLIGATION": "候选产品的到期时点晚于需要资金的义务时点。",
    "WAITING_FOR_PRINCIPAL": "本金仍待实际到账；预计边界不能作为已到账事实。",
    "AMOUNT_CAP": "本次金额超过当时保存的授权上限。",
    "UNDECLARED_USER_VARIABLE_CHANGE": "候选世界改变了白名单之外的事实。",
    "TARGET_CUMULATIVE_MONTHLY_CONTRIBUTION": "目标建议依据当月已贡献金额与累计目标额度。",
    "MINIMUM_SHORTFALL": "本次可分配金额不足以补足目标最低贡献。",
    "PREVIEW_DOES_NOT_CONSUME_INCOME": "本次仅保存分配预览，未消耗收入来源。",
    "POSITIVE_FEE_OR_LOSS_REQUIRES_NEW_CONFIRMATION": "提前支取有费用或损失，需要新的明确确认。",
    "NO_NEGATIVE_POINT_IMPROVED": "候选恢复动作未改善任何负边界点。",
    "RECOVERY_WORSENS_A_CHECKPOINT": "候选恢复动作使至少一个检查点的边界变差。",
    "CURRENT_AUTHORIZATION_NOT_ACTIVE_LATEST_VERSION": "当前恢复授权不是有效的最新策略版本。",
    "MANUAL_POSITION_HAS_NO_ORIGINAL_RECOVERY_AUTHORITY": "手动持仓缺少原始自主恢复授权。",
    "POSITION_ALREADY_REDEEMED_OR_RESERVED": "该持仓已赎回、正在赎回或本金已预留。",
    "PRODUCT_HAS_NO_AUTOMATIC_LOSSLESS_REDEMPTION": "原产品没有可核对的自动无损赎回条款。",
}


def _encoded(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _input_payload(trace: _TraceContent) -> dict[str, Any]:
    return trace.model_dump(
        mode="json",
        include={
            "schema_version",
            "simulation",
            "user_id",
            "phase",
            "as_of",
            "algorithm_versions",
            "inputs",
            "sources",
            "policies",
        },
    )


def _content_payload(trace: DecisionTrace) -> dict[str, Any]:
    return trace.model_dump(mode="json", exclude={"trace_hash"})


def _validate_content(trace: _TraceContent) -> None:
    if trace.parent_run_id == trace.run_id:
        raise ValueError("Decision trace cannot be its own parent")
    for collection, identity in (
        (trace.sources, "id"),
        (trace.policies, "id"),
        (trace.constraints, "constraint_key"),
        (trace.candidates, "candidate_key"),
    ):
        keys = [getattr(item, identity) for item in collection]
        if len(keys) != len(set(keys)):
            raise ValueError("Trace source, policy, constraint and candidate keys must be unique")
    for source in trace.sources:
        if source.user_id != trace.user_id:
            raise ValueError("Trace evidence belongs to another user")
        actual = configuration_hash(source.content)
        if actual != source.captured_content_hash:
            raise ValueError("Trace captured evidence hash does not match its saved copy")
        expected_integrity = "VERIFIED" if actual == source.content_hash else "INVALID"
        if source.content_integrity != expected_integrity:
            raise ValueError("Trace evidence integrity label does not match the source claim")
    for policy in trace.policies:
        if policy.user_id != trace.user_id:
            raise ValueError("Trace policy belongs to another user")
        actual = configuration_hash(policy.configuration)
        if actual != policy.captured_configuration_hash:
            raise ValueError("Trace captured policy hash does not match its saved copy")
        expected_integrity = "VERIFIED" if actual == policy.configuration_hash else "INVALID"
        if policy.configuration_integrity != expected_integrity:
            raise ValueError("Trace policy integrity label does not match the source claim")

    def owners(value: Any) -> None:
        if isinstance(value, dict):
            if "user_id" in value and value["user_id"] != str(trace.user_id):
                raise ValueError("Trace nested facts belong to another user")
            for child in value.values():
                owners(child)
        elif isinstance(value, list):
            for child in value:
                owners(child)

    # Source/configuration bodies are untrusted declarations. Their original owner
    # claims may be precisely why the trusted adapter rejected them. Typed row owners
    # above and trusted inputs/results still have to belong to this user.
    owners(trace.model_dump(mode="json", exclude={"sources", "policies"}))
    payload = trace.model_dump(mode="json")
    byte_limit = (
        MAX_FULL_JOINT_TRACE_BYTES
        if trace.algorithm_versions.get("full_joint_goal_execution")
        == "registered-joint-goal-execution-v2"
        else MAX_TRACE_BYTES
    )
    if len(_encoded(payload)) > byte_limit:
        raise ValueError("Complete decision trace exceeds the byte limit")


def _validate_hashes(trace: DecisionTrace) -> None:
    if hashlib.sha256(_encoded(_input_payload(trace))).hexdigest() != trace.input_hash:
        raise ValueError("Decision trace input hash does not match its frozen inputs")
    if hashlib.sha256(_encoded(_content_payload(trace))).hexdigest() != trace.trace_hash:
        raise ValueError("Decision trace content hash does not match its frozen content")


def build_trace(**fields: Any) -> DecisionTrace:
    """Freeze exact supplied facts/results; caller-provided hash fields are rejected."""
    content = _TraceContent.model_validate(fields)
    payload = content.model_dump(mode="python")
    payload["input_hash"] = hashlib.sha256(_encoded(_input_payload(content))).hexdigest()
    json_payload = {**content.model_dump(mode="json"), "input_hash": payload["input_hash"]}
    payload["trace_hash"] = hashlib.sha256(_encoded(json_payload)).hexdigest()
    return DecisionTrace.model_validate(payload)


def verify_trace(trace: DecisionTrace) -> None:
    """Revalidate copies too: frozen Pydantic models do not make nested JSON immutable."""

    def declared(value: Any) -> None:
        if isinstance(value, BaseModel):
            if set(value.__dict__) - set(type(value).model_fields):
                raise ValueError("Trace model contains undeclared copied fields")
            for child in value.__dict__.values():
                declared(child)
        elif isinstance(value, dict):
            for child in value.values():
                declared(child)
        elif isinstance(value, list):
            for child in value:
                declared(child)

    if not isinstance(trace, DecisionTrace):
        raise ValueError("Expected a DecisionTrace")
    declared(trace)
    DecisionTrace.model_validate(trace.model_dump(mode="python"))


def explain_trace(trace: DecisionTrace) -> TraceExplanation:
    """Only saved fields produce explanations; unknown reason codes remain explicit."""
    verify_trace(trace)
    outcome = trace.outcome
    decision: dict[str, Any] = {}
    decision_path = "outcome"
    for key in ("decision", "autonomy"):
        if isinstance(outcome.get(key), dict):
            decision = outcome[key]
            decision_path = "outcome." + key
            break
    if not decision:
        decision = outcome
    level = decision.get("level", decision.get("autonomy_level"))
    financial = decision.get("financial_evaluation")
    required = decision.get("confirmation_required")
    satisfied = decision.get("confirmation_satisfied")
    summary = [f"本记录为模拟决策，阶段 {trace.phase}，依据时刻 {trace.as_of.isoformat()}。"]
    for source in trace.sources:
        if source.content_integrity == "INVALID":
            summary.append(f"来源 {source.id} 的原内容声明哈希未通过核验；本轨迹保留实际副本。")
    for policy in trace.policies:
        summary.append(
            f"使用策略版本 {policy.id}（第 {policy.version_number} 版），"
            f"当时状态 {policy.status_at_decision}。"
        )
        if policy.configuration_integrity == "INVALID":
            summary.append(f"策略版本 {policy.id} 的原配置声明哈希未通过核验；本轨迹保留实际副本。")
    if isinstance(level, str):
        summary.append(f"当时自主级别：{level}。")
    if financial == "NOT_EVALUATED":
        summary.append("本次资金可执行性未评估，记录不能据此得出资金可用结论。")
    elif isinstance(financial, str):
        summary.append(f"当时财务核验：{financial}。")
    if required is True:
        summary.append(
            "本次保留人工确认来源；确认已完成。"
            if satisfied is True
            else "本次需要人工确认，保存的记录未显示确认已完成。"
        )

    for key, name in (("actual_boundary", "实际"), ("projected_boundary", "预计")):
        boundary = outcome.get(key)
        if isinstance(boundary, dict) and isinstance(boundary.get("status"), str):
            summary.append(f"{name}资金边界状态：{boundary['status']}。")

    reasons: list[TraceReason] = []

    def add(
        code: str, reference: str, text: str | None = None, references: list[str] | None = None
    ) -> None:
        reasons.append(
            TraceReason(
                code=code,
                text=text or _REASONS.get(code, f"记录原因码 {code}；未提供该原因的解释模板。"),
                references=references or [reference],
            )
        )

    def saved_reasons(value: dict[str, Any], reference: str) -> None:
        codes = value.get("reasons")
        if isinstance(codes, list):
            for index, code in enumerate(codes):
                if isinstance(code, str) and code:
                    add(code, f"{reference}.reasons[{index}]")

    saved_reasons(decision, decision_path)
    if decision_path != "outcome":
        saved_reasons(outcome, "outcome")
    for key in (
        "validation",
        "boundary",
        "plan",
        "recovery",
        "asset_allocation",
        "goal_allocation",
    ):
        value = outcome.get(key)
        if isinstance(value, dict):
            saved_reasons(value, "outcome." + key)
            for boundary_key, name in (("actual_boundary", "实际"), ("projected_boundary", "预计")):
                boundary = value.get(boundary_key)
                if isinstance(boundary, dict) and isinstance(boundary.get("status"), str):
                    summary.append(f"{name}资金边界状态：{boundary['status']}。")
    for index, constraint in enumerate(trace.constraints):
        reference = f"constraints[{index}]"
        state = (
            "未知"
            if constraint.satisfied is None
            else ("满足" if constraint.satisfied else "不满足")
        )
        text = f"约束 {constraint.constraint_key}：{state}。"
        references = [reference + ".reason_code", reference + ".satisfied"]
        if constraint.required_cents is not None:
            text += f"所需 {constraint.required_cents} 分。"
            references.append(reference + ".required_cents")
        if constraint.available_cents is not None:
            text += f"可用 {constraint.available_cents} 分。"
            references.append(reference + ".available_cents")
        add(constraint.reason_code, reference + ".reason_code", text, references)
    for index, candidate in enumerate(trace.candidates):
        text = f"候选 {candidate.candidate_key}：当时状态 {candidate.status}。"
        if candidate.status != "NOT_EVALUATED":
            cap = candidate.result.get("max_allocatable_cents")
            net_yield = candidate.result.get("net_simulated_yield_cents")
            if type(cap) is int:
                text += f"计算上限 {cap} 分。"
            if type(net_yield) is int:
                text += f"模拟净收益 {net_yield} 分。"
            exit_plan = candidate.result.get("exit_plan")
            if isinstance(exit_plan, dict):
                available_at = exit_plan.get("principal_available_at")
                if isinstance(available_at, str):
                    text += f"计划本金可用时刻 {available_at}。"
        summary.append(text)
        for reason_index, code in enumerate(candidate.reasons):
            add(code, f"candidates[{index}].reasons[{reason_index}]")
    if not reasons:
        summary.append("保存的轨迹未提供原因码，无法补充未记录的计算理由。")
    return TraceExplanation(
        run_id=trace.run_id,
        user_id=trace.user_id,
        level=level if isinstance(level, str) else None,
        financial_evaluation=financial if isinstance(financial, str) else None,
        confirmation_required=required if type(required) is bool else None,
        confirmation_satisfied=satisfied if type(satisfied) is bool else None,
        summary=summary,
        reasons=reasons,
    )
