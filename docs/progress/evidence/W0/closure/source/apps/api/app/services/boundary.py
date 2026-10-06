"""Read-only adapter for verified simulated facts; never creates execution authority."""

from calendar import monthrange
from copy import copy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from app.db.models import (
    Account,
    AssetPosition,
    AssetProduct,
    CreditCardBill,
    EvidenceItem,
    Goal,
    Policy,
    PolicyVersion,
    User,
)
from app.domain.boundary_types import (
    BillFact,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
    CashFact,
    FixedReturnTerms,
    GoalMonthFact,
    GoalOwnership,
    LivingReserveFact,
    SettlementFact,
    SourceIssue,
    UnassignedGoalCash,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.living_reserve import estimate_living_reserve
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, effective_status
from app.services.simulated_bank import validate_recovery_exposure
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

OWNERSHIP_SOURCE = "SIMULATED_GOAL_OWNERSHIP"
CONTRIBUTION_SOURCE = "SIMULATED_GOAL_MONTH_CONTRIBUTION"
SETTLEMENT_SOURCE = "SIMULATED_RECURRING_SETTLEMENT"
AVAILABILITY_SOURCE = "SIMULATED_PRINCIPAL_AVAILABILITY"


class BoundarySourceIssue(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    source_ref: str
    message: str


class BoundaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    boundary: BoundaryResult
    source_evidence_ids: list[UUID]
    input_digest: str
    source_issues: list[BoundarySourceIssue]


class Sources:
    def __init__(self, user_id: UUID, now: datetime, evidence: list[EvidenceItem]) -> None:
        self.user_id = user_id
        self.now = now
        self.balance_as_of: datetime | None = None
        self.evidence = {item.id: item for item in evidence}
        self.used: set[UUID] = set()
        self.issues: list[BoundarySourceIssue] = []

    def issue(self, code: str, entity: UUID | str, message: str) -> None:
        self.issues.append(BoundarySourceIssue(code=code, source_ref=str(entity), message=message))

    def candidates(self, source: str, key: str, value: UUID | str) -> list[EvidenceItem]:
        return [
            item
            for item in self.evidence.values()
            if item.source_type == source
            and item.status != "SUPERSEDED"
            and isinstance(item.content, dict)
            and item.content.get(key) == str(value)
        ]

    def valid(self, item: EvidenceItem, expected: dict[str, Any]) -> bool:
        self.used.add(item.id)
        try:
            return (
                item.user_id == self.user_id
                and item.evidence_level == "BANK_CONFIRMED"
                and item.status == "VALID"
                and item.observed_at <= self.now
                and item.valid_from <= self.now
                and (item.valid_to is None or item.valid_to > self.now)
                and configuration_hash(item.content) == item.content_hash
                and all(key in item.content for key in expected)
                and configuration_hash({key: item.content[key] for key in expected})
                == configuration_hash(expected)
            )
        except (TypeError, ValueError):
            return False

    def proof(
        self, source: str, key: str, identifier: UUID, expected: dict[str, Any]
    ) -> EvidenceItem | None:
        matches = self.candidates(source, key, identifier)
        self.used.update(item.id for item in matches)
        if len(matches) != 1 or not self.valid(matches[0], expected):
            self.issue("INVALID_SOURCE", identifier, f"{source} 来源缺失、冲突或内容不匹配")
            return None
        return matches[0]


def _stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _identity(sources: Sources) -> dict[str, Any]:
    return {"simulation": True, "user_id": str(sources.user_id)}


def _cash(accounts: list[Account], sources: Sources) -> list[CashFact]:
    facts: list[CashFact] = []
    for account in accounts:
        proof = sources.proof(
            "SIMULATED_BANK_BALANCE",
            "account_id",
            account.id,
            {
                **_identity(sources),
                "account_id": str(account.id),
                "account_type": account.account_type,
                "balance_cents": account.balance_cents,
                "currency": account.currency,
                "as_of": _stamp(account.observed_at),
            },
        )
        if proof is None:
            continue
        if account.observed_at > sources.now or proof.observed_at < account.observed_at:
            sources.issue("INVALID_BALANCE_TIME", account.id, "余额事实时间尚不可知")
            continue
        facts.append(
            CashFact.model_validate(
                {
                    "account_id": account.id,
                    "account_type": account.account_type,
                    "balance_cents": account.balance_cents,
                    "observed_at": account.observed_at,
                    "evidence_ids": [proof.id],
                }
            )
        )
    if not accounts:
        sources.issue("MISSING_CASH_ACCOUNTS", "accounts", "没有可验证的账户余额")
    return facts


def _bills(
    bills: list[CreditCardBill], accounts: dict[UUID, Account], sources: Sources, zone: timezone
) -> list[BillFact]:
    facts: list[BillFact] = []
    for bill in bills:
        proof = sources.proof(
            "SIMULATED_CREDIT_CARD_BILL",
            "bill_id",
            bill.id,
            {
                **_identity(sources),
                "bill_id": str(bill.id),
                "account_id": str(bill.account_id),
                "source_ref": bill.source_ref,
                "statement_date": bill.statement_date.isoformat(),
                "due_date": bill.due_date.isoformat(),
                "total_cents": bill.total_cents,
                "minimum_due_cents": bill.minimum_due_cents,
                "paid_cents": bill.paid_cents,
                "status": bill.status,
            },
        )
        if proof is None:
            continue
        account = accounts.get(bill.account_id)
        if (
            proof.id != bill.evidence_id
            or proof.source_ref != bill.source_ref
            or account is None
            or account.account_type != "CREDIT_CARD"
            or bill.statement_date > sources.now.astimezone(zone).date()
        ):
            sources.issue("INVALID_BILL_BINDING", bill.id, "账单身份、账户或出账时间无效")
            continue
        if (
            (bill.status == "PAID") != (bill.paid_cents == bill.total_cents)
            or (bill.status == "UNPAID" and bill.paid_cents != 0)
            or (bill.status == "PARTIALLY_PAID" and not 0 < bill.paid_cents < bill.total_cents)
        ):
            sources.issue("INCONSISTENT_BILL", bill.id, "账单状态与已付金额矛盾")
            continue
        facts.append(
            BillFact.model_validate(
                {
                    "bill_id": bill.id,
                    "account_id": bill.account_id,
                    "statement_date": bill.statement_date,
                    "due_date": bill.due_date,
                    "total_cents": bill.total_cents,
                    "paid_cents": bill.paid_cents,
                    "status": bill.status,
                    "evidence_ids": [proof.id],
                }
            )
        )
    return facts


def _positions(rows: list[AssetPosition], sources: Sources) -> list[BoundaryPosition]:
    facts: list[BoundaryPosition] = []
    for row in rows:
        matches = sources.candidates("SIMULATED_BANK_POSITION", "position_id", row.id)
        observed = matches[0].observed_at if len(matches) == 1 else sources.now
        proof = sources.proof(
            "SIMULATED_BANK_POSITION",
            "position_id",
            row.id,
            {
                **_identity(sources),
                "position_id": str(row.id),
                "account_id": str(row.account_id),
                "product_id": str(row.product_id),
                "goal_id": str(row.goal_id) if row.goal_id else None,
                "policy_version_id": str(row.policy_version_id) if row.policy_version_id else None,
                "principal_cents": row.principal_cents,
                "purchased_at": _stamp(row.purchased_at),
                "maturity_at": _stamp(row.maturity_at),
                "available_at": _stamp(row.available_at),
                "status": row.status,
                "as_of": _stamp(observed),
            },
        )
        if proof is None:
            continue
        if row.purchased_at > sources.now:
            sources.issue("FUTURE_POSITION", row.id, "未来购买不能成为当前本金")
            continue
        available_at = None
        availability_ids: list[UUID] = []
        if (
            row.available_at is not None
            and row.available_at <= sources.now
            and row.status != "REDEEMED"
        ):
            sources.issue(
                "UNRECONCILED_POSITION_AVAILABILITY",
                row.id,
                "声称已过本金到账时间的持仓尚未结清，必须先核对当前余额与归属",
            )
        availability = sources.candidates(AVAILABILITY_SOURCE, "position_id", row.id)
        if availability:
            availability_proof = sources.proof(
                AVAILABILITY_SOURCE,
                "position_id",
                row.id,
                {
                    **_identity(sources),
                    "protocol": "principal-availability-v1",
                    "position_id": str(row.id),
                    "account_id": str(row.account_id),
                    "goal_id": str(row.goal_id) if row.goal_id else None,
                    "principal_cents": row.principal_cents,
                    "available_at": _stamp(row.available_at),
                    "principal_return_bps": 10000,
                    "rollover": False,
                },
            )
            if availability_proof is not None:
                if row.available_at is None or row.status not in {"HELD", "REDEEMING", "MATURED"}:
                    sources.issue("INVALID_AVAILABILITY", row.id, "本金可用证明与当前持仓不一致")
                else:
                    available_at = row.available_at
                    availability_ids = [availability_proof.id]
        facts.append(
            BoundaryPosition.model_validate(
                {
                    "position_id": row.id,
                    "goal_id": row.goal_id,
                    "principal_cents": row.principal_cents,
                    "status": row.status,
                    "principal_available_at": available_at,
                    "evidence_ids": [proof.id],
                    "availability_evidence_ids": availability_ids,
                }
            )
        )
    return facts


def _products(rows: list[AssetProduct], sources: Sources) -> list[BoundaryProduct]:
    facts: list[BoundaryProduct] = []
    for row in rows:
        if row.effective_from > sources.now or (
            row.effective_until is not None and row.effective_until <= sources.now
        ):
            continue
        fixed_return = None
        if row.maturity_rule.get("protocol") == "fixed-principal-return-v1":
            try:
                terms = row.maturity_rule
                if terms.get("day_basis") != "CALENDAR" or terms.get("guaranteed") is not True:
                    raise ValueError("Missing principal guarantee and calendar-day basis")
                if terms.get("auto_rollover", False) is not False:
                    raise ValueError("Automatic rollover contradicts a fixed cash return")
                fixed_return = FixedReturnTerms.model_validate(
                    {
                        key: terms[key]
                        for key in (
                            "term_days",
                            "settlement_delay_days",
                            "principal_return_bps",
                            "rollover",
                        )
                    }
                )
                if row.principal_fluctuation or row.risk_level != 0:
                    raise ValueError("Principal risk conflicts with fixed return")
                if fixed_return.term_days < row.lock_days or (
                    fixed_return.settlement_delay_days < row.redemption_delay_days
                ):
                    raise ValueError("Return terms contradict lock or settlement delay")
            except (KeyError, TypeError, ValueError):
                sources.issue("INVALID_PRODUCT_TERMS", row.id, "本金返还条款不完整或存在风险")
        financial = {
            "product_id": str(row.id),
            "version_number": row.version_number,
            "asset_class": row.asset_class,
            "minimum_purchase_cents": row.minimum_purchase_cents,
            "fixed_return": fixed_return.model_dump(mode="json") if fixed_return else None,
        }
        facts.append(
            BoundaryProduct(
                product_id=row.id,
                version_number=row.version_number,
                asset_class=row.asset_class,
                minimum_purchase_cents=row.minimum_purchase_cents,
                fixed_return=fixed_return,
                terms_digest=configuration_hash(financial),
            )
        )
    return facts


def _generated_before(version: PolicyVersion, stop: datetime, zone: timezone) -> bool:
    """Conservative detection only; no guessed historical obligation amounts are emitted."""
    if version.confirmed_at is None or version.confirmed_at >= stop:
        return False
    try:
        configuration = validate_configuration(version.configuration)
        if configuration["type"] != "recurring_obligation" or (
            configuration["amount_rule"]["kind"] == "bill_balance"
        ):
            return False
        if configuration_hash(configuration) != version.content_hash:
            return True
        start = max(version.confirmed_at, version.valid_from or version.confirmed_at)
        end = min(stop, version.valid_until or stop)
        if end <= start:
            return False
        first = start.astimezone(zone).date()
        last = end.astimezone(zone).date()
        count = (last.year - first.year) * 12 + last.month - first.month + 1
        if count > 120:
            return True
        for offset in range(count):
            month_index = first.month - 1 + offset
            year, month = first.year + month_index // 12, month_index % 12 + 1
            due = date(year, month, min(configuration["due_day"], monthrange(year, month)[1]))
            day_start = datetime.combine(due, time.min, zone)
            if day_start < end and day_start + timedelta(days=1) > start:
                return True
        return False
    except (KeyError, TypeError, ValueError, OverflowError):
        return True


def _policies(
    session: Session,
    sources: Sources,
    zone: timezone,
) -> tuple[list[BoundaryPolicyVersion], list[LivingReserveFact]]:
    versions: list[BoundaryPolicyVersion] = []
    living: list[LivingReserveFact] = []
    policies = session.scalars(select(Policy).where(Policy.user_id == sources.user_id)).all()
    for policy in policies:
        if policy.policy_type == "asset_authorization":
            continue
        history = list(
            session.scalars(
                select(PolicyVersion)
                .where(
                    PolicyVersion.policy_id == policy.id, PolicyVersion.user_id == sources.user_id
                )
                .order_by(PolicyVersion.version_number)
            )
        )
        current = history[-1] if history else None
        ordinary = policy.policy_type == "recurring_obligation" and current is not None
        ambiguous_history = ordinary and any(
            _generated_before(
                previous, min(following.confirmed_at or sources.now, sources.now), zone
            )
            for previous, following in zip(history, history[1:], strict=False)
        )
        stopped = policy.status in {"SUSPENDED", "REVOKED"} or (
            policy.status == "EXPIRED"
            and current is not None
            and (current.valid_until is None or current.valid_until > sources.now)
        )
        if ordinary and stopped and current is not None:
            ambiguous_history = ambiguous_history or _generated_before(current, sources.now, zone)
        if ambiguous_history:
            sources.issue(
                "HISTORICAL_OBLIGATION_RECONCILIATION_REQUIRED",
                policy.id,
                "旧周期可能已生成欠款，缺少可唯一恢复的金额或停止历史",
            )
            continue
        if stopped or policy.status not in {"ACTIVE", "CONFIRMED", "EXPIRED"}:
            continue
        effective = effective_status(policy, current, sources.now)
        if effective == "EXPIRED" and not ordinary:
            continue
        try:
            if (
                current is None
                or effective not in {"ACTIVE", "CONFIRMED", "EXPIRED"}
                or current.confirmed_at is None
            ):
                raise ValueError("No known confirmed current version")
            proofs = _evidence(
                session, sources.user_id, current.evidence_ids, sources.now, lock=False
            )
            sources.used.update(item.id for item in proofs)
            if not any(
                item.evidence_level == "USER_CONFIRMED_POLICY"
                and item.source_type == "POLICY_CONFIRMATION"
                and item.source_ref == str(current.id)
                and item.content == current.confirmation
                and item.content.get("accepted") is True
                for item in proofs
            ):
                raise ValueError("Missing explicit policy confirmation")
            configuration = validate_configuration(current.configuration)
            if configuration["type"] != policy.policy_type:
                raise ValueError("Policy type and current configuration differ")
            versions.append(
                BoundaryPolicyVersion(
                    policy_id=policy.id,
                    version_id=current.id,
                    configuration=configuration,
                    content_hash=current.content_hash,
                    confirmed_at=current.confirmed_at,
                    valid_from=current.valid_from or current.confirmed_at,
                    valid_until=current.valid_until,
                    evidence_ids=sorted(item.id for item in proofs),
                )
            )
            if policy.policy_type == "living_reserve":
                estimate = estimate_living_reserve(
                    session, sources.user_id, sources.now, configuration
                )
                sources.used.update(estimate.source_evidence_ids)
                amount = estimate.estimation.recommended_reserve_cents
                if estimate.estimation.status != "READY" or amount is None:
                    sources.issue(
                        "INSUFFICIENT_LIVING_HISTORY", current.id, "已确认生活策略缺少完整历史"
                    )
                else:
                    living.append(
                        LivingReserveFact(
                            policy_version_id=current.id,
                            amount_cents=amount,
                            estimation_input_digest=configuration_hash(
                                estimate.estimation.model_dump(mode="json")
                            ),
                        )
                    )
        except (PolicyLifecycleError, TypeError, ValueError):
            sources.issue(
                "INVALID_POLICY_SOURCE", policy.id, "当前保护策略缺少有效且现在已知的确认来源"
            )
    return versions, living


def _known_import(item: EvidenceItem, sources: Sources) -> datetime:
    value = item.content.get("as_of")
    if not isinstance(value, str):
        raise ValueError("Import as_of is missing")
    observed = datetime.fromisoformat(value)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("Import time must be aware")
    observed = observed.astimezone(UTC)
    if observed > sources.now or observed > item.observed_at:
        raise ValueError("Import describes unknown future facts")
    if sources.balance_as_of is not None and observed < sources.balance_as_of:
        raise ValueError("Cumulative import does not cover the adopted balance snapshot")
    return observed


def _settlements(
    versions: list[BoundaryPolicyVersion],
    sources: Sources,
) -> list[SettlementFact]:
    facts: list[SettlementFact] = []
    for version in versions:
        if version.configuration["type"] != "recurring_obligation":
            continue
        for proof in sources.candidates(SETTLEMENT_SOURCE, "policy_id", version.policy_id):
            try:
                matches = sources.candidates(SETTLEMENT_SOURCE, "policy_id", version.policy_id)
                if (
                    sum(
                        item.content.get("period") == proof.content.get("period")
                        for item in matches
                    )
                    != 1
                ):
                    raise ValueError("Conflicting statements for the same obligation occurrence")
                as_of = _known_import(proof, sources)
                expected = {
                    **_identity(sources),
                    "protocol": "recurring-settlement-v1",
                    "policy_id": str(version.policy_id),
                    "period": proof.content["period"],
                    "paid_cents": proof.content["paid_cents"],
                    "payee_id": version.configuration["payee_id"],
                    "complete": True,
                    "as_of": _stamp(as_of),
                }
                if not sources.valid(proof, expected):
                    raise ValueError("Invalid settlement proof")
                rule = version.configuration["amount_rule"]
                if rule["kind"] == "bill_balance":
                    raise ValueError("Real bills use their own paid amount instead of settlements")
                maximum = rule["amount_cents"] if rule["kind"] == "exact" else rule["max_cents"]
                final_total = proof.content.get("final_total_cents")
                if final_total is not None and (
                    type(final_total) is not int
                    or final_total < proof.content["paid_cents"]
                    or (rule["kind"] == "range" and not rule["min_cents"] <= final_total <= maximum)
                    or (rule["kind"] == "exact" and final_total != maximum)
                ):
                    raise ValueError("Final occurrence total is outside the confirmed rule")
                if (
                    type(proof.content["paid_cents"]) is not int
                    or proof.content["paid_cents"] > maximum
                ):
                    raise ValueError("Settlement amount exceeds the confirmed obligation")
                facts.append(
                    SettlementFact(
                        policy_id=version.policy_id,
                        period=proof.content["period"],
                        paid_cents=proof.content["paid_cents"],
                        final_total_cents=final_total,
                        settled_at=as_of,
                        evidence_ids=[proof.id],
                    )
                )
            except (KeyError, TypeError, ValueError):
                sources.issue("INVALID_SETTLEMENT_SOURCE", proof.id, "周期结清进口证据无效")
    return facts


def read_goal_month_fact(
    goal: Goal,
    sources: Sources,
    zone: timezone,
    ownership_as_of: datetime,
) -> GoalMonthFact | None:
    """Read one actual complete monthly statement, also for explicit policy assumptions."""
    period = sources.now.astimezone(zone).strftime("%Y-%m")
    month_proofs = [
        item
        for item in sources.candidates(CONTRIBUTION_SOURCE, "goal_id", goal.id)
        if item.content.get("period") == period
    ]
    sources.used.update(item.id for item in month_proofs)
    try:
        if len(month_proofs) != 1:
            raise ValueError("Current month needs one complete contribution statement")
        month = month_proofs[0]
        as_of = _known_import(month, sources)
        if as_of != ownership_as_of:
            raise ValueError("Contribution and ownership must describe the same snapshot")
        if as_of.astimezone(zone).strftime("%Y-%m") != period:
            raise ValueError("Contribution statement predates this month")
        if not sources.valid(
            month,
            {
                **_identity(sources),
                "protocol": "goal-month-contribution-v1",
                "goal_id": str(goal.id),
                "period": period,
                "contributed_cents": month.content["contributed_cents"],
                "complete": True,
                "as_of": _stamp(as_of),
            },
        ):
            raise ValueError("Invalid contribution statement")
        return GoalMonthFact(
            goal_id=goal.id,
            period=period,
            contributed_cents=month.content["contributed_cents"],
            evidence_ids=[month.id],
        )
    except (KeyError, TypeError, ValueError):
        sources.issue("INVALID_GOAL_MONTH_SOURCE", goal.id, "本月累计贡献缺少完整、当前证明")
        return None


def _goals(
    rows: list[Goal],
    accounts: list[CashFact],
    positions: list[BoundaryPosition],
    versions: list[BoundaryPolicyVersion],
    sources: Sources,
    zone: timezone,
) -> tuple[list[GoalOwnership], list[GoalMonthFact], list[UnassignedGoalCash]]:
    ownership: list[GoalOwnership] = []
    contributions: list[GoalMonthFact] = []
    assigned: dict[UUID, int] = {}
    account_map = {item.account_id: item for item in accounts}
    active_ids = {
        item.policy_id for item in versions if item.configuration["type"] == "goal_saving"
    }
    goal_by_policy = {item.policy_id: item for item in rows}
    for identifier in active_ids - goal_by_policy.keys():
        sources.issue("MISSING_GOAL_PROJECTION", identifier, "已确认目标尚无可验证的归属投影")
    for goal in rows:
        owned_positions = [
            item for item in positions if item.goal_id == goal.id and item.status != "REDEEMED"
        ]
        principal = sum(item.principal_cents for item in owned_positions)
        cash = goal.allocated_cents - principal
        matches = sources.candidates(OWNERSHIP_SOURCE, "goal_id", goal.id)
        if len(matches) != 1:
            sources.used.update(item.id for item in matches)
            sources.issue("INVALID_GOAL_OWNERSHIP", goal.id, "目标归属缺少唯一完整证明")
            continue
        proof = matches[0]
        try:
            as_of = _known_import(proof, sources)
            expected = {
                **_identity(sources),
                "protocol": "goal-ownership-v1",
                "goal_id": str(goal.id),
                "policy_id": str(goal.policy_id),
                "account_id": str(goal.account_id) if goal.account_id else None,
                "allocated_cents": goal.allocated_cents,
                "cash_owned_cents": cash,
                "principal_owned_cents": principal,
                "position_ids": sorted(str(item.position_id) for item in owned_positions),
                "as_of": _stamp(as_of),
            }
            if not sources.valid(proof, expected) or cash < 0:
                raise ValueError("Goal allocation does not match its source")
            ownership_as_of = as_of
            if (cash or goal.account_id is not None) and (
                goal.account_id not in account_map
                or account_map[goal.account_id].account_type == "CREDIT_CARD"
            ):
                raise ValueError("Goal cash has no matching cash account")
            if goal.account_id is not None and (
                cash + assigned.get(goal.account_id, 0) > account_map[goal.account_id].balance_cents
            ):
                raise ValueError("Goal cash allocation exceeds its account")
            ownership.append(
                GoalOwnership(
                    goal_id=goal.id,
                    policy_id=goal.policy_id,
                    account_id=goal.account_id,
                    cash_owned_cents=cash,
                    principal_owned_cents=principal,
                    allocated_cents=goal.allocated_cents,
                    evidence_ids=[proof.id],
                )
            )
            if goal.account_id is not None:
                assigned[goal.account_id] = assigned.get(goal.account_id, 0) + cash
        except (KeyError, TypeError, ValueError):
            sources.issue("INVALID_GOAL_OWNERSHIP", goal.id, "目标现金、本金与归属证明不一致")
            continue
        if goal.policy_id not in active_ids:
            continue
        version = next(item for item in versions if item.policy_id == goal.policy_id)
        month_start = sources.now.astimezone(zone).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        if (
            version.valid_from >= next_month
            or (version.valid_until is not None and version.valid_until <= month_start)
            or version.configuration["deadline"] < sources.now.astimezone(zone).date().isoformat()
        ):
            continue
        month = read_goal_month_fact(goal, sources, zone, ownership_as_of)
        if month is not None:
            contributions.append(month)
    unassigned: list[UnassignedGoalCash] = []
    for account in accounts:
        amount = account.balance_cents - assigned.get(account.account_id, 0)
        if amount < 0:
            sources.issue(
                "GOAL_CASH_EXCEEDS_BALANCE", account.account_id, "目标现金归属超过账户余额"
            )
        elif account.account_type == "GOAL":
            unassigned.append(
                UnassignedGoalCash(
                    account_id=account.account_id,
                    amount_cents=amount,
                    evidence_ids=account.evidence_ids,
                )
            )
    return ownership, contributions, unassigned


@dataclass(frozen=True)
class BoundaryContext:
    snapshot: BoundarySnapshot
    versions: list[BoundaryPolicyVersion]
    positions: list[BoundaryPosition]
    products: list[BoundaryProduct]
    sources: Sources


def clone_boundary_context(
    context: BoundaryContext, user_id: UUID, now: datetime
) -> BoundaryContext:
    """Reuse this request's facts without sharing mutable validation bookkeeping."""
    if context.sources.user_id != user_id or context.snapshot.as_of != now:
        raise ValueError("Read context must match its tenant and trusted clock")
    sources = copy(context.sources)
    sources.used = set(context.sources.used)
    sources.issues = list(context.sources.issues)
    sources.evidence = dict(context.sources.evidence)
    return replace(context, sources=sources)


def load_boundary_context(session: Session, user_id: UUID, now: datetime) -> BoundaryContext:
    """Load verified financial facts without writing or authorizing actions."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    try:
        now = now.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间超出支持范围") from error
    with session.no_autoflush:
        user = session.get(User, user_id)
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        if user.timezone not in {"Asia/Shanghai", "UTC"}:
            raise PolicyLifecycleError("UNSUPPORTED_TIMEZONE", "当前仅支持 Asia/Shanghai 或 UTC")
        zone = timezone(timedelta(hours=8)) if user.timezone == "Asia/Shanghai" else UTC
        try:
            _ = now.astimezone(zone).date() + timedelta(days=90)
        except (ValueError, OverflowError) as error:
            raise PolicyLifecycleError("INVALID_CLOCK", "预测日期超出支持范围") from error
        sources = Sources(
            user_id,
            now,
            list(session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == user_id))),
        )
        try:
            validate_recovery_exposure(session, user_id, now)
        except PolicyLifecycleError as error:
            sources.issue(error.code, "independent_bank", error.message)
        accounts = list(session.scalars(select(Account).where(Account.user_id == user_id)))
        bills = list(
            session.scalars(select(CreditCardBill).where(CreditCardBill.user_id == user_id))
        )
        positions = list(
            session.scalars(select(AssetPosition).where(AssetPosition.user_id == user_id))
        )
        goals = list(session.scalars(select(Goal).where(Goal.user_id == user_id)))
        products = list(session.scalars(select(AssetProduct)))
        if len(accounts) > 100 or len(bills) > 10000 or len(positions) > 10000 or len(goals) > 100:
            raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "金融事实数量超出边界容量")
        cash = _cash(accounts, sources)
        sources.balance_as_of = max((item.observed_at for item in cash), default=None)
        bill_facts = _bills(bills, {item.id: item for item in accounts}, sources, zone)
        position_facts = _positions(positions, sources)
        product_facts = _products(products, sources)
        versions, living = _policies(session, sources, zone)
        settlements = _settlements(versions, sources)
        ownership, contributions, unassigned = _goals(
            goals, cash, position_facts, versions, sources, zone
        )
        issues = sorted(sources.issues, key=lambda item: (item.code, item.source_ref))
        input_digest = configuration_hash(
            {
                "sources": [
                    {"id": str(identifier), "hash": sources.evidence[identifier].content_hash}
                    for identifier in sorted(sources.used)
                ]
            }
        )
        snapshot = BoundarySnapshot.model_validate(
            {
                "as_of": now,
                "timezone": user.timezone,
                "cash_accounts": cash,
                "bills": bill_facts,
                "goals": ownership,
                "goal_month_contributions": contributions,
                "occurrence_settlements": settlements,
                "living_reserves": living,
                "unassigned_goal_cash": unassigned,
                "source_issues": [
                    SourceIssue(
                        code=item.code,
                        entity_type="source",
                        entity_id=item.source_ref,
                    )
                    for item in issues
                ],
                "source_digest": input_digest,
            }
        )
        return BoundaryContext(snapshot, versions, position_facts, product_facts, sources)


def compute_user_boundary(session: Session, user_id: UUID, now: datetime) -> BoundaryResponse:
    """Compatibility wrapper around the shared read-only verified context."""
    from app.domain.boundary import compute_boundary

    context = load_boundary_context(session, user_id, now)
    try:
        boundary = compute_boundary(
            context.snapshot, context.versions, context.positions, context.products
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise PolicyLifecycleError(
            "INVALID_BOUNDARY_INPUT", "金融输入不一致或超出支持范围"
        ) from error
    return BoundaryResponse(
        user_id=user_id,
        as_of=context.snapshot.as_of,
        boundary=boundary,
        source_evidence_ids=sorted(context.sources.used),
        input_digest=context.snapshot.source_digest,
        source_issues=sorted(context.sources.issues, key=lambda item: (item.code, item.source_ref)),
    )
