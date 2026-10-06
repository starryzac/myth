"""Bounded offline FULL candidate grammar. Compilation never creates authority."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any, Literal

from app.domain.full_policy_configuration import (
    FULL_TYPE_MAPPING,
    TemplateName,
    template_names,
    validate_full_configuration,
)
from app.domain.policy_compiler import CompileContext
from app.domain.policy_configuration import StrictModel, configuration_hash
from pydantic import Field, ValidationError

COMPILER_VERSION = "full-offline-candidate-rules-v1"
MAX_TEXT_CHARS = 4000
MAX_CANDIDATE_BYTES = 65536
_MONEY = r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?(?:万元|元|分)"
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_LIST = r"[^，,；;。\n]+"
_PAYEE = r"[A-Za-z][A-Za-z0-9_.:-]{0,159}"
_HEADERS: dict[TemplateName, str] = {
    "RecurringObligationPolicy": "周期义务",
    "LivingReservePolicy": "生活储备",
    "EmergencyBufferPolicy": "应急缓冲",
    "DatedExpensePolicy": "日期支出",
    "LongTermGoalPolicy": "长期目标",
    "PeriodicTransferPolicy": "定期转账",
    "AssetAuthorizationPolicy": "资产配置",
    "RecoveryPolicy": "回撤规则",
    "GoalAllocationPolicy": "目标分配",
    "CrossGoalReallocationPolicy": "跨目标应急调拨",
    "SeasonalReservePolicy": "节日储备建议",
    "InterventionPolicy": "介入规则",
}
_ASSETS = {
    "现金": "CASH",
    "T+0现金管理": "CASH_MGMT_T0",
    "T+1现金管理": "CASH_MGMT_T1",
    "7天定存": "FIXED_DEPOSIT_7D",
    "30天定存": "FIXED_DEPOSIT_30D",
    "90天定存": "FIXED_DEPOSIT_90D",
    "低风险定期": "LOW_RISK_TERM",
}
_TRIGGERS = {
    "边界收缩": "BOUNDARY_SHRINK",
    "授权撤销": "AUTHORIZATION_REVOKED",
    "策略到期": "POLICY_EXPIRED",
    "流动性不足": "LIQUIDITY_SHORTFALL",
}
_EMERGENCIES = {
    "硬义务不足": "HARD_OBLIGATION_SHORTFALL",
    "生活储备不足": "LIVING_RESERVE_SHORTFALL",
    "应急缓冲不足": "EMERGENCY_BUFFER_SHORTFALL",
}


class SourceFragment(StrictModel):
    field: str
    start: int
    end: int
    redacted_text: str
    original_fragment_sha256: str


class FullCompilationIssue(StrictModel):
    code: str
    field: str
    message: str
    source_fragment: str = ""


class CandidateDifference(StrictModel):
    field: str
    before: Any
    after: Any
    explanation: str


class FullCompilationResult(StrictModel):
    simulation: Literal[True] = True
    compiler_version: str = COMPILER_VERSION
    dsl_version: Literal["FULL_V1"] = "FULL_V1"
    engine: Literal["rules", "llm"] = "rules"
    status: Literal["READY_FOR_REVIEW", "MISSING", "AMBIGUOUS", "UNKNOWN", "REVIEW_REQUIRED"]
    template_name: TemplateName | None
    original_text_sha256: str
    redacted_source_text: str
    draft: dict[str, Any]
    configuration: dict[str, Any] | None
    configuration_hash: str | None
    source_fragments: list[SourceFragment] = Field(default_factory=list)
    issues: list[FullCompilationIssue] = Field(default_factory=list)
    defaulted_fields: list[str] = Field(default_factory=list)
    differences: list[CandidateDifference] = Field(default_factory=list)
    summary: str
    evidence_level: Literal["USER_DECLARED", "MODEL_INFERRED"] = "USER_DECLARED"
    requires_confirmation: Literal[True] = True
    grants_authority: Literal[False] = False
    bank_authority: Literal[False] = False
    policy_created: Literal[False] = False
    reference_validation: Literal["NOT_SERVER_VERIFIED"] = "NOT_SERVER_VERIFIED"
    manual_review_required: bool = True
    privacy_redactions: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True)
class RedactedInput:
    text: str
    # This mapping remains local; providers receive only text and calendar context.
    references: dict[str, str]
    counts: dict[str, int]


_PII: tuple[tuple[str, str], ...] = (
    ("EMAIL", r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("PHONE_OR_ACCOUNT_OR_ID", r"(?<![0-9])[0-9]{11,19}[Xx]?(?![0-9])"),
    ("NAME", r"(?:姓名|身份证姓名)\s*[:：]?\s*[^，,；;。\n]+"),
    ("ADDRESS", r"(?:住址|家庭地址)\s*[:：]?\s*[^，,；;。\n]+"),
    ("SECRET", r"(?:密码|口令|token|api_key)\s*[:：=]\s*[^，,；;。\n]+"),
)


def redact_full_policy_text(text: str) -> RedactedInput:
    """No raw identity mapping is supplied to an optional provider or public log."""
    references: dict[str, str] = {}
    counts: dict[str, int] = {}

    def reference(value: str) -> str:
        for token, original in references.items():
            if original == value:
                return token
        token = f"[REFERENCE_{len(references) + 1}]"
        references[token] = value
        return token

    value = re.sub(_UUID, lambda match: reference(match[0]), text)
    value = re.sub(rf"(收款人)({_PAYEE})", lambda match: match[1] + reference(match[2]), value)
    value = re.sub(
        r"(名称[“\"])([^”\"\n]+)([”\"])",
        lambda match: match[1] + reference(match[2]) + match[3],
        value,
    )
    for kind, pattern in _PII:
        value, count = re.subn(pattern, f"[{kind}]", value, flags=re.IGNORECASE)
        if count:
            counts[kind] = count
    if references:
        counts["REFERENCE"] = len(references)
    return RedactedInput(value, references, counts)


def _public(value: Any) -> Any:
    if isinstance(value, str):
        return redact_full_policy_text(value).text
    if isinstance(value, dict):
        return {key: _public(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_public(child) for child in value]
    return value


def strict_candidate_json(value: Any) -> dict[str, Any]:
    """Finite JSON admission, independently before the original twelve schemas."""
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 4096 or depth > 16:
            raise ValueError("Candidate JSON exceeds structural capacity")
        if type(item) in (str, int, float, bool) or item is None:
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                visit(child, depth + 1)
            return
        raise ValueError("Only exact JSON types are admitted")

    if type(value) is not dict:
        raise ValueError("A candidate object is required")
    visit(value, 0)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_CANDIDATE_BYTES:
        raise ValueError("Candidate JSON exceeds byte capacity")
    return value


def _money(token: str) -> int:
    unit = next(unit for unit in ("万元", "元", "分") if token.endswith(unit))
    with localcontext() as context:
        context.prec = max(64, len(token) * 2)
        amount = Decimal(token.removesuffix(unit)) * {"万元": 1_000_000, "元": 100, "分": 1}[unit]
    if amount != amount.to_integral_value() or not 0 <= amount <= 9_223_372_036_854_775_807:
        raise ValueError("Money must be exact integral cents within signed int64")
    return int(amount)


def _items(token: str, mapping: dict[str, str] | None = None) -> list[str]:
    values = [value.strip() for value in token.split("、")]
    if not values or any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError("List items must be explicit and unique")
    if mapping is not None:
        return [mapping[value] for value in values]
    return values


@dataclass(frozen=True)
class _Slot:
    pattern: str
    values: Callable[[re.Match[str]], dict[str, Any]]


def _slot(field: str, pattern: str, convert: Callable[[str], Any] = str) -> _Slot:
    return _Slot(pattern, lambda match: {field: convert(match[1])})


def _fixed(field: str, pattern: str, value: Any) -> _Slot:
    return _Slot(pattern, lambda _match: {field: value})


def _window(prefix: str) -> _Slot:
    return _Slot(
        rf"{prefix}({_DATE})至({_DATE})",
        lambda match: {"window.start": match[1], "window.end": match[2]},
    )


def _contribution(prefix: str, target: str) -> _Slot:
    return _Slot(
        rf"{prefix}最低({_MONEY})、目标({_MONEY})、最高({_MONEY})",
        lambda match: {
            f"{target}.min_cents": _money(match[1]),
            f"{target}.target_cents": _money(match[2]),
            f"{target}.max_cents": _money(match[3]),
        },
    )


def _flags(field: str, phrase: str) -> list[_Slot]:
    return [_fixed(field, "不允许" + phrase, False), _fixed(field, "(?<!不)允许" + phrase, True)]


def _slots(template: TemplateName) -> list[_Slot]:
    common = [
        _slot("name", r"名称[“\"]([^”\"\n]{1,120})[”\"]"),
        _Slot(
            rf"有效期({_DATE})至({_DATE})",
            lambda match: {"valid_from": match[1], "valid_until": match[2]},
        ),
    ]
    amount = _slot("amount_cents", rf"保留(?:应急金)?({_MONEY})(?:应急金)?", _money)
    due = _slot("due_day", r"每月([0-9]+)日", int)
    prepare = _slot("prepare_days_before", r"提前([0-9]+)天(?:准备)?", int)
    payee = _slot("payee_id", rf"(?:向|给)?收款人({_PAYEE})")
    auto = [
        _fixed("auto_execute", "不自动执行", False),
        _fixed("auto_execute", "(?<!不)自动执行", True),
    ]
    exact = _slot(
        "amount_rule",
        rf"(?:支付|转账|金额)({_MONEY})",
        lambda token: {"kind": "exact", "amount_cents": _money(token)},
    )
    ranged = _Slot(
        rf"金额({_MONEY})至({_MONEY})",
        lambda match: {
            "amount_rule": {
                "kind": "range",
                "min_cents": _money(match[1]),
                "max_cents": _money(match[2]),
            }
        },
    )
    scope = [
        _fixed("scope", "一般闲钱", "general_idle_funds"),
        _Slot(rf"仅目标({_UUID})", lambda match: {"scope": "goal", "goal_id": match[1]}),
    ]
    cap = _slot("single_action_cap_cents", rf"单次上限({_MONEY})", _money)
    delay = _slot("max_redemption_delay_days", r"赎回延迟最多([0-9]+)天", int)
    essential = _slot("essential_categories", rf"必要类别({_LIST})", _items)
    quantile = _slot(
        "quantile", r"([0-9]+(?:\.[0-9]+)?)%分位", lambda token: float(Decimal(token) / 100)
    )
    match template:
        case "RecurringObligationPolicy":
            specific = [due, prepare, payee, ranged, exact, *auto]
        case "EmergencyBufferPolicy":
            specific = [amount]
        case "LivingReservePolicy":
            specific = [
                _slot("horizon_days", r"覆盖([0-9]+)天", int),
                _slot("method.lookback_days", r"回看([0-9]+)天", int),
                _slot(
                    "method.quantile", quantile.pattern, lambda token: float(Decimal(token) / 100)
                ),
                _slot("method.essential_categories", essential.pattern, _items),
                _fixed("method.name", "滚动分位", "rolling_window_quantile"),
                _fixed("method.exclude_one_off", "排除一次性消费", True),
                _slot("extra_buffer_cents", rf"额外缓冲({_MONEY})", _money),
            ]
        case "DatedExpensePolicy":
            specific = [
                _window("窗口"),
                _contribution("金额", "amount"),
                _slot("must_not_reduce_policy_ids", rf"不能减少策略({_LIST})", _items),
            ]
        case "LongTermGoalPolicy":
            specific = [
                _Slot(
                    rf"到({_DATE})攒({_MONEY})",
                    lambda match: {"deadline": match[1], "target_cents": _money(match[2])},
                ),
                _contribution("每月", "monthly_contribution"),
                _slot("importance", r"重要性([0-9]+)", int),
                _slot("minimum_guarantee_cents", rf"最低保障({_MONEY})", _money),
                *_flags("allow_partial", "部分完成"),
                *_flags("allow_deferral", "延期"),
                _slot("deferral_cost_cents_per_day", rf"延期每天代价({_MONEY})", _money),
                _slot("asset_policy_id", rf"资产策略({_UUID})"),
                _fixed("cross_goal_reallocation_allowed", "不允许跨目标调拨", False),
            ]
        case "PeriodicTransferPolicy":
            specific = [
                _slot("source_account_id", rf"从账户({_UUID})"),
                due,
                prepare,
                payee,
                ranged,
                exact,
                cap,
                *auto,
            ]
        case "AssetAuthorizationPolicy":
            specific = [
                *scope,
                cap,
                delay,
                _slot(
                    "allowed_asset_classes",
                    rf"仅允许({_LIST})",
                    lambda token: _items(token, _ASSETS),
                ),
                _slot("max_auto_managed_cents", rf"管理上限({_MONEY})", _money),
                _slot("max_lock_days", r"锁定最多([0-9]+)天", int),
                _slot("max_principal_risk_level", r"风险等级([0-9]+)", int),
                *_flags("allow_auto_recovery_without_penalty", "无损自动恢复"),
                *_flags("allow_early_withdrawal_with_penalty", "有损提前取出"),
            ]
        case "RecoveryPolicy":
            specific = [
                *scope,
                cap,
                delay,
                _slot("asset_policy_id", rf"资产策略({_UUID})"),
                _slot("triggers", rf"触发({_LIST})", lambda token: _items(token, _TRIGGERS)),
                *_flags("allow_auto_recovery_without_penalty", "无损自动恢复"),
                _fixed("max_fee_cents", "费用必须为零", 0),
                _fixed("max_loss_cents", "损失必须为零", 0),
            ]
        case "GoalAllocationPolicy":
            specific = [
                _slot("goal_ids", rf"目标({_UUID}(?:、{_UUID})*)", _items),
                _slot("max_single_allocation_cents", rf"单次分配上限({_MONEY})", _money),
                _fixed("method", "按词典序", "lexicographic_v1"),
                _fixed("funds_scope", "仅新安全未归属资金", "NEW_UNASSIGNED_SAFE_FUNDS"),
            ]
        case "CrossGoalReallocationPolicy":
            specific = [
                _fixed("enabled", "禁用", False),
                _fixed("enabled", "启用", True),
                cap,
                _slot("source_goal_ids", rf"来源目标({_UUID}(?:、{_UUID})*)", _items),
                _slot(
                    "emergency_conditions",
                    rf"紧急条件({_LIST})",
                    lambda token: _items(token, _EMERGENCIES),
                ),
                _slot("total_cap_cents", rf"总上限({_MONEY})", _money),
                _fixed("destination_scope", "仅保护现金", "PROTECTED_CASH"),
            ]
        case "SeasonalReservePolicy":
            specific = [
                _slot("holiday_code", r"节日代码([A-Z][A-Z0-9_]{0,47})"),
                _window("窗口"),
                _slot("lookback_days", r"回看([0-9]+)天", int),
                _slot("minimum_historical_windows", r"历史窗口至少([0-9]+)个", int),
                quantile,
                essential,
                _slot("adjustment_cap_cents", rf"调整上限({_MONEY})", _money),
                _Slot(
                    "仅建议且需确认",
                    lambda _match: {"requires_confirmation": True, "advice_only": True},
                ),
            ]
        case "InterventionPolicy":
            specific = [
                _slot("minimum_reask_interval_seconds", r"重复问答间隔([0-9]+)秒", int),
                _fixed("deduplicate_by_boundary_event", "按边界去重", True),
                _fixed("safety_events_bypass_throttle", "安全事件不节流", True),
                _fixed("silent_when_action_set_unchanged", "动作集合不变静默", True),
            ]
    return common + specific


def flatten_candidate(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        field = prefix + key
        if isinstance(item, dict):
            result.update(flatten_candidate(item, field + "."))
        else:
            result[field] = item
    return result


def explain_candidate_diff(
    before: dict[str, Any], after: dict[str, Any]
) -> list[CandidateDifference]:
    left, right = flatten_candidate(before), flatten_candidate(after)
    return [
        CandidateDifference(
            field=field,
            before=_public(left.get(field)),
            after=_public(right.get(field)),
            explanation="候选字段变化；原策略版本、已确认权限与经济后果未改变，需重新独立预览并明确确认。",
        )
        for field in sorted(left.keys() | right.keys())
        if left.get(field) != right.get(field)
    ]


def _yuan(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}元"


def _summary(template: TemplateName | None, configuration: dict[str, Any] | None) -> str:
    suffix = "仅候选；默认字段须复核，原引用未验真，未确认策略或授银行权限。"
    if template is None or configuration is None:
        return "原句不完整、有歧义或含未支持片段，请先补充或修改明确原值。" + suffix
    value = configuration

    def amount() -> str:
        rule = value["amount_rule"]
        return (
            _yuan(rule["amount_cents"])
            if rule["kind"] == "exact"
            else (_yuan(rule["min_cents"]) + "至" + _yuan(rule["max_cents"]))
        )

    def monthly(field: str) -> str:
        item = value[field]
        return (
            f"最低{_yuan(item['min_cents'])}、目标{_yuan(item['target_cents'])}、"
            f"最高{_yuan(item['max_cents'])}"
        )

    match template:
        case "RecurringObligationPolicy" | "PeriodicTransferPolicy":
            description = (
                f"每月{value['due_day']}日，付款范围{amount()}，"
                f"提前{value['prepare_days_before']}天准备。"
            )
            if template == "PeriodicTransferPolicy":
                description += (
                    f"单次上限{_yuan(value['single_action_cap_cents'])}，声明的源账户仍待验真。"
                )
        case "LivingReservePolicy":
            description = (
                f"保护{value['horizon_days']}天生活支出；回看{value['method']['lookback_days']}天，"
                f"分位{value['method']['quantile']}，额外缓冲{_yuan(value['extra_buffer_cents'])}。"
                "未在本次编译计算实际生活储备。"
            )
        case "EmergencyBufferPolicy":
            description = f"保留应急缓冲{_yuan(value['amount_cents'])}。"
        case "DatedExpensePolicy":
            description = (
                f"支出窗口{value['window']['start']}至{value['window']['end']}，"
                f"{monthly('amount')}。"
            )
        case "LongTermGoalPolicy":
            description = (
                f"目标{_yuan(value['target_cents'])}，截止{value['deadline']}；"
                f"每月{monthly('monthly_contribution')}；最低保障{_yuan(value['minimum_guarantee_cents'])}。"
            )
        case "AssetAuthorizationPolicy":
            description = (
                f"声明资产范围{value['scope']}，类别{value['allowed_asset_classes']}；"
                f"总额{_yuan(value['max_auto_managed_cents'])}、单次{_yuan(value['single_action_cap_cents'])}，"
                f"锁定最多{value['max_lock_days']}天、赎回延迟最多{value['max_redemption_delay_days']}天。"
                "产品适配与银行权限尚未验证。"
            )
        case "RecoveryPolicy":
            description = (
                f"恢复范围{value['scope']}，单次{_yuan(value['single_action_cap_cents'])}，"
                f"延迟最多{value['max_redemption_delay_days']}天；费用与损失必须为零。"
            )
        case "GoalAllocationPolicy":
            description = (
                f"{len(value['goal_ids'])}个声明目标，仅新安全未归属资金，"
                f"词典序分配单次上限{_yuan(value['max_single_allocation_cents'])}。"
            )
        case "CrossGoalReallocationPolicy":
            description = (
                "跨目标应急调拨候选启用；" if value["enabled"] else "跨目标应急调拨候选禁用；"
            )
            description += (
                f"单次{_yuan(value['single_action_cap_cents'])}，"
                f"总额{_yuan(value['total_cap_cents'])}。"
            )
        case "SeasonalReservePolicy":
            description = (
                f"节日代码{value['holiday_code']}，窗口{value['window']['start']}至{value['window']['end']}；"
                f"要求至少{value['minimum_historical_windows']}个历史窗口，调整上限{_yuan(value['adjustment_cap_cents'])}。"
                "仅建议且需确认，未在本次编译证明历史样本或计算调整额。"
            )
        case "InterventionPolicy":
            description = (
                f"重复问答间隔{value['minimum_reask_interval_seconds']}秒；"
                "按边界去重，安全与新权限/损失/风险等必须询问，动作集合不变静默。"
            )
    return description + suffix


def compile_full_policy(text: str, context: CompileContext) -> FullCompilationResult:
    if type(text) is not str or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ValueError("A nonempty policy text up to 4000 characters is required")
    redacted = redact_full_policy_text(text)
    issues: list[FullCompilationIssue] = []
    fragments: list[SourceFragment] = []
    covered = [False] * len(text)
    draft: dict[str, Any] = {}

    def issue(code: str, field: str, message: str, source: str = "") -> None:
        issues.append(
            FullCompilationIssue(
                code=code,
                field=field,
                message=message,
                source_fragment=redact_full_policy_text(source).text,
            )
        )

    def consume(match: re.Match[str], field: str) -> None:
        covered[match.start() : match.end()] = [True] * (match.end() - match.start())
        fragments.append(
            SourceFragment(
                field=field,
                start=match.start(),
                end=match.end(),
                redacted_text=redact_full_policy_text(match[0]).text,
                original_fragment_sha256=hashlib.sha256(match[0].encode()).hexdigest(),
            )
        )

    def put(field: str, value: Any, match: re.Match[str]) -> None:
        cursor = draft
        *parents, leaf = field.split(".")
        for parent in parents:
            cursor = cursor.setdefault(parent, {})
        if leaf in cursor and cursor[leaf] != value:
            issue("CONFLICTING_VALUE", field, "同字段有不同明确声明，请保留一个原值。", match[0])
        else:
            cursor[leaf] = value
        consume(match, field)

    selected: list[tuple[TemplateName, re.Match[str]]] = []
    for candidate_template, header in _HEADERS.items():
        selected.extend(
            (candidate_template, match) for match in re.finditer(header + "[:：]", text)
        )
    template: TemplateName | None
    if not selected and re.search(r"保留(?:应急金)?" + _MONEY + r"(?:应急金)", text):
        template = "EmergencyBufferPolicy"
    elif len(selected) == 1:
        template, header_match = selected[0]
        consume(header_match, "template_name")
    else:
        template = None
        issue(
            "MULTIPLE_TEMPLATES" if selected else "UNSUPPORTED_INTENT",
            "template_name",
            "一次只编译一个明确模板；请使用已列出的受控自然句式。",
        )
    if template is not None:
        draft["type"] = FULL_TYPE_MAPPING[template]
        for slot in _slots(template):
            for match in re.finditer(slot.pattern, text):
                # Avoid interpreting the first half of a range as an exact amount.
                if slot.pattern.startswith("(?:支付|转账|金额)") and text[match.end() :].startswith(
                    "至"
                ):
                    continue
                try:
                    for field, value in slot.values(match).items():
                        put(field, value, match)
                except (ValueError, KeyError, ArithmeticError) as error:
                    consume(match, "invalid_value")
                    issue(
                        "INVALID_EXPLICIT_VALUE",
                        "configuration",
                        "原金额、列表或日期值不能精确解析，不作猜测或取整。",
                        type(error).__name__,
                    )
        if template == "LivingReservePolicy" and "method" in draft:
            # Fixed named algorithm, not a guessed historical reserve amount.
            draft["method"].setdefault("name", "rolling_window_quantile")
        for kind, count in redacted.counts.items():
            if kind != "REFERENCE" and count:
                issue(
                    "SENSITIVE_TEXT_REQUIRES_EDIT",
                    "text",
                    "原文含需脱敏的身份字段，请从策略句式移除。",
                )
        residue = "".join(
            character if not covered[index] else " " for index, character in enumerate(text)
        )
        if re.sub(r"[\s，,；;。:：]", "", residue):
            issue(
                "UNSUPPORTED_TEXT",
                "text",
                "有未解释片段；不能忽略其金额、否定或权限含义。",
                residue.strip(),
            )

    canonical = None
    if template is not None:
        try:
            canonical = validate_full_configuration(template, strict_candidate_json(draft))
            for field in ("deadline", "valid_until"):
                if (
                    canonical.get(field) is not None
                    and date.fromisoformat(canonical[field]) < context.reference_date
                ):
                    issue("PAST_DATE", field, "日期早于服务器本地锚点，不生成可用候选。")
            if (
                canonical.get("window")
                and date.fromisoformat(canonical["window"]["end"]) < context.reference_date
            ):
                issue("PAST_DATE", "window.end", "支出窗口已结束，历史欠付需另行原事实验证。")
        except ValidationError as error:
            for item in error.errors(include_input=False, include_context=False):
                field = ".".join(str(part) for part in item["loc"])
                issue(
                    "MISSING_FIELD" if item["type"] == "missing" else "SCHEMA_REJECTED",
                    field,
                    "请补充该字段的明确原值。"
                    if item["type"] == "missing"
                    else "严格模板约束拒绝该值。",
                )
        except (TypeError, ValueError, RecursionError):
            issue("SCHEMA_REJECTED", "configuration", "严格独立模板校验失败。")
    defaults = (
        sorted(flatten_candidate(canonical).keys() - flatten_candidate(draft).keys())
        if canonical
        else []
    )
    if issues:
        canonical = None
    codes = {item.code for item in issues}
    status: Literal["READY_FOR_REVIEW", "MISSING", "AMBIGUOUS", "UNKNOWN", "REVIEW_REQUIRED"]
    status = (
        "AMBIGUOUS"
        if codes & {"CONFLICTING_VALUE", "MULTIPLE_TEMPLATES"}
        else (
            "MISSING" if "MISSING_FIELD" in codes else "UNKNOWN" if issues else "READY_FOR_REVIEW"
        )
    )
    return FullCompilationResult(
        status=status,
        template_name=template,
        original_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        redacted_source_text=redacted.text,
        draft=_public(draft),
        configuration=canonical,
        configuration_hash=configuration_hash(canonical) if canonical is not None else None,
        source_fragments=fragments,
        issues=issues,
        defaulted_fields=defaults,
        summary=_summary(template, canonical),
        privacy_redactions=redacted.counts,
    )


def resolve_provider_references(value: dict[str, Any], redacted: RedactedInput) -> dict[str, Any]:
    """Only registered source tokens in precise reference slots can be restored."""
    scalar_refs = {
        "name",
        "source_account_id",
        "payee_id",
        "goal_id",
        "asset_policy_id",
        "cross_goal_reallocation_policy_id",
        "account_id",
    }
    list_refs = {"goal_ids", "source_goal_ids", "must_not_reduce_policy_ids"}

    def walk(item: Any, field: str = "") -> Any:
        if field in scalar_refs and item is not None:
            if type(item) is not str or item not in redacted.references:
                raise ValueError("Provider reference must be an original redaction token")
            return redacted.references[item]
        if field in list_refs:
            if type(item) is not list:
                raise ValueError("Provider reference list is invalid")
            return [walk(child, "goal_id") for child in item]
        if isinstance(item, dict):
            return {key: walk(child, key) for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        return item

    result: dict[str, Any] = walk(strict_candidate_json(value))
    return result


def provider_source_text(text: str, result: FullCompilationResult) -> RedactedInput:
    """Unknown prose is excluded rather than treated as an exhaustive PII detector."""
    covered = [False] * len(text)
    for fragment in result.source_fragments:
        if fragment.field != "invalid_value":
            covered[fragment.start : fragment.end] = [True] * (fragment.end - fragment.start)
    extracted = "".join(
        character if covered[index] else " " for index, character in enumerate(text)
    )
    return redact_full_policy_text(extracted)


EXAMPLES: dict[TemplateName, str] = {
    "RecurringObligationPolicy": (
        "周期义务：每月5日向收款人demo-landlord支付3000元，提前3天，不自动执行"
    ),
    "LivingReservePolicy": (
        "生活储备：覆盖30天，回看180天，90%分位，必要类别餐饮、交通，排除一次性消费，额外缓冲100元"
    ),
    "EmergencyBufferPolicy": "保留3000元应急金",
    "DatedExpensePolicy": (
        "日期支出：窗口2027-02-01至2027-02-07，金额最低1000元、目标2000元、最高3000元"
    ),
    "LongTermGoalPolicy": (
        "长期目标：到2027-12-31攒50000元，每月最低1000元、目标2000元、最高3000元，"
        "不允许延期，不允许部分完成"
    ),
    "PeriodicTransferPolicy": (
        "定期转账：从账户11111111-1111-1111-1111-111111111111，"
        "每月5日向收款人demo-landlord转账1000元，单次上限1500元，不自动执行"
    ),
    "AssetAuthorizationPolicy": (
        "资产配置：一般闲钱，仅允许现金、T+0现金管理、30天定存，管理上限10000元，"
        "单次上限2000元，赎回延迟最多1天，锁定最多30天，风险等级0，不允许有损提前取出"
    ),
    "RecoveryPolicy": (
        "回撤规则：一般闲钱，资产策略11111111-1111-1111-1111-111111111111，"
        "触发边界收缩、授权撤销，单次上限2000元，赎回延迟最多1天，费用必须为零，损失必须为零"
    ),
    "GoalAllocationPolicy": (
        "目标分配：目标11111111-1111-1111-1111-111111111111、"
        "22222222-2222-2222-2222-222222222222，单次分配上限10000元，仅新安全未归属资金，按词典序"
    ),
    "CrossGoalReallocationPolicy": "跨目标应急调拨：禁用",
    "SeasonalReservePolicy": (
        "节日储备建议：节日代码SPRING_FESTIVAL，窗口2027-02-01至2027-02-07，"
        "回看365天，历史窗口至少2个，90%分位，必要类别餐饮、交通，调整上限3000元，仅建议且需确认"
    ),
    "InterventionPolicy": (
        "介入规则：重复问答间隔300秒，按边界去重，安全事件不节流，动作集合不变静默"
    ),
}


def grammar_examples() -> dict[TemplateName, str]:
    if set(EXAMPLES) != set(template_names()):
        raise ValueError("Controlled grammar template inventory drift")
    return dict(EXAMPLES)
