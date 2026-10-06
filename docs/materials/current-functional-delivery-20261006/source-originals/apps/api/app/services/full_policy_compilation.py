"""Stateless, server-context FULL previews and an explicitly disabled provider seam."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db.models import User
from app.domain.full_policy_compiler import (
    COMPILER_VERSION,
    MAX_TEXT_CHARS,
    FullCompilationIssue,
    FullCompilationResult,
    compile_full_policy,
    explain_candidate_diff,
    grammar_examples,
    provider_source_text,
    resolve_provider_references,
    strict_candidate_json,
)
from app.domain.full_policy_configuration import TemplateName, validate_full_configuration
from app.domain.policy_compiler import CompileContext
from app.domain.policy_configuration import CalendarDate, StrictModel
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import Field, StrictStr


class FullCandidateEnvelope(StrictModel):
    template_name: TemplateName
    configuration: dict[str, Any]


class FullCompilationRequest(StrictModel):
    text: Annotated[StrictStr, Field(min_length=1, max_length=MAX_TEXT_CHARS)]
    engine: Literal["rules", "llm"] = "rules"
    comparison_candidate: FullCandidateEnvelope | None = None


class FullCompilationResponse(StrictModel):
    simulation: Literal[True] = True
    user_id: UUID
    reference_date: CalendarDate
    timezone: str
    compiler_version: str = COMPILER_VERSION
    compilation: FullCompilationResult
    comparison_source: Literal["NONE", "USER_PROVIDED_CANDIDATE_NOT_CURRENT_VERSION"]
    confirmation_record_created: Literal[False] = False
    grants_authority: Literal[False] = False
    bank_authority: Literal[False] = False


class FullGrammarResponse(StrictModel):
    simulation: Literal[True] = True
    compiler_version: str = COMPILER_VERSION
    examples: dict[TemplateName, str]
    original_examples_are_synthetic: Literal[True] = True
    supported_scope: str = (
        "十二模板的有限受控中文句式；金额需明确元/分/万元，日期需YYYY-MM-DD，"
        "引用需明确UUID或收款人符号；列表用顿号。未解释、相对日期或缺字段保持UNKNOWN/MISSING。"
    )
    bank_authority: Literal[False] = False


class FullCandidateProvider(Protocol):
    def propose(self, text: str, context: CompileContext) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FullCompilerRuntime:
    # Only server dependency wiring may supply these values. No network implementation exists.
    llm_enabled: bool = False
    provider: FullCandidateProvider | None = None


def grammar() -> FullGrammarResponse:
    return FullGrammarResponse(examples=grammar_examples())


def _model_candidate(
    body: FullCompilationRequest,
    context: CompileContext,
    result: FullCompilationResult,
    runtime: FullCompilerRuntime,
) -> FullCompilationResult:
    if not runtime.llm_enabled:
        raise PolicyLifecycleError("LLM_DISABLED", "可选完整候选模型默认关闭", 422)
    if runtime.provider is None:
        raise PolicyLifecycleError("LLM_UNAVAILABLE", "未配置完整候选模型提供方", 503)
    redacted = provider_source_text(body.text, result)
    try:
        raw = runtime.provider.propose(redacted.text, context)
    except Exception:
        raise PolicyLifecycleError("LLM_UNAVAILABLE", "候选提供方未返回可用原输出", 503) from None
    issues = list(result.issues)
    candidate: dict[str, Any] | None = None
    differences = []
    try:
        envelope = FullCandidateEnvelope.model_validate(strict_candidate_json(raw))
        restored = resolve_provider_references(envelope.configuration, redacted)
        candidate = validate_full_configuration(envelope.template_name, restored)
        if envelope.template_name != result.template_name or result.configuration is None:
            issues.append(
                FullCompilationIssue(
                    code="MODEL_SOURCE_NOT_COMPLETE",
                    field="configuration",
                    message="模型不能补造未被独立原句规则提取的金额、日期、范围或引用。",
                )
            )
        else:
            differences = explain_candidate_diff(result.configuration, candidate)
            if differences:
                issues.append(
                    FullCompilationIssue(
                        code="MODEL_RULE_CONFLICT",
                        field="configuration",
                        message="模型与独立原句规则不同，需人工修改并重新完整预览，不能直接采纳。",
                    )
                )
    except (ValueError, TypeError, RecursionError):
        issues.append(
            FullCompilationIssue(
                code="MODEL_SCHEMA_REJECTED",
                field="configuration",
                message="模型封套、原引用令牌或十二模板严格JSON校验失败；不返回其非法原内容。",
            )
        )
    accepted = candidate is not None and not issues
    return result.model_copy(
        update={
            "engine": "llm",
            "evidence_level": "MODEL_INFERRED",
            "issues": issues,
            "configuration": candidate if accepted else None,
            "configuration_hash": result.configuration_hash if accepted else None,
            "differences": differences,
            "status": "READY_FOR_REVIEW"
            if accepted
            else (
                "REVIEW_REQUIRED" if differences else result.status if result.issues else "UNKNOWN"
            ),
            "summary": "脱敏模型输出经过独立原句与严格Schema验证；仅候选，未确认、未授银行权限。",
        }
    )


def preview_full_policy_candidate(
    user: User,
    now: datetime,
    body: FullCompilationRequest,
    runtime: FullCompilerRuntime | None = None,
) -> FullCompilationResponse:
    if not user.is_simulated:
        raise PolicyLifecycleError("SIMULATION_REQUIRED", "仅支持当前模拟用户", 403)
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "编译需服务端带时区时钟", 422)
    try:
        local = now.astimezone(UTC).astimezone(ZoneInfo(user.timezone))
        context = CompileContext(reference_date=local.date(), timezone=user.timezone)
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as error:
        raise PolicyLifecycleError(
            "INVALID_CONTEXT", "当前用户时区或本地日期不可用", 409
        ) from error
    try:
        result = compile_full_policy(body.text, context)
    except (ValueError, TypeError) as error:
        raise PolicyLifecycleError("INVALID_TEXT", "原句需非空并在有限编译容量内", 422) from error
    if body.engine == "llm":
        result = _model_candidate(body, context, result, runtime or FullCompilerRuntime())
    comparison_source: Literal["NONE", "USER_PROVIDED_CANDIDATE_NOT_CURRENT_VERSION"] = "NONE"
    if body.comparison_candidate is not None:
        try:
            before = validate_full_configuration(
                body.comparison_candidate.template_name,
                strict_candidate_json(body.comparison_candidate.configuration),
            )
        except (ValueError, TypeError, RecursionError) as error:
            raise PolicyLifecycleError(
                "INVALID_COMPARISON", "比较对象不是完整严格候选", 422
            ) from error
        comparison_source = "USER_PROVIDED_CANDIDATE_NOT_CURRENT_VERSION"
        if result.configuration is not None:
            result = result.model_copy(
                update={"differences": explain_candidate_diff(before, result.configuration)}
            )
    return FullCompilationResponse(
        user_id=user.id,
        reference_date=local.date(),
        timezone=user.timezone,
        compilation=result,
        comparison_source=comparison_source,
    )
