"""Read-only FULL periodic/seasonal suggestions from the original verified history pipeline."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from app.db.models import Account, CreditCardBill, EvidenceItem, Transaction, User
from app.domain.history_coverage import COVERAGE_SOURCE_TYPE
from app.domain.living_reserve import MAX_ACCOUNTS, MAX_TRANSACTIONS
from app.domain.pattern_suggestions import (
    PERIODIC_RULE,
    SEASONAL_RULE,
    HistoryProof,
    PeriodicFact,
    PeriodicParameters,
    PeriodicPattern,
    PublicWindow,
    SeasonalParameters,
    SeasonalSpend,
    SeasonalSuggestion,
    SuggestionModel,
    SuggestionSource,
    periodic_patterns,
    public_calendar,
    seasonal_suggestion,
)
from app.domain.policy_configuration import configuration_hash
from app.services.evidence_graph import readonly
from app.services.living_reserve import (
    CATEGORY_SOURCE_TYPE,
    ReserveSourceIssue,
    _context,
    _coverage,
    _issue,
    _valid,
)
from app.services.policy_discovery import (
    _bill_payload,
    _evidence_reason,
    _matches,
    _source,
    _transaction_payload,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import select
from sqlalchemy.orm import Session

MAX_EVIDENCE = 250_000


class PeriodicSuggestions(SuggestionModel):
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    rule_version: str = PERIODIC_RULE
    history_start: date
    history_end: date
    history_proof: HistoryProof
    patterns: list[PeriodicPattern]
    source_issues: list[ReserveSourceIssue]
    excluded_transaction_count: int
    source_evidence_ids: list[UUID]
    source_digest: str
    bank_authority: Literal[False] = False
    hard_protection_changed: Literal[False] = False


class SeasonalSuggestions(SuggestionModel):
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    rule_version: str = SEASONAL_RULE
    public_windows: list[PublicWindow]
    calendar_extraction_hash: str
    calendar_verified_on: Literal["2026-10-05"] = "2026-10-05"
    history_proof: HistoryProof
    suggestion: SeasonalSuggestion
    sources: list[SuggestionSource]
    source_issues: list[ReserveSourceIssue]
    source_evidence_ids: list[UUID]
    source_digest: str
    bank_authority: Literal[False] = False
    hard_protection_changed: Literal[False] = False


@dataclass
class _History:
    user: User
    now: datetime
    zone: timezone
    reference_date: date
    accounts: dict[UUID, Account]
    transactions: list[Transaction]
    evidence: dict[UUID, EvidenceItem]
    roles: dict[UUID, str]
    proof: HistoryProof
    used: set[UUID]
    issues: list[ReserveSourceIssue]
    category_confirmations: dict[str, list[EvidenceItem]]


def _history(session: Session, user_id: UUID, now: datetime, lookback: int | None) -> _History:
    user = session.scalar(select(User).where(User.id == user_id))
    if user is None or user.id != user_id or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    now, zone, today = _context(user, now)
    account_rows = list(
        session.scalars(
            select(Account)
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
    evidence_rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.user_id == user_id)
            .order_by(EvidenceItem.id)
            .limit(MAX_EVIDENCE + 1)
        )
    )
    if (
        len(account_rows) > MAX_ACCOUNTS
        or len(rows) > MAX_TRANSACTIONS
        or len(evidence_rows) > MAX_EVIDENCE
    ):
        raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "历史来源超出候选计算容量")
    accounts = {row.id: row for row in account_rows}
    evidence = {row.id: row for row in evidence_rows}
    used: set[UUID] = set()
    issues: list[ReserveSourceIssue] = []
    end = today - timedelta(days=1)
    start = today - timedelta(days=lookback) if lookback is not None else end
    # The original verifier always replays the certificate's *entire* transaction
    # interval, even when the requested covered date here is just its last day.
    coverage, roles = _coverage(
        user, now, zone, start, end, sorted(accounts), rows, evidence, used, issues
    )
    certificates = [
        item
        for item in evidence.values()
        if item.source_type == COVERAGE_SOURCE_TYPE and item.status != "SUPERSEDED"
    ]
    verified = bool(coverage) and not issues
    period_start = None
    period_end = None
    if verified:
        period_start = date.fromisoformat(certificates[0].content["period_start"])
        period_end = date.fromisoformat(certificates[0].content["period_end"])
    confirmations: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in evidence_rows:
        if item.source_type == CATEGORY_SOURCE_TYPE and item.status != "SUPERSEDED":
            transaction_id = item.content.get("transaction_id")
            if isinstance(transaction_id, str):
                confirmations[transaction_id].append(item)
    return _History(
        user,
        now,
        zone,
        today,
        accounts,
        rows,
        evidence,
        roles,
        HistoryProof(
            verified=verified,
            period_start=period_start,
            period_end=period_end,
            account_ids=sorted(accounts),
            evidence_ids=sorted(used),
            reason_codes=sorted({issue.code for issue in issues}),
        ),
        used,
        issues,
        confirmations,
    )


def _category_proof(history: _History, row: Transaction) -> EvidenceItem | None:
    matching = history.category_confirmations.get(str(row.id), [])
    history.used.update(item.id for item in matching)
    if (
        not row.category_confirmed
        or len(matching) != 1
        or not _valid(matching[0], "USER_DECLARED", history.now)
        or matching[0].user_id != history.user.id
        or matching[0].content.get("simulation") is not True
        or matching[0].content.get("category") != row.category
        or matching[0].content.get("confirmed") is not True
    ):
        return None
    return matching[0]


def _transaction_source(history: _History, row: Transaction) -> SuggestionSource:
    assert row.evidence_id is not None
    item = history.evidence[row.evidence_id]
    history.used.add(item.id)
    return SuggestionSource.model_validate(_source(row, item, _transaction_payload(row)))


def _evidence_source(item: EvidenceItem) -> SuggestionSource:
    return SuggestionSource(
        fact_type="evidence_items",
        fact_id=item.id,
        source_ref=item.source_ref,
        evidence_id=item.id,
        evidence_hash=item.content_hash,
        evidence_source_type=item.source_type,
        evidence_source_ref=item.source_ref,
        evidence_observed_at=item.observed_at.isoformat(),
        fact=item.content,
    )


def _evidence_metadata(item: EvidenceItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "hash": item.content_hash,
        "status": item.status,
        "level": item.evidence_level,
        "source_type": item.source_type,
        "source_ref": item.source_ref,
        "valid_from": item.valid_from.isoformat(),
        "valid_to": item.valid_to.isoformat() if item.valid_to is not None else None,
        "observed_at": item.observed_at.isoformat(),
    }


def _digest(history: _History, parameters: SuggestionModel, result: Any) -> str:
    return configuration_hash(
        {
            "user_id": str(history.user.id),
            "timezone": history.user.timezone,
            "as_of": history.now.isoformat(),
            "parameters": parameters.model_dump(mode="json"),
            "history_proof": history.proof.model_dump(mode="json"),
            "result": result,
            "source_evidence": [
                _evidence_metadata(history.evidence[identifier])
                for identifier in sorted(history.used)
            ],
            "actual_classifications": [
                {
                    "id": str(row.id),
                    "category": row.category,
                    "confirmed": row.category_confirmed,
                    "is_one_off": row.is_one_off,
                }
                for row in history.transactions
            ],
            "issues": [item.model_dump(mode="json") for item in history.issues],
        }
    )


def periodic_suggestions(
    session: Session, user_id: UUID, now: datetime, parameters: PeriodicParameters
) -> PeriodicSuggestions:
    """Fresh original rows in the caller's read-only snapshot; never calls discovery writes."""
    readonly(session)
    with session.no_autoflush:
        history = _history(session, user_id, now, parameters.lookback_days)
        start = history.reference_date - timedelta(days=parameters.lookback_days)
        end = history.reference_date - timedelta(days=1)
        facts = []
        excluded = 0
        for row in history.transactions:
            day = row.occurred_at.astimezone(history.zone).date()
            if not start <= day <= end:
                continue
            account = history.accounts.get(row.account_id)
            role = history.roles.get(row.id)
            if (
                row.direction != "DEBIT"
                or row.is_one_off
                or account is None
                or account.account_type != "CASH"
                or role not in {"INTERNAL_TRANSFER", "CONSUMPTION"}
            ):
                excluded += 1
                continue
            if not row.counterparty_ref or row.counterparty_ref != row.counterparty_ref.strip():
                if role == "INTERNAL_TRANSFER" or row.category == "rent":
                    history.issues.append(
                        _issue(
                            "INVALID_PAYEE_REFERENCE", row.source_ref, "银行原收款标识缺失或不规范"
                        )
                    )
                excluded += 1
                continue
            kind: Literal["FIXED_TRANSFER", "RENT", "CREDIT_CARD_BILL"]
            supporting_sources = []
            if role == "INTERNAL_TRANSFER":
                kind = "FIXED_TRANSFER"
            elif row.category == "rent":
                confirmation = _category_proof(history, row)
                if confirmation is None:
                    history.issues.append(
                        _issue(
                            "INVALID_CATEGORY_CONFIRMATION",
                            row.source_ref,
                            "房租类别缺少唯一且匹配的原用户确认",
                        )
                    )
                    excluded += 1
                    continue
                kind = "RENT"
                supporting_sources.append(_evidence_source(confirmation))
            else:
                excluded += 1
                continue
            facts.append(
                PeriodicFact(
                    kind=kind,
                    account_id=row.account_id,
                    payee_ref=row.counterparty_ref,
                    occurred_on=day,
                    amount_cents=row.amount_cents,
                    source=_transaction_source(history, row),
                    supporting_sources=supporting_sources,
                )
            )
        bills = list(
            session.scalars(
                select(CreditCardBill)
                .where(CreditCardBill.user_id == user_id)
                .order_by(CreditCardBill.id)
                .limit(MAX_TRANSACTIONS + 1)
            )
        )
        if len(bills) > MAX_TRANSACTIONS:
            raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "账单数量超出容量")
        uses = Counter(bill.evidence_id for bill in bills if bill.evidence_id)
        for row_bill in bills:
            if not start <= row_bill.due_date <= end:
                continue
            account = history.accounts.get(row_bill.account_id)
            item = history.evidence.get(row_bill.evidence_id) if row_bill.evidence_id else None
            reason = _evidence_reason(item, user_id, history.now)
            if item is not None:
                history.used.add(item.id)
            payload = {**_bill_payload(row_bill), "minimum_due_cents": row_bill.minimum_due_cents}
            if (
                reason is None
                and item is not None
                and (
                    item.source_type != "SIMULATED_CREDIT_CARD_BILL"
                    or item.content.get("simulation") is not True
                    or item.source_ref != row_bill.source_ref
                    or uses[item.id] != 1
                    or not _matches(item.content, payload)
                    or item.content.get("user_id") != str(user_id)
                    or item.content.get("bill_id") != str(row_bill.id)
                    or item.content.get("account_id") != str(row_bill.account_id)
                    or account is None
                    or account.account_type != "CREDIT_CARD"
                    or row_bill.statement_date > end
                )
            ):
                reason = "INVALID_BILL_SOURCE"
            if reason is not None:
                history.issues.append(_issue(reason, row_bill.source_ref, "账单来源不能完整验真"))
                continue
            assert item is not None
            facts.append(
                PeriodicFact(
                    kind="CREDIT_CARD_BILL",
                    account_id=row_bill.account_id,
                    payee_ref=f"credit-card:{row_bill.account_id}",
                    occurred_on=row_bill.due_date,
                    amount_cents=row_bill.total_cents,
                    source=SuggestionSource.model_validate(_source(row_bill, item, payload)),
                )
            )
        if history.issues:
            history.proof = history.proof.model_copy(
                update={
                    "verified": False,
                    "reason_codes": sorted({item.code for item in history.issues}),
                }
            )
        patterns = periodic_patterns(facts, parameters, history.proof)
        history.issues.sort(key=lambda issue: (issue.code, issue.source_ref))
        return PeriodicSuggestions(
            user_id=user_id,
            as_of=history.now,
            history_start=start,
            history_end=end,
            history_proof=history.proof,
            patterns=patterns,
            source_issues=history.issues,
            excluded_transaction_count=excluded,
            source_evidence_ids=sorted(history.used),
            source_digest=_digest(
                history, parameters, [item.model_dump(mode="json") for item in patterns]
            ),
        )


