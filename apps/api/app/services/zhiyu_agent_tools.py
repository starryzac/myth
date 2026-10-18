"""Finite Next-only model tools; suggestions never become user declarations or grants."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.full_policy_configuration import TemplateName
from app.domain.policy_compiler import CompileContext, compile_policy
from app.services.full_policy_compilation import (
    FullCandidateEnvelope,
    FullCompilationRequest,
    FullCompilerRuntime,
    preview_full_policy_candidate,
)
from app.services.llm_provider import (
    ModelCallBudget,
    ModelProviderError,
    OpenAICompatibleProvider,
    RegisteredModelTool,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _user
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GoalArguments(NoArguments):
    goal_name: str = ""


class PolicyArguments(NoArguments):
    # A model may select a registered form, never supply missing user facts here.
    template_name: TemplateName | None = None


TOOLS = (
    RegisteredModelTool(
        "funds_summary", "Read current verified protection and safe funds.", NoArguments
    ),
    RegisteredModelTool("current_rules", "Read current owned policy versions.", NoArguments),
    RegisteredModelTool(
        "policy_candidate", "Review this turn's original policy request.", PolicyArguments
    ),
    RegisteredModelTool(
        "goal_plan", "Read an existing goal's current plan by its user name.", GoalArguments
    ),
    RegisteredModelTool(
        "action_status", "Read the action referenced by this conversation.", NoArguments
    ),
)


@dataclass
class BudgetedCandidateProvider:
    provider: OpenAICompatibleProvider
    budget: ModelCallBudget
    candidate_format: Literal["mvp", "full"]
    suggestion: FullCandidateEnvelope | None = None

    def propose(self, text: str, context: CompileContext) -> dict[str, Any]:
        raw = self.provider.propose(
            text, context, budget=self.budget, candidate_format=self.candidate_format
        )
        if self.candidate_format == "full":
            try:
                self.suggestion = FullCandidateEnvelope.model_validate(raw)
            except ValueError:
                self.suggestion = None
        return raw


def conversation_references(
    session: Session, user_id: UUID, request_id: UUID | None
) -> dict[str, str]:
    """Only an audited, current-epoch, owned operation may supply object locators."""
    if request_id is None:
        return {}
    from app.services.zhiyu_orchestration import operation

    original = operation(session, user_id, request_id)
    if original is None or original["status"] != "COMPLETED":
        raise PolicyLifecycleError("AGENT_CONTEXT_NOT_FOUND", "原对话或安排尚无法核实", 409)
    result = original["result"]
    if type(result) is not dict:
        raise PolicyLifecycleError("AGENT_CONTEXT_INVALID", "原对象引用无法核实", 409)
    refs = result.get("references", {})
    if type(refs) is not dict:
        raise PolicyLifecycleError("AGENT_CONTEXT_INVALID", "原对象引用无法核实", 409)
    selected: dict[str, str] = {}
    for key in ("action_id", "goal_id"):
        value = result.get(key, refs.get(key))
        if value is not None:
            try:
                selected[key] = str(UUID(str(value)))
            except ValueError as error:
                raise PolicyLifecycleError(
                    "AGENT_CONTEXT_INVALID", "原对象引用无法核实", 409
                ) from error
    return selected


def read_funds(session: Session, user_id: UUID, now: datetime) -> dict[str, Any]:
    from app.services.dashboard import get_dashboard

    dashboard = get_dashboard(session, user_id, now, pending_limit=1, recovery_limit=1)
    boundary = dashboard.boundary
    proven = boundary.state == "PROVEN"
    return {
        "kind": "funds",
        "as_of": dashboard.as_of.isoformat(),
        "source_version": boundary.input_digest,
        "state": boundary.state,
        "safe_cents": boundary.safe_idle_cents if proven else None,
        "protected_cents": boundary.current_protected_cents if proven else None,
        "summary": (
            f"当前保护资金{boundary.current_protected_cents / 100:,.2f}元，"
            f"安全可安排{boundary.safe_idle_cents / 100:,.2f}元。"
            if proven
            and boundary.current_protected_cents is not None
            and boundary.safe_idle_cents is not None
            else "当前资金来源尚未完整核实，不能据此扩大安排范围。"
        ),
    }


def read_goal(
    session: Session, user_id: UUID, now: datetime, refs: dict[str, str], name: str
) -> dict[str, Any]:
    from app.services.goal_allocation import preview_goal_allocation
    from app.services.goals import list_goals

    goals = list_goals(session, user_id).items
    selected = [
        goal
        for goal in goals
        if (goal.name == name if name else str(goal.id) == refs.get("goal_id"))
    ]
    if len(selected) != 1:
        return {"kind": "goal", "summary": "请说明要查询哪个现有目标的名称。"}
    goal = selected[0]
    plan = preview_goal_allocation(session, user_id, goal.id, now)
    amount = plan.allocation.suggested_cents
    return {
        "kind": "goal",
        "references": {"goal_id": str(goal.id)},
        "as_of": plan.as_of.isoformat(),
        "source_version": plan.input_digest,
        "summary": (
            f"{goal.name}当前可安排{amount / 100:,.2f}元。"
            if amount is not None
            else f"{goal.name}当前尚不能形成可核实安排。"
        )
        + " ".join(plan.allocation.reasons),
    }


def read_action(
    session: Session, user_id: UUID, now: datetime, refs: dict[str, str]
) -> dict[str, Any]:
    from app.db.models import ActionPlan
    from app.services.execution import get_action

    if "action_id" not in refs:
        return {"kind": "action", "summary": "请选择活动中的原安排，再查询这笔的当前结果。"}
    action_id = UUID(refs["action_id"])
    owned = session.get(ActionPlan, action_id)
    if owned is None or owned.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "当前本人原安排不存在", 404)
    protocol = owned.request.get("protocol")
    if protocol in {"goal-release-action-v1", "goal-transfer-action-v1",
                    "goal-transfer-action-v2"}:
        if protocol == "goal-release-action-v1":
            from app.services.full_goal_release_execution import read_goal_release_execution

            dedicated: Any = read_goal_release_execution(session, user_id, action_id, now)
            amount = dedicated.original_command.effect.amount_cents
            goals = {"goal_id": str(dedicated.original_command.effect.source_goal_id)}
            label = "目标现金回拨"
        else:
            from app.services.full_goal_transfer import read_goal_transfer_action

            dedicated = read_goal_transfer_action(session, user_id, action_id, now)
            amount = dedicated.original_command.effect.scope.amount_cents
            goals = {
                "goal_id": str(dedicated.original_command.effect.scope.source.goal_id),
                "target_goal_id": str(dedicated.original_command.effect.scope.target.goal_id),
            }
            label = "目标间转移"
        if dedicated.service_receipt_verified:
            receipt = dedicated.original_receipt
            if not isinstance(receipt, dict):
                raise PolicyLifecycleError("INVALID_ACTION_RECEIPT", "原回执未核实", 409)
            summary = (
                f"原{label}回执已独立核实，实际金额{receipt['executed_cents'] / 100:,.2f}元，"
                f"费用{receipt['fee_cents'] / 100:,.2f}元，"
                f"损失{receipt['loss_cents'] / 100:,.2f}元。"
            )
        elif dedicated.unresolved:
            summary = (
                f"原{label}{amount / 100:,.2f}元仍在核实，保留同一原动作与银行键，不会另建一笔。"
            )
        else:
            summary = f"原{label}{amount / 100:,.2f}元尚无已核实回执，执行仍须原服务重检。"
        return {
            "kind": "action",
            "summary": summary,
            "status": dedicated.original_action_status,
            "as_of": dedicated.as_of.isoformat(),
            "source_version": dedicated.original_command.effect_hash,
            "receipt_verified": dedicated.service_receipt_verified,
            "receipt_is_current_authority": False,
            "references": {"action_id": str(dedicated.action_id), **goals},
        }
    actual = get_action(session, user_id, action_id, now)
    if actual.receipt is not None:
        summary = (
            f"原安排已核实到账{actual.receipt.executed_cents / 100:,.2f}元，"
            f"费用{actual.receipt.fee_cents / 100:,.2f}元，"
            f"损失{actual.receipt.loss_cents / 100:,.2f}元。"
        )
    elif actual.status in {"UNKNOWN", "SUBMITTED"}:
        summary = "这笔原安排正在核实，继续查询同一原动作，不会另建一笔。"
    else:
        summary = "这笔原安排尚无已核实回执，当前条件仍须由原执行服务重检。"
    return {
        "kind": "action",
        "summary": summary,
        "status": actual.status,
        "as_of": actual.as_of.isoformat(),
        "source_version": actual.effect_hash,
        "references": {
            "action_id": str(actual.action_id),
            **({"goal_id": str(actual.effect.goal_id)} if actual.effect.goal_id else {}),
        },
    }


def full_candidate(
    session: Session,
    user_id: UUID,
    text: str,
    now: datetime,
    provider: BudgetedCandidateProvider,
    suggested_template: TemplateName | None = None,
) -> dict[str, Any]:
    from app.domain.full_policy_compiler import compile_full_policy, provider_source_text

    user = _user(session, user_id)
    context = CompileContext(
        reference_date=now.astimezone(ZoneInfo(user.timezone)).date(), timezone=user.timezone
    )
    parsed = compile_full_policy(text, context)
    # Unknown prose is not sent as if it were a complete declarative original.
    # Its model-selected registered form still allows new explicit user input.
    has_original = bool(provider_source_text(text, parsed).text.strip())
    original = preview_full_policy_candidate(
        user,
        now,
        FullCompilationRequest(text=text, engine="llm" if has_original else "rules"),
        FullCompilerRuntime(llm_enabled=True, provider=provider),
    ).compilation
    template = (
        original.template_name
        or suggested_template
        or (provider.suggestion.template_name if provider.suggestion is not None else None)
    )
    if template is None:
        return {
            "reply": "请说明要设置哪一类规则，金额和范围仍需明确。",
            "candidate": None,
            "questions": ["这次要设置哪一类规则？"],
            "full_review": None,
        }
    # Unanchored model fields are never prefilled. The existing USER form supplies
    # any missing facts; its original candidate/preview/confirmation chain remains.
    anchored = original.configuration is not None
    return {
        "reply": "已整理规则候选，请核对必要业务字段，再预览影响；尚未生效或授予权限。",
        "candidate": None,
        "questions": [],
        "full_review": {
            "template_name": template,
            "configuration": original.configuration if anchored else {},
            "source_status": "ANCHORED" if anchored else "EXPLICIT_FIELDS_REQUIRED",
            "source_text_sha256": original.original_text_sha256,
            "source_fragments": [row.model_dump(mode="json") for row in original.source_fragments],
            "issues": [row.message for row in original.issues],
            "requires_user_fields": not anchored,
            "bank_authority": False,
        },
    }


def run_model_agent(
    session: Session,
    user_id: UUID,
    text: str,
    now: datetime,
    provider: OpenAICompatibleProvider,
    mvp: Callable[[BudgetedCandidateProvider], dict[str, Any]],
    refs: dict[str, str],
) -> dict[str, Any]:
    from app.domain.full_policy_compiler import redact_full_policy_text

    budget = provider.new_budget()
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Use only the registered tools for this user's original request. Never invent "
                "money, authority, actions or results. Policy requests must call policy_candidate; "
                "Status questions use action_status; planning uses goal_plan or funds_summary. "
                "Do not rewrite user declarations. After read tools return, reply exactly JSON "
                '{"done":true}; server renders verified numbers and explanations.'
            ),
        },
        {"role": "user", "content": redact_full_policy_text(text).text},
    ]
    observations: list[dict[str, Any]] = []
    invoked: set[str] = set()
    while budget.used < budget.max_calls:
        reply = provider.chat(messages, tools=TOOLS, budget=budget)
        if not reply.tool_calls:
            if reply.content != {"done": True}:
                raise ModelProviderError("LLM_INVALID_RESPONSE")
            break
        if len(reply.tool_calls) != 1:
            raise ModelProviderError("LLM_TOOL_REJECTED")
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in reply.tool_calls
                ],
            }
        )
        for call in reply.tool_calls:
            if call.name in invoked:
                raise ModelProviderError("LLM_TOOL_REJECTED")
            invoked.add(call.name)
            if call.name == "policy_candidate":
                user = _user(session, user_id)
                context = CompileContext(
                    reference_date=now.astimezone(ZoneInfo(user.timezone)).date(),
                    timezone=user.timezone,
                )
                parsed = compile_policy(text, context)
                from app.domain.full_policy_compiler import compile_full_policy

                full_parsed = compile_full_policy(text, context)
                full_header = any(
                    fragment.field == "template_name" for fragment in full_parsed.source_fragments
                )
                if not full_header and (
                    "旅行目标" in text
                    or "应急金" in text
                    or parsed.configuration is not None
                    or parsed.draft.get("type") in {"goal_saving", "emergency_buffer"}
                ):
                    candidate = mvp(BudgetedCandidateProvider(provider, budget, "mvp"))
                else:
                    candidate = full_candidate(
                        session,
                        user_id,
                        text,
                        now,
                        BudgetedCandidateProvider(provider, budget, "full"),
                        call.arguments.get("template_name"),
                    )
                funds = read_funds(session, user_id, now)
                return {**candidate, "model_used": True, "facts": [funds], "references": refs}
            if call.name == "funds_summary":
                observed = read_funds(session, user_id, now)
            elif call.name == "current_rules":
                from app.services.zhiyu_policy_catalog import list_catalog_policies

                rules = list_catalog_policies(session, user_id, now).items
                if len(rules) > 100:
                    raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "规则数量超过对话容量", 409)
                observed = {
                    "kind": "rules",
                    "summary": f"当前核实了{len(rules)}条规则。"
                    + "；".join(str(row.configuration.get("name", "规则")) for row in rules),
                    "versions": [
                        {
                            "policy_id": str(row.policy_id),
                            "version_id": str(row.current_version_id),
                            "status": row.status,
                            "name": row.configuration.get("name", "规则"),
                        }
                        for row in rules
                    ],
                }
            elif call.name == "goal_plan":
                observed = read_goal(session, user_id, now, refs, call.arguments["goal_name"])
            elif call.name == "action_status":
                observed = read_action(session, user_id, now, refs)
            else:
                raise ModelProviderError("LLM_TOOL_REJECTED")
            observations.append(observed)
            refs = {**refs, **observed.get("references", {})}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(
                        {"kind": observed["kind"], "summary": observed["summary"]},
                        ensure_ascii=False,
                    ),
                }
            )
    return {
        "reply": " ".join(row["summary"] for row in observations)
        or "请说明要设置规则还是查询已有安排。",
        "model_used": True,
        "candidate": None,
        "full_review": None,
        "questions": [] if observations else ["这次需要设置规则，还是查询已有安排？"],
        "facts": observations,
        "references": refs,
    }
