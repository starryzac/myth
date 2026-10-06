"""Calendar v2 uses original history verification in a fresh clean RR/RO snapshot."""

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID

from app.db.models import AuditEpoch, CreditCardBill
from app.domain.calendar_periodic_suggestions import (
    PROTOCOL,
    CalendarPeriodicParameters,
    CalendarPeriodicPattern,
    calendar_periodic_patterns,
)
from app.domain.living_reserve import MAX_TRANSACTIONS
from app.domain.pattern_suggestions import (
    HistoryProof,
    PeriodicFact,
    SuggestionModel,
    SuggestionSource,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.evidence_graph import readonly
from app.services.living_reserve import ReserveSourceIssue, _issue
from app.services.policy_discovery import _bill_payload, _evidence_reason, _matches, _source
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.policy_suggestions import (
    _category_proof,
    _digest,
    _evidence_source,
    _History,
    _history,
    _transaction_source,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


class CalendarPeriodicSuggestions(SuggestionModel):
    protocol: Literal["full-calendar-periodic-discovery-v2"] = PROTOCOL
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    parameters: CalendarPeriodicParameters
    history_start: date
    history_end: date
    history_proof: HistoryProof
    patterns: list[CalendarPeriodicPattern]
    source_issues: list[ReserveSourceIssue]
    excluded_transaction_count: int
    source_evidence_ids: list[UUID]
    source_digest: str
    source_scope: Literal["TRANSACTION_COVERAGE_WITH_OBSERVED_BILLS_ONLY"] = (
        "TRANSACTION_COVERAGE_WITH_OBSERVED_BILLS_ONLY"
    )
    writes_performed: Literal[False] = False
    grants_authority: Literal[False] = False
    bank_authority: Literal[False] = False
    hard_protection_changed: Literal[False] = False
    future_obligation_created: Literal[False] = False
    audit_chain_verified: Literal[False] = False
    limitations: list[str]


def _facts(
    session: Session, history: _History, start: date, end: date
) -> tuple[list[PeriodicFact], int]:
    """No new facts: preserve v1 source/category/bill gates, only extend its date window."""
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
                    _issue("INVALID_PAYEE_REFERENCE", row.source_ref, "银行原收款标识缺失或不规范")
                )
            excluded += 1
            continue
        kind: Literal["FIXED_TRANSFER", "RENT", "CREDIT_CARD_BILL"]
        supporting = []
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
            supporting.append(_evidence_source(confirmation))
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
                supporting_sources=supporting,
            )
        )
    bills = list(
        session.scalars(
            select(CreditCardBill)
            .where(CreditCardBill.user_id == history.user.id)
            .order_by(CreditCardBill.id)
            .limit(MAX_TRANSACTIONS + 1)
        )
    )
    if len(bills) > MAX_TRANSACTIONS:
        raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "账单数量超出容量")
    uses = Counter(bill.evidence_id for bill in bills if bill.evidence_id)
    for bill in bills:
        if not start <= bill.due_date <= end:
            continue
        account = history.accounts.get(bill.account_id)
        item = history.evidence.get(bill.evidence_id) if bill.evidence_id else None
        reason = _evidence_reason(item, history.user.id, history.now)
        if item is not None:
            history.used.add(item.id)
        payload = {**_bill_payload(bill), "minimum_due_cents": bill.minimum_due_cents}
        if (
            reason is None
            and item is not None
            and (
                item.source_type != "SIMULATED_CREDIT_CARD_BILL"
                or item.content.get("simulation") is not True
                or item.source_ref != bill.source_ref
                or uses[item.id] != 1
                or not _matches(item.content, payload)
                or item.content.get("user_id") != str(history.user.id)
                or item.content.get("bill_id") != str(bill.id)
                or item.content.get("account_id") != str(bill.account_id)
                or account is None
                or account.account_type != "CREDIT_CARD"
                or bill.statement_date > end
            )
        ):
            reason = "INVALID_BILL_SOURCE"
        if reason is not None:
            history.issues.append(_issue(reason, bill.source_ref, "账单来源不能完整验真"))
            continue
        assert item is not None
        facts.append(
            PeriodicFact(
                kind="CREDIT_CARD_BILL",
                account_id=bill.account_id,
                payee_ref=f"credit-card:{bill.account_id}",
                occurred_on=bill.due_date,
                amount_cents=bill.total_cents,
                source=SuggestionSource.model_validate(_source(bill, item, payload)),
            )
        )
    return facts, excluded


def calendar_periodic_suggestions(
    session: Session, user_id: UUID, now: datetime, parameters: CalendarPeriodicParameters
) -> CalendarPeriodicSuggestions:
    readonly(session)
    now = _now(now)
    with session.no_autoflush:
        epochs = list(
            session.scalars(
                select(AuditEpoch)
                .where(AuditEpoch.user_id == user_id, AuditEpoch.status == "OPEN")
                .order_by(AuditEpoch.id)
                .limit(2)
            )
        )
        if len(epochs) != 1 or epochs[0].user_id != user_id or epochs[0].status != "OPEN":
            raise PolicyLifecycleError("OPEN_EPOCH_REQUIRED", "周期候选需要当前用户唯一开放期", 409)
        epoch = epochs[0]
        if epoch.opened_at.tzinfo is None or epoch.opened_at > now:
            raise PolicyLifecycleError("INVALID_EPOCH_TIME", "当前原开放期晚于服务器查询时点", 409)
        history = _history(session, user_id, now, parameters.lookback_days)
        start = history.reference_date - timedelta(days=parameters.lookback_days)
        end = history.reference_date - timedelta(days=1)
        facts, excluded = _facts(session, history, start, end)
        if history.issues:
            history.proof = history.proof.model_copy(
                update={
                    "verified": False,
                    "reason_codes": sorted({item.code for item in history.issues}),
                }
            )
        patterns = calendar_periodic_patterns(
            facts, parameters, history.proof, history.reference_date
        )
        history.issues.sort(key=lambda issue: (issue.code, issue.source_ref))
        digest = configuration_hash(
            {
                "protocol": PROTOCOL,
                "epoch": row_copy(epoch),
                "history_digest": _digest(
                    history, parameters, [item.model_dump(mode="json") for item in patterns]
                ),
            }
        )
        return CalendarPeriodicSuggestions(
            user_id=user_id,
            epoch_id=epoch.id,
            as_of=history.now,
            parameters=parameters,
            history_start=start,
            history_end=end,
            history_proof=history.proof,
            patterns=patterns,
            source_issues=history.issues,
            excluded_transaction_count=excluded,
            source_evidence_ids=sorted(history.used),
            source_digest=digest,
            limitations=[
                "DISCOVERY_IS_NOT_USER_CONFIRMATION_OR_BANK_AUTHORITY",
                "BILL_SEQUENCE_IS_OBSERVED_ONLY_WITHOUT_EXTERNAL_BILL_COVERAGE",
                "WEEKLY_AND_NONZERO_MONTH_END_OFFSET_HAVE_NO_CURRENT_POLICY_DSL",
                "NO_HOLIDAY_SHIFT_INFERENCE_OR_MERCHANT_NAME_INFERENCE",
                "HISTORY_PROOF_IS_NOT_FULL_AUDIT_OR_INDEPENDENT_ECONOMIC_VERIFICATION",
                "NO_REAL_HUMAN_OR_BANK_STUDY_RESULTS",
            ],
        )
