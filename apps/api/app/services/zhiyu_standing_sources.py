"""Read actual original trigger identities; observations create no bank authority."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.full_models import FullPolicy, FullPolicyVersion
from app.db.models import ActionPlan, ActionReceipt, ExternalBankFact, Policy, PolicyVersion, User
from app.domain.boundary_types import BoundaryModel
from app.domain.external_bank_fact import external_json, validate_external_fact_original
from app.domain.full_maturity_execution import FullMaturityRequest
from app.domain.full_maturity_replanning import OriginalMaturityEvent
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_income_goal_scope import AnyScope
from app.domain.zhiyu_standing_journal import StandingSource
from app.services.demo_console import _epoch
from app.services.full_maturity_execution import lookup_maturity_execution
from app.services.full_maturity_replanning import read_original_maturity
from app.services.full_policy_lifecycle import read_full_policy
from app.services.policy_lifecycle import is_version_authorized
from app.services.zhiyu_orchestration import _audit_healthy
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

MAX_OBSERVATIONS = 128


class ObservedOriginal(BoundaryModel):
    protocol: Literal["zhiyu-next-observed-standing-original-v1"] = (
        "zhiyu-next-observed-standing-original-v1"
    )
    source: StandingSource
    occurred_at: datetime
    original_source: dict[str, Any]
    original_integrity_verified: Literal[True] = True
    native_bank_authority: Literal[False] = False


def income_original(
    scope: AnyScope,
    environment_id: str,
    row: Mapping[str, Any],
    now: datetime,
) -> ObservedOriginal | None:
    """Full native original validation, not a caller supplied amount or status."""
    command = validate_external_fact_original(row)
    if command.user_id != scope.user_id:
        raise ValueError("Actual source owner differs")
    occurred = datetime.fromisoformat(str(row["occurred_at"]))
    observed = datetime.fromisoformat(str(row["observed_at"]))
    if (
        command.kind != "INCOME"
        or command.account_id not in scope.source_account_ids
        or row["bank_status"] != "SETTLED"
        or row["projection_status"] != "PROJECTED"
        or not scope.issued_at <= occurred <= observed <= now < scope.expires_at
    ):
        return None
    original: dict[str, Any] = external_json(dict(row))
    return ObservedOriginal(
        source=StandingSource(
            environment_id=environment_id,
            user_id=scope.user_id,
            epoch_id=scope.epoch_id,
            source_kind="SETTLED_INCOME",
            original_source_id=str(row["id"]),
            original_source_version=str(row["protocol_version"]),
            original_source_hash=configuration_hash(original),
        ),
        occurred_at=occurred,
        original_source=original,
    )


def period_original(
    scope: AnyScope,
    environment_id: str,
    policy_id: UUID,
    current_version_id: UUID,
    current_configuration_hash: str,
    timezone: str,
    now: datetime,
) -> ObservedOriginal | None:
    """Server current period identity excludes polling time and grant identity."""
    if timezone not in {"UTC", "Asia/Shanghai"}:
        raise ValueError("Server-owned supported timezone required")
    if (
        scope.family != "PERIODIC_PAYMENT"
        or current_version_id not in scope.policy_version_ids
        or not scope.issued_at <= now < scope.expires_at
    ):
        return None
    month = now.astimezone(ZoneInfo(timezone))
    original = {
        "protocol": "standing-server-payment-period-v1",
        "user_id": str(scope.user_id),
        "epoch_id": str(scope.epoch_id),
        "policy_id": str(policy_id),
        "current_version_id": str(current_version_id),
        "current_configuration_hash": current_configuration_hash,
        "server_period": month.strftime("%Y-%m"),
        "timezone": timezone,
        "payee_id": scope.payee_id,
    }
    return ObservedOriginal(
        source=StandingSource(
            environment_id=environment_id,
            user_id=scope.user_id,
            epoch_id=scope.epoch_id,
            source_kind="SERVER_PERIOD",
            original_source_id=str(policy_id) + ":" + month.strftime("%Y-%m"),
            original_source_version=str(current_version_id),
            original_source_hash=configuration_hash(original),
        ),
        occurred_at=month.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        original_source=original,
    )


def _maturity(
    session: Session, scope: AnyScope, action: ActionPlan, now: datetime
) -> OriginalMaturityEvent:
    raw = action.request.get("full_maturity_execution")
    if not isinstance(raw, dict):
        raise ValueError("Only a native FULL maturity original is eligible")
    request = FullMaturityRequest.model_validate_json(__import__("json").dumps(raw["request"]))
    if request.expected_epoch_id != scope.epoch_id:
        raise ValueError("Maturity belongs to another epoch")
    original = lookup_maturity_execution(session, scope.user_id, request.idempotency_key, now)
    if (
        original.action is None
        or original.action.action_id != action.id
        or original.action.original_request != request
        or original.action.service_receipt_verified is not True
    ):
        raise ValueError("Original maturity needs its independently verified native receipt")
    event = read_original_maturity(session, scope.user_id, action.id, now)
    if (
        event.proof_status != "VERIFIED"
        or event.service_receipt_verified is not True
        or event.settled_at is None
        or event.original_receipt_id is None
        or event.bank_operation_id is None
        or event.destination_account_id not in scope.source_account_ids
        or not scope.issued_at <= event.settled_at <= now < scope.expires_at
    ):
        raise ValueError("Actual received original principal is required")
    return event


def read_original_sources(
    session: Session, scope: AnyScope, environment_id: str, now: datetime
) -> list[ObservedOriginal]:
    """Caller owns one RR/RO transaction; no hidden writes or preparation here."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Aware current server clock required")
    bind = session.get_bind()
    actual_engine = bind if isinstance(bind, Engine) else bind.engine
    if actual_engine.url.database != environment_id:
        raise ValueError("Current native environment differs")
    _epoch(session, scope.user_id, scope.epoch_id)
    _audit_healthy(session, scope.user_id)
    owner = session.get(User, scope.user_id)
    if owner is None or not owner.is_simulated:
        raise ValueError("Actual simulated owner required")
    result: list[ObservedOriginal] = []
    if scope.family in {"SINGLE_GOAL", "JOINT_GOALS", "ASSET_PORTFOLIO"}:
        rows = list(
            session.scalars(
                select(ExternalBankFact)
                .where(
                    ExternalBankFact.user_id == scope.user_id,
                    ExternalBankFact.kind == "INCOME",
                    ExternalBankFact.account_id.in_(scope.source_account_ids),
                    ExternalBankFact.occurred_at >= scope.issued_at,
                    ExternalBankFact.occurred_at <= now,
                    ExternalBankFact.observed_at <= now,
                )
                .order_by(ExternalBankFact.occurred_at, ExternalBankFact.id)
                .limit(MAX_OBSERVATIONS + 1)
            )
        )
        if len(rows) > MAX_OBSERVATIONS:
            raise ValueError("Complete trigger inventory exceeds bounded observation capacity")
        for row in rows:
            raw = {
                column.key: getattr(row, column.key)
                for column in ExternalBankFact.__table__.columns
            }
            value = income_original(scope, environment_id, raw, now)
            if value is not None:
                result.append(value)
    elif scope.family == "MATURITY_REINVESTMENT":
        maturity_rows = list(
            session.scalars(
                select(ActionPlan)
                .join(ActionReceipt, ActionReceipt.action_plan_id == ActionPlan.id)
                .where(
                    ActionPlan.user_id == scope.user_id,
                    ActionReceipt.user_id == scope.user_id,
                    ActionReceipt.status == "SUCCEEDED",
                    ActionReceipt.occurred_at >= scope.issued_at,
                    ActionReceipt.occurred_at <= now,
                    ActionPlan.action_type == "ASSET_MATURITY",
                    ActionPlan.status.in_(["SUCCEEDED", "RECONCILED"]),
                    ActionPlan.created_at <= now,
                )
                .order_by(ActionPlan.created_at, ActionPlan.id)
                .limit(MAX_OBSERVATIONS + 1)
            )
        )
        if len(maturity_rows) > MAX_OBSERVATIONS:
            raise ValueError("Complete maturity inventory exceeds bounded observation capacity")
        for action in maturity_rows:
            event = _maturity(session, scope, action, now)
            assert event.original_receipt_id is not None and event.settled_at is not None
            result.append(
                ObservedOriginal(
                    source=StandingSource(
                        environment_id=environment_id,
                        user_id=scope.user_id,
                        epoch_id=scope.epoch_id,
                        source_kind="SETTLED_MATURITY_RECEIPT",
                        original_source_id=str(event.original_receipt_id),
                        original_source_version="verified-native-FULL-maturity-v1",
                        original_source_hash=event.originals_hash,
                    ),
                    occurred_at=event.settled_at,
                    original_source=event.originals,
                )
            )
    else:
        for identity in scope.policy_version_ids:
            version = session.get(PolicyVersion, identity)
            if version is None:
                full = session.get(FullPolicyVersion, identity)
                if full is None or full.user_id != scope.user_id:
                    raise ValueError("Current exact policy version original is missing")
                full_policy = session.get(FullPolicy, full.policy_id)
                if (
                    full_policy is None
                    or full_policy.user_id != scope.user_id
                    or full_policy.epoch_id != scope.epoch_id
                ):
                    raise ValueError("Current FULL permission belongs to another scope")
                actual = read_full_policy(session, scope.user_id, full_policy.id, now)
                if (
                    actual.current_version.version_id != identity
                    or not actual.planning_confirmation_valid
                ):
                    raise ValueError("Current FULL planning version changed or is unavailable")
                continue
            policy = session.get(Policy, version.policy_id)
            if (
                version.user_id != scope.user_id
                or policy is None
                or policy.user_id != scope.user_id
                or policy.policy_type != "recurring_obligation"
                or version.content_hash != configuration_hash(version.configuration)
                or not is_version_authorized(session, scope.user_id, identity, now)
                or version.configuration.get("payee_id") != scope.payee_id
            ):
                raise ValueError("Current fixed-payee native permission differs")
            value = period_original(
                scope,
                environment_id,
                policy.id,
                identity,
                version.content_hash,
                owner.timezone,
                now.astimezone(UTC),
            )
            if value is not None:
                result.append(value)
        if len(result) != 1:
            raise ValueError("Exactly one native original payment period required")
    if len({row.source.event_key for row in result}) != len(result):
        raise ValueError("Original source event identity conflicts")
    return sorted(result, key=lambda row: (row.occurred_at, row.source.event_key))
