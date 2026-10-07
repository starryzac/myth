"""Small source-grounded Agent adapter; conversation never grants authority."""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.policy_compiler import CompileContext, compile_policy
from app.services.llm_provider import OpenAICompatibleProvider
from app.services.policy_compilation import CompilationResponse, compile_candidate
from app.services.zhiyu_model_settings import load_model_settings
from app.services.zhiyu_orchestration import autonomy_state, records
from sqlalchemy.orm import Session


def candidate_summary(result: CompilationResponse) -> str:
    config = result.configuration
    if config is None:
        return "还需要一个明确条件，补充后我会重新检查。"
    if config["type"] == "emergency_buffer":
        return f"先保留{config['amount_cents'] / 100:,.2f}元应急金，后续安排不得侵占这条底线。"
    if config["type"] == "goal_saving":
        monthly = config["monthly_contribution"]
        return (
            f"为{config.get('name') or '目标'}储备{config['target_cents'] / 100:,.2f}元，"
            f"截止{config['deadline']}。每月最低{monthly['min_cents'] / 100:,.2f}元，"
            f"目标{monthly['target_cents'] / 100:,.2f}元，最多{monthly['max_cents'] / 100:,.2f}元。"
            "仅使用实际到账收入，在保护底线内自动安排，无费用、无损失，可随时暂停。"
        )
    return "该候选消费链尚未开放。"


def message(
    session: Session,
    user_id: UUID,
    source_text: str,
    engine: str,
    now: datetime,
) -> dict[str, Any]:
    # Server-owned read tools. No caller-selected URL, arbitrary route or write tool.
    query = source_text.strip()
    if any(
        word in query for word in ("现在怎么样", "这笔", "为什么", "多少", "当前状态")
    ) and not any(word in query for word in ("目标", "应急金", "保留")):
        current = autonomy_state(session, user_id, now)
        results = records(session, user_id, "RESULT")
        reply = (
            results[-1].content["payload"].get("summary", "当前没有已核实结果")
            if results
            else "当前没有已完成的自动安排；实际到账后才会按规则处理。"
        )
        if current["pending_count"]:
            reply = "原安排正在核实，会使用同一原动作查询；核实期间暂停新的冲突安排。"
        return {"reply": reply, "candidate": None, "questions": [], "model_used": False}
    settings = load_model_settings()
    provider = OpenAICompatibleProvider(settings) if engine == "llm" else None
    result = compile_candidate(
        session,
        user_id,
        source_text,
        now,
        engine=engine,
        llm_enabled=settings.enabled,
        provider=provider,
    )
    parsed = compile_policy(
        source_text,
        CompileContext(
            reference_date=now.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            timezone="Asia/Shanghai",
        ),
    )
    anchored = result.configuration is not None and parsed.configuration == result.configuration
    if not anchored:
        question = "请明确目标金额、截止日期和每月可安排范围；应急金规则请明确保留金额。"
        if parsed.issues:
            question = parsed.issues[0].message
        return {
            "reply": "候选还需要核对原声明，尚未生效。",
            "model_used": engine == "llm",
            "candidate": {
                "compilation": result.model_dump(mode="json"),
                "summary": "补充明确条件后再审阅",
                "can_confirm": False,
            },
            "questions": [question],
        }
    summary = candidate_summary(result)
    return {
        "reply": summary,
        "model_used": engine == "llm",
        "questions": [],
        "candidate": {
            "compilation": result.model_dump(mode="json"),
            "summary": summary,
            "can_confirm": result.proposal_id is not None,
        },
    }
