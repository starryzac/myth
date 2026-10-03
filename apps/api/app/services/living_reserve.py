"""Read-only estimates from verified history coverage and user category confirmations."""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from app.db.models import Account, EvidenceItem, Transaction, User
from app.domain.history_coverage import COVERAGE_SOURCE_TYPE, build_history_coverage
from app.domain.living_reserve import (
    MAX_ACCOUNTS,
    MAX_LOOKBACK_DAYS,
    MAX_TRANSACTIONS,
    HistoryCoverage,
    LivingReserveEstimate,
    ReserveEstimateInput,
    ReserveTransaction,
)
from app.domain.living_reserve import estimate_living_reserve as calculate_reserve
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

CATEGORY_SOURCE_TYPE = "SIMULATED_USER_CATEGORY_CONFIRMATION"
NON_CONSUMPTION_ROLES = {
    "INCOME",
    "OPENING",
    "INTERNAL_TRANSFER",
    "ASSET_PURCHASE",
    "CREDIT_CARD_PAYMENT",
}


class ReserveSourceIssue(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    source_ref: str
    message: str


class ReserveEstimationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    estimation: LivingReserveEstimate
    source_evidence_ids: list[UUID]
    input_digest: str
    source_issues: list[ReserveSourceIssue]
    candidate_configuration: dict[str, Any] | None
    candidate_configuration_hash: str | None


def _context(user: User, now: datetime) -> tuple[datetime, timezone, date]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    if user.timezone not in {"Asia/Shanghai", "UTC"}:
        raise PolicyLifecycleError("UNSUPPORTED_TIMEZONE", "当前仅支持 Asia/Shanghai 或 UTC")
    zone = timezone(timedelta(hours=8)) if user.timezone == "Asia/Shanghai" else UTC
    try:
        now = now.astimezone(UTC)
        return now, zone, now.astimezone(zone).date()
    except (ValueError, OverflowError) as error:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间无法转换为本地日期") from error


def _valid(item: EvidenceItem | None, level: str, now: datetime) -> bool:
    if item is None or item.evidence_level != level or item.status != "VALID":
        return False
    if (
        item.observed_at > now
        or item.valid_from > now
        or (item.valid_to is not None and item.valid_to <= now)
    ):
        return False
    try:
        return configuration_hash(item.content) == item.content_hash
    except (TypeError, ValueError):
        return False


def _issue(code: str, source_ref: str, message: str) -> ReserveSourceIssue:
    return ReserveSourceIssue(code=code, source_ref=source_ref, message=message)


def _bank_role(row: Transaction, evidence: EvidenceItem | None, now: datetime) -> str | None:
    if (
        not _valid(evidence, "BANK_CONFIRMED", now)
        or row.observed_at > now
        or row.occurred_at > now
    ):
        return None
    assert evidence is not None
    if evidence.source_type != "SIMULATED_BANK_TRANSACTION":
        return None
    expected = {
        "transaction_id": str(row.id),
        "account_id": str(row.account_id),
        "direction": row.direction,
        "amount_cents": row.amount_cents,
        "balance_after_cents": row.balance_after_cents,
        "occurred_at": row.occurred_at.isoformat(),
        "counterparty_ref": row.counterparty_ref,
    }
    if configuration_hash(
        {key: evidence.content.get(key) for key in expected}
    ) != configuration_hash(expected):
        return None
    if not all(key in evidence.content for key in expected):
        return None
    role = evidence.content.get("economic_role")
    return (
        role if isinstance(role, str) and role in NON_CONSUMPTION_ROLES | {"CONSUMPTION"} else None
    )


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("Coverage dates must be ISO strings")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Coverage dates must be canonical")
    return parsed


def _coverage(
    user: User,
    now: datetime,
    zone: timezone,
    start: date,
    end: date,
    account_ids: list[UUID],
    transactions: list[Transaction],
    evidence: dict[UUID, EvidenceItem],
    used: set[UUID],
    issues: list[ReserveSourceIssue],
) -> tuple[list[HistoryCoverage], dict[UUID, str]]:
    certificates = [
        item
        for item in evidence.values()
        if item.source_type == COVERAGE_SOURCE_TYPE and item.status != "SUPERSEDED"
    ]
    used.update(item.id for item in certificates)
    if len(certificates) != 1:
        issues.append(
            _issue(
                "MISSING_HISTORY_COVERAGE" if not certificates else "CONFLICTING_HISTORY_COVERAGE",
                "history-coverage",
                "需要唯一、有效的完整交易历史覆盖证明",
            )
        )
        return [], {}
    certificate = certificates[0]
    if not _valid(certificate, "BANK_CONFIRMED", now):
        issues.append(
            _issue(
                "INVALID_HISTORY_COVERAGE", str(certificate.id), "覆盖证明状态、哈希或可知时间无效"
            )
        )
        return [], {}
    try:
        period_start = _date(certificate.content["period_start"])
        period_end = _date(certificate.content["period_end"])
        closed_at = datetime.combine(period_end + timedelta(days=1), time.min, zone).astimezone(UTC)
        if period_start > period_end or period_start > start or period_end < end:
            raise ValueError("Requested complete dates are not covered")
        if certificate.observed_at < closed_at or closed_at > now:
            raise ValueError("The last covered local day has not closed when observed")
        period_rows = [
            row
            for row in transactions
            if period_start <= row.occurred_at.astimezone(zone).date() <= period_end
        ]
        roles: dict[UUID, str] = {}
        for row in period_rows:
            if row.evidence_id is not None:
                used.add(row.evidence_id)
            role = _bank_role(row, evidence.get(row.evidence_id) if row.evidence_id else None, now)
            if role is None:
                issues.append(
                    _issue(
                        "INVALID_BANK_FACT",
                        row.source_ref,
                        "交易与银行证据不符、无可靠经济角色或尚不可知",
                    )
                )
            else:
                roles[row.id] = role
        expected = build_history_coverage(
            user.id, user.timezone, period_start, period_end, account_ids, period_rows, evidence
        )
        if configuration_hash(certificate.content) != configuration_hash(expected):
            raise ValueError("Coverage account scope or complete transaction snapshot changed")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        issues.append(_issue("HISTORY_COVERAGE_MISMATCH", str(certificate.id), str(error)))
        return [], {}
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    return [
        HistoryCoverage(account_id=identifier, covered_dates=days) for identifier in account_ids
    ], roles


def estimate_living_reserve(
    session: Session, user_id: UUID, now: datetime, configuration: dict[str, Any]
) -> ReserveEstimationResponse:
    """No INSERT, UPDATE, DELETE, flush, commit, or authority is performed here."""
    try:
        canonical = validate_configuration(configuration)
        if (
            canonical["type"] != "living_reserve"
            or canonical["method"]["lookback_days"] > MAX_LOOKBACK_DAYS
        ):
            raise ValueError("living_reserve requires a lookback of at most 366 days")
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError(
            "INVALID_CONFIGURATION", "生活准备金配置无效或超出容量范围"
        ) from error
    with session.no_autoflush:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        now, zone, reference_date = _context(user, now)
        try:
            start = reference_date - timedelta(days=canonical["method"]["lookback_days"])
            end = reference_date - timedelta(days=1)
        except (ValueError, OverflowError) as error:
            raise PolicyLifecycleError("INVALID_CLOCK", "历史日期超出支持范围") from error
        account_ids = list(
            session.scalars(
                select(Account.id)
                .where(Account.user_id == user_id)
                .order_by(Account.id)
                .limit(MAX_ACCOUNTS + 1)
            )
        )
        rows = list(
            session.scalars(
                select(Transaction)
                .where(Transaction.user_id == user_id)
                .order_by(Transaction.id)
                .limit(MAX_TRANSACTIONS + 1)
            )
        )
        if len(account_ids) > MAX_ACCOUNTS or len(rows) > MAX_TRANSACTIONS:
            raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "账户或交易数量超出估算容量范围")
        evidence = {
            item.id: item
            for item in session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == user_id))
        }
        used: set[UUID] = set()
        issues: list[ReserveSourceIssue] = []
        coverage, roles = _coverage(
            user, now, zone, start, end, account_ids, rows, evidence, used, issues
        )
        confirmations: dict[str, list[EvidenceItem]] = defaultdict(list)
        for item in evidence.values():
            if (
                item.source_type == CATEGORY_SOURCE_TYPE
                and item.status != "SUPERSEDED"
                and isinstance(item.content, dict)
            ):
                identifier = item.content.get("transaction_id")
                if isinstance(identifier, str):
                    confirmations[identifier].append(item)
        selected: list[ReserveTransaction] = []
        for row in rows:
            day = row.occurred_at.astimezone(zone).date()
            if not start <= day <= end:
                continue
            role = roles.get(row.id)
            if role == "CONSUMPTION" and row.direction == "DEBIT" and row.category_confirmed:
                matching = confirmations[str(row.id)]
                used.update(item.id for item in matching)
                if (
                    len(matching) != 1
                    or not _valid(matching[0], "USER_DECLARED", now)
                    or matching[0].content.get("category") != row.category
                    or matching[0].content.get("confirmed") is not True
                ):
                    issues.append(
                        _issue(
                            "INVALID_CATEGORY_CONFIRMATION",
                            row.source_ref,
                            "当前确认类别缺少唯一、有效且匹配的用户证据",
                        )
                    )
            selected.append(
                ReserveTransaction(
                    transaction_id=row.id,
                    account_id=row.account_id,
                    occurred_on=day,
                    direction="DEBIT" if row.direction == "DEBIT" else "CREDIT",
                    amount_cents=row.amount_cents,
                    category=row.category,
                    category_confirmed=row.category_confirmed,
                    is_one_off=row.is_one_off,
                    economic_role="CONSUMPTION"
                    if role == "CONSUMPTION"
                    else "NON_CONSUMPTION"
                    if role in NON_CONSUMPTION_ROLES
                    else "UNKNOWN",
                )
            )
        if issues:
            coverage = []
        inputs = ReserveEstimateInput(
            reference_date=reference_date,
            timezone=user.timezone,
            account_ids=account_ids,
            transactions=selected,
            coverage=coverage,
        )
        try:
            estimation = calculate_reserve(canonical, inputs)
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_CONFIGURATION", "估算输入或结果超出支持范围"
            ) from error
        issues.sort(key=lambda issue: (issue.code, issue.source_ref))
        digest = configuration_hash(
            {
                "configuration": canonical,
                "inputs": inputs.model_dump(mode="json"),
                "evidence": [
                    {
                        "id": str(identifier),
                        "hash": evidence[identifier].content_hash,
                        "status": evidence[identifier].status,
                        "observed_at": evidence[identifier].observed_at.isoformat(),
                    }
                    for identifier in sorted(used)
                    if identifier in evidence
                ],
                "source_issues": [issue.model_dump(mode="json") for issue in issues],
            }
        )
        candidate = estimation.normalized_configuration if estimation.status == "READY" else None
        return ReserveEstimationResponse(
            simulation=True,
            user_id=user_id,
            as_of=now,
            estimation=estimation,
            source_evidence_ids=sorted(used),
            input_digest=digest,
            source_issues=issues,
            candidate_configuration=candidate,
            candidate_configuration_hash=configuration_hash(candidate)
            if candidate is not None
            else None,
        )