def seasonal_suggestions(
    session: Session, user_id: UUID, now: datetime, parameters: SeasonalParameters
) -> SeasonalSuggestions:
    readonly(session)
    with session.no_autoflush:
        history = _history(session, user_id, now, None)
        if history.user.timezone != "Asia/Shanghai":
            history.proof = history.proof.model_copy(
                update={
                    "verified": False,
                    "reason_codes": [*history.proof.reason_codes, "CALENDAR_TIMEZONE_UNSUPPORTED"],
                }
            )
            history.issues.append(
                _issue(
                    "CALENDAR_TIMEZONE_UNSUPPORTED",
                    "public-calendar",
                    "中国公共节日候选仅支持 Asia/Shanghai 日期合同",
                )
            )
        windows = public_calendar(history.reference_date)
        target = next((item for item in windows if item.window_id == parameters.window_id), None)
        intervals = []
        if target is not None:
            for item in windows:
                if item.holiday_code == target.holiday_code and item.end < history.reference_date:
                    length = (item.end - item.start).days + 1
                    intervals.append((item.start - timedelta(days=length), item.end))
        invalid_days: dict[date, list[str]] = defaultdict(list)
        spends = []
        sources: dict[tuple[str, UUID], SuggestionSource] = {}
        for row in history.transactions:
            day = row.occurred_at.astimezone(history.zone).date()
            if not any(start <= day <= end for start, end in intervals):
                continue
            if history.roles.get(row.id) != "CONSUMPTION" or row.direction != "DEBIT":
                continue
            proof = _category_proof(history, row)
            if proof is None:
                invalid_days[day].append("INVALID_CATEGORY_CONFIRMATION")
                history.issues.append(
                    _issue(
                        "INVALID_CATEGORY_CONFIRMATION",
                        row.source_ref,
                        "历史消费类别缺少唯一、匹配且当前有效的原用户确认",
                    )
                )
                continue
            row_sources = [_transaction_source(history, row), _evidence_source(proof)]
            for source in row_sources:
                sources[(source.fact_type, source.fact_id)] = source
            if row.category not in parameters.essential_categories:
                continue
            spends.append(
                SeasonalSpend(
                    transaction_id=row.id,
                    occurred_on=day,
                    amount_cents=row.amount_cents,
                    category=row.category,
                    sources=row_sources,
                )
            )
        suggestion = seasonal_suggestion(
            parameters, history.reference_date, history.proof, spends, invalid_days
        )
        history.issues.sort(key=lambda issue: (issue.code, issue.source_ref))
        for identifier in history.proof.evidence_ids:
            evidence_item = history.evidence.get(identifier)
            if evidence_item is not None and evidence_item.source_type == COVERAGE_SOURCE_TYPE:
                sources[("evidence_items", identifier)] = _evidence_source(evidence_item)
        return SeasonalSuggestions(
            user_id=user_id,
            as_of=history.now,
            public_windows=windows,
            calendar_extraction_hash=configuration_hash(
                {"windows": [item.model_dump(mode="json") for item in windows]}
            ),
            history_proof=history.proof,
            suggestion=suggestion,
            sources=[source for _, source in sorted(sources.items())],
            source_issues=history.issues,
            source_evidence_ids=sorted(history.used),
            source_digest=_digest(history, parameters, suggestion.model_dump(mode="json")),
        )
