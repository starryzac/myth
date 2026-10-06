"""Finite, offline policy grammar. A compiled candidate is never authorization."""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.policy_configuration import validate_configuration
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

COMPILER_VERSION = "offline-policy-rules-v1"
_DIGITS = "零一二三四五六七八九"
_CHINESE = "零〇一二两三四五六七八九十百千万"
_MONEY = (
    rf"(?:[0-9]+(?:\.[0-9]+)?[万千百]?|[{_CHINESE}]+"
    r"(?:点[零〇一二三四五六七八九]+[万千百]?)?)(?:元|块)?"
)
_DAY_NUMBER = r"(?:[0-9]+|[零〇一二两三四五六七八九十]+)"
_DATE = (
    rf"(?:[0-9]{{4}}-[0-9]{{1,2}}-[0-9]{{1,2}}|"
    rf"(?:[0-9]{{4}}年|明年|今年){_DAY_NUMBER}月(?:{_DAY_NUMBER}[日号])?)"
)
_PURPOSE = r"(?:买车|旅行|旅游|应急金)"


def _chinese_spelling(number: int) -> str:
    if number == 0:
        return "零"
    if number >= 10_000:
        high, low = divmod(number, 10_000)
        return (
            _chinese_spelling(high)
            + "万"
            + (("零" if low < 1000 else "") + _chinese_spelling(low) if low else "")
        )
    result = ""
    pending_zero = False
    for scale, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, number = divmod(number, scale)
        if digit:
            if pending_zero:
                result += "零"
            result += ("" if digit == 1 and scale == 10 and not result else _DIGITS[digit]) + unit
            pending_zero = False
        elif result and number:
            pending_zero = True
    return result


def _chinese_integer(token: str) -> int:
    token = token.replace("两", "二").replace("〇", "零")
    if token.count("万") > 1:
        raise ValueError("中文金额仅支持到万位组，请使用明确的阿拉伯数字。")
    total = section = number = 0
    for character in token:
        if character in _DIGITS:
            number = _DIGITS.index(character)
        elif character == "万":
            total += (section + number) * 10_000
            section = number = 0
        elif character in "十百千":
            section += (number or 1) * {"十": 10, "百": 100, "千": 1000}[character]
            number = 0
        else:
            raise ValueError("无法明确解析中文金额。")
    result = total + section + number
    if result >= 100_000_000 or _chinese_spelling(result) != token:
        raise ValueError("中文金额存在省略或歧义，请写完整数词或阿拉伯数字。")
    return result


def _money_cents(token: str) -> int:
    token = token.removesuffix("元").removesuffix("块")
    multiplier = 1
    if token[0].isdigit():
        if token[-1] in "万千百":
            multiplier = {"万": 10_000, "千": 1000, "百": 100}[token[-1]]
            token = token[:-1]
        decimal_text = token
    elif "点" in token:
        if token[-1] in "万千百":
            multiplier = {"万": 10_000, "千": 1000, "百": 100}[token[-1]]
            token = token[:-1]
        whole, fraction = token.split("点")
        decimal_text = (
            str(_chinese_integer(whole))
            + "."
            + "".join(str(_DIGITS.index(character.replace("〇", "零"))) for character in fraction)
        )
    else:
        decimal_text = str(_chinese_integer(token))
    with localcontext() as arithmetic:
        arithmetic.prec = max(64, len(decimal_text) * 2 + 10)
        try:
            cents = Decimal(decimal_text) * multiplier * 100
        except InvalidOperation as error:
            raise ValueError("金额必须是精确十进制数。") from error
        if cents != cents.to_integral_value():
            raise ValueError("金额必须能精确表示为整数分，不能四舍五入。")
        if not 0 <= cents <= 9_223_372_036_854_775_807:
            raise ValueError("金额超出整数分存储范围。")
        return int(cents)


class CompileContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reference_date: date
    timezone: str

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("A valid IANA timezone is required") from error
        return value


class CompilationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    field: str
    message: str
    source_fragment: str


class CompilationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    compiler_version: str
    reference_date: date
    timezone: str
    draft: dict[str, Any]
    configuration: dict[str, Any] | None
    issues: list[CompilationIssue] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class _Parser:
    def __init__(self, context: CompileContext) -> None:
        self.context = context
        self.draft: dict[str, Any] = {}
        self.issues: list[CompilationIssue] = []
        self.assumptions: list[str] = []
        self.fragments: dict[str, str] = {}
        self.monthly_context = False

    def issue(self, code: str, field: str, message: str, source: str = "") -> None:
        self.issues.append(
            CompilationIssue(
                code=code,
                field=field,
                message=message,
                source_fragment=source,
            )
        )

    def assume(self, message: str) -> None:
        if message not in self.assumptions:
            self.assumptions.append(message)

    def put(self, field: str, value: Any, source: str) -> None:
        target = self.draft
        *parents, key = field.split(".")
        for parent in parents:
            target = target.setdefault(parent, {})
        if key in target and target[key] != value:
            self.issue("CONFLICTING_VALUE", field, "同一字段有不同声明，请保留一个明确值。", source)
            return
        target[key] = value
        self.fragments[field] = source

    def intent(self, kind: str, name: str, source: str) -> bool:
        if self.draft.get("type") not in (None, kind) or self.draft.get("name") not in (None, name):
            self.issue("MULTIPLE_INTENTS", "type", "一次编译只处理一个明确目标或应急缓冲。", source)
            return False
        self.put("type", kind, source)
        self.put("name", name, source)
        return True

    def money(self, token: str) -> int:
        if not token.endswith(("元", "块")):
            self.assume("金额槽位中未写币种的数值按人民币元解释，请在确认前核对。")
        return _money_cents(token)

    def calendar(self, token: str, before: bool = False) -> str:
        absolute = re.fullmatch(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})", token)
        if absolute:
            result = date(int(absolute[1]), int(absolute[2]), int(absolute[3]))
        else:
            localized = re.fullmatch(
                rf"(?:([0-9]{{4}})年|(明年|今年))({_DAY_NUMBER})月(?:({_DAY_NUMBER})[日号])?",
                token,
            )
            if localized is None:
                raise ValueError("请写明确的年月日。")
            year = (
                int(localized[1])
                if localized[1]
                else (self.context.reference_date.year + (1 if localized[2] == "明年" else 0))
            )
            if localized[2]:
                self.assume(
                    f"相对日期以 {self.context.reference_date.isoformat()}"
                    f"（{self.context.timezone}）为锚点，"
                    f"{localized[2]}解释为{year}年。"
                )
            month = int(localized[3]) if localized[3].isdigit() else _chinese_integer(localized[3])
            if localized[4] is None:
                if not before:
                    raise ValueError("只给出月份时无法确定截止日，请补充日期或明确使用该月之前。")
                day = 1
            else:
                day = (
                    int(localized[4]) if localized[4].isdigit() else _chinese_integer(localized[4])
                )
            result = date(year, month, day)
        if before:
            result -= timedelta(days=1)
            self.assume(
                f"严格的“前/之前”解释为该日期或月份开始前一天，截止日为{result.isoformat()}。"
            )
        return result.isoformat()

    def monthly(self, text: str, source: str) -> bool:
        scoped = re.fullmatch(r"(?:每月|每个月)(.*)", text)
        if scoped:
            self.monthly_context = True
            remainder = scoped[1]
        elif self.monthly_context:
            remainder = text
        else:
            return False
        ranged = re.fullmatch(rf"(?:储备|存|储蓄)?({_MONEY})(?:到|至|-|~)({_MONEY})", remainder)
        if ranged:
            if (
                not ranged[1].endswith(("元", "块"))
                and not any(scale in ranged[1] for scale in "万千百")
                and any(scale in ranged[2] for scale in "万千百")
            ):
                self.issue(
                    "AMBIGUOUS_AMOUNT_UNIT",
                    "monthly_contribution",
                    "范围两端的金额单位不明确，请分别写出单位。",
                    source,
                )
                return True
            self.put("monthly_contribution.min_cents", self.money(ranged[1]), source)
            self.put("monthly_contribution.max_cents", self.money(ranged[2]), source)
            return True
        fixed = re.fullmatch(rf"固定(?:储备|存|储蓄)?({_MONEY})", remainder)
        if fixed:
            amount = self.money(fixed[1])
            for field in ("min_cents", "target_cents", "max_cents"):
                self.put(f"monthly_contribution.{field}", amount, source)
            self.assume("明确的固定月额展开为相同的月度最低、建议和最高储备额。")
            return True
        part = re.fullmatch(
            rf"(至少|最低|最少|尽量|建议|目标|最多|最高)(?:储备|存|储蓄)?({_MONEY})", remainder
        )
        if part:
            field = (
                "min_cents"
                if part[1] in {"至少", "最低", "最少"}
                else "max_cents"
                if part[1] in {"最多", "最高"}
                else "target_cents"
            )
            self.put(f"monthly_contribution.{field}", self.money(part[2]), source)
            return True
        return False

    def consume(self, text: str, source: str) -> None:
        window = re.fullmatch(rf"有效期(?:从|自|为)?({_DATE})(?:至|到)({_DATE})", text)
        if window:
            self.put("valid_from", self.calendar(window[1]), source)
            self.put("valid_until", self.calendar(window[2]), source)
            return
        start = re.fullmatch(rf"(?:自|从)({_DATE})(?:起|开始生效)", text)
        if start:
            self.put("valid_from", self.calendar(start[1]), source)
            return
        end = re.fullmatch(rf"(?:有效至|有效期至)({_DATE})", text)
        if end:
            self.put("valid_until", self.calendar(end[1]), source)
            return
        if text in {"资金别锁太久", "别锁太久", "资金不要锁太久"}:
            self.issue(
                "UNRESOLVED_LOCK_LIMIT",
                "max_lock_days",
                "请明确最长锁定天数；不会据此生成资产授权。",
                source,
            )
            return
        compound = re.fullmatch(
            rf"(?:我)?(?:想|希望)?(?:在|到)?({_DATE})(之前|前)?(?:想|希望)?"
            rf"(?:攒到|攒够|攒下|攒|存到|存够|存)({_MONEY})(?:用于)?({_PURPOSE})",
            text,
        )
        if compound:
            if self.intent("goal_saving", "旅行" if compound[4] == "旅游" else compound[4], source):
                self.put("target_cents", self.money(compound[3]), source)
                self.put("deadline", self.calendar(compound[1], bool(compound[2])), source)
            return
        reserve = re.fullmatch(rf"(?:始终)?(?:保留|留出)({_MONEY})应急金", text)
        if reserve:
            if self.intent("emergency_buffer", "应急金", source):
                self.put("amount_cents", self.money(reserve[1]), source)
            return
        goal = re.fullmatch(rf"(买车|旅行|旅游)目标(?:为|是)?({_MONEY})", text)
        if goal:
            if self.intent("goal_saving", "旅行" if goal[1] == "旅游" else goal[1], source):
                self.put("target_cents", self.money(goal[2]), source)
            return
        deadline = re.fullmatch(rf"(?:目标)?(?:截止|期限为|在|到)?({_DATE})(之前|前)?", text)
        if deadline:
            self.put("deadline", self.calendar(deadline[1], bool(deadline[2])), source)
            return
        if self.monthly(text, source):
            return
        self.issue("UNSUPPORTED_CLAUSE", "text", "该子句未被完整理解，请改写或明确修订。", source)

    def finish(self) -> CompilationResult:
        configuration: dict[str, Any] | None = None
        kind = self.draft.get("type")
        for field in ("deadline", "valid_until"):
            value = self.draft.get(field)
            if isinstance(value, str) and date.fromisoformat(value) < self.context.reference_date:
                self.issue(
                    "PAST_DATE",
                    field,
                    "该日期早于当前编译日期，请明确修订。",
                    self.fragments.get(field, ""),
                )
        if kind is None:
            self.issue(
                "MISSING_INTENT", "type", "请明确一个买车、旅行、应急储蓄目标或立即应急缓冲。"
            )
        if kind == "goal_saving":
            for field in ("target_cents", "deadline"):
                if field not in self.draft:
                    self.issue("MISSING_FIELD", field, "请补充目标金额或具体截止日期。")
            for field in ("min_cents", "target_cents", "max_cents"):
                if field not in self.draft.get("monthly_contribution", {}):
                    self.issue(
                        "MISSING_FIELD",
                        f"monthly_contribution.{field}",
                        "请明确月度最低、建议及最高储备额。",
                    )
            self.assume(
                "priority 默认 importance=50、minimum_cents=0、"
                "reducible=false、deferrable=false，须由用户复核。"
            )
            self.assume(
                "cross_goal_reallocation_allowed=false；asset_policy_id=null，不生成资产授权或跨目标许可。"
            )
        if self.draft.get("valid_from") is None or self.draft.get("valid_until") is None:
            self.assume("未指定的有效期日期保留空值，由首次确认和生命周期规则决定生效。")
        if not self.issues:
            try:
                configuration = validate_configuration(self.draft)
            except ValidationError as error:
                for detail in error.errors(include_url=False, include_input=False):
                    location = list(detail["loc"])
                    if location and location[0] == kind:
                        location.pop(0)
                    field = ".".join(str(part) for part in location) or "configuration"
                    self.issue(
                        "INVALID_CONFIGURATION", field, detail["msg"], self.fragments.get(field, "")
                    )
        return CompilationResult(
            compiler_version=COMPILER_VERSION,
            reference_date=self.context.reference_date,
            timezone=self.context.timezone,
            draft=self.draft,
            configuration=configuration,
            issues=self.issues,
            assumptions=self.assumptions,
        )


def compile_policy(text: str, context: CompileContext) -> CompilationResult:
    """Compile against an explicit date anchor, with no clock, database or network access."""
    if not isinstance(text, str):
        raise ValueError("Policy text must be a string")
    parser = _Parser(context)
    if not text.strip() or len(text) > 2000:
        parser.issue("INVALID_TEXT", "text", "请输入1至2000个字符的策略描述。")
        return parser.finish()
    for source in re.split(r"[，,。；;、\n\r]+", text):
        if not source.strip():
            continue
        normalized = unicodedata.normalize("NFKC", source)
        if re.search(rf"[0-9{_CHINESE}]\s+[0-9{_CHINESE}]", normalized):
            parser.issue(
                "AMBIGUOUS_NUMBER_SPACING",
                "text",
                "数字之间存在空白，不能将多个数字自动合并。",
                source,
            )
            continue
        normalized = re.sub(r"\s+", "", normalized)
        try:
            parser.consume(normalized, source)
        except (ValueError, OverflowError) as error:
            parser.issue("INVALID_VALUE", "text", str(error), source)
    return parser.finish()
