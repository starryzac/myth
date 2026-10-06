"""Small private simulation steps that call original services and observe original rows."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from app.db.base import Base
from app.db.models import Account, Policy, PolicyVersion, User
from app.db.testing import require_test_database
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

KINDS = {
    "LOOKUP_ACCOUNT",
    "READ_ACTION",
    "READ_POLICY",
    "SNAPSHOT",
    "CHANGE_POLICY",
    "SUSPEND_POLICY",
    "REVOKE_POLICY",
    "CREATE_GOAL",
    "RUN_RECOVERY",
    "READ_RECOVERY",
    "ISSUE_EARLY_QUOTE",
    "DECLARE_POLICY",
    "CONFIRM_POLICY",
}


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LookupAccount(Inputs):
    external_ref: Annotated[StrictStr, Field(min_length=1, max_length=128)]


class ActionIdentity(Inputs):
    action_id: UUID


class PolicyIdentity(Inputs):
    policy_id: UUID


class PolicyState(PolicyIdentity):
    expected_version_id: UUID


class PolicyChange(PolicyState):
    configuration: dict[str, Any]
    reviewed_hash: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted: StrictBool
    reason: Annotated[StrictStr, Field(min_length=1, max_length=1000)]
    idempotency_key: Annotated[StrictStr, Field(min_length=1, max_length=160)]

    @field_validator("accepted")
    @classmethod
    def affirmative(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Only explicit review of this exact changed configuration is accepted")
        return value


class CreateGoal(PolicyState):
    account_id: UUID


class RecoveryKey(Inputs):
    idempotency_key: Annotated[StrictStr, Field(min_length=1, max_length=160)]


class RecoveryIdentity(Inputs):
    decision_run_id: UUID


class PositionIdentity(Inputs):
    position_id: UUID


class PolicyDeclaration(Inputs):
    configuration: dict[str, Any]
    idempotency_key: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
    expected_epoch_id: UUID

    @field_validator("configuration")
    @classmethod
    def original_configuration(cls, value: dict[str, Any]) -> dict[str, Any]:
        from app.domain.policy_configuration import validate_configuration

        return validate_configuration(value)


class ProposalConfirmation(Inputs):
    proposal_id: UUID
    reviewed_hash: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted: StrictBool


def _context(session: Session, user_id: UUID, engine: Engine) -> None:
    actual = session.scalar(text("SELECT current_database()"))
    user = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if (
        actual != engine.url.database
        or user is None
        or not user.is_simulated
        or epoch is None
        or epoch.status != "OPEN"
    ):
        raise PolicyLifecycleError(
            "INVALID_SCENARIO_INPUT", "Actual owned simulation is required", 409
        )


def dispatch_service_step(
    engine: Engine,
    user_id: UUID,
    kind: str,
    data: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    if kind not in KINDS:
        return None
    require_test_database(engine.url.database)
    if (
        engine.url.get_backend_name() != "postgresql"
        or engine.url.host != "127.0.0.1"
        or engine.url.port != 54329
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise PolicyLifecycleError(
            "INVALID_SCENARIO_INPUT", "Local isolated simulation clock required", 409
        )
    now = now.astimezone(UTC)
    with Session(engine) as session, session.begin():
        _context(session, user_id, engine)
    if kind in {"LOOKUP_ACCOUNT", "READ_ACTION", "READ_POLICY", "SNAPSHOT", "READ_RECOVERY"}:
        with Session(engine) as session, session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            _context(session, user_id, engine)
            if kind == "LOOKUP_ACCOUNT":
                lookup = LookupAccount.model_validate(data)
                account = session.scalar(
                    select(Account).where(
                        Account.user_id == user_id,
                        Account.external_ref == lookup.external_ref,
                    )
                )
                if account is None:
                    raise PolicyLifecycleError("NOT_FOUND", "Original owned account not found", 404)
                return {"account": row_copy(account), "observation_only": True}
            if kind == "READ_ACTION":
                from app.services.execution import get_action

                action = ActionIdentity.model_validate(data)
                return get_action(session, user_id, action.action_id, now).model_dump(mode="json")
            if kind == "READ_RECOVERY":
                from app.services.recovery import get_recovery_run

                recovery = RecoveryIdentity.model_validate(data)
                return get_recovery_run(session, user_id, recovery.decision_run_id, now).model_dump(
                    mode="json"
                )
            if kind == "READ_POLICY":
                policy_input = PolicyIdentity.model_validate(data)
                policy = session.scalar(
                    select(Policy).where(
                        Policy.user_id == user_id,
                        Policy.id == policy_input.policy_id,
                    )
                )
                if policy is None:
                    raise PolicyLifecycleError("NOT_FOUND", "Original owned policy not found", 404)
                versions = session.scalars(
                    select(PolicyVersion)
                    .where(
                        PolicyVersion.user_id == user_id,
                        PolicyVersion.policy_id == policy.id,
                    )
                    .order_by(PolicyVersion.version_number)
                ).all()
                return {
                    "policy": row_copy(policy),
                    "versions": [row_copy(v) for v in versions],
                    "observation_only": True,
                }
            Inputs.model_validate(data)
            tables = {
                table.name: [
                    dict(row)
                    for row in session.execute(select(table).order_by(table.c.id)).mappings()
                ]
                for table in Base.metadata.sorted_tables
            }
            return {
                "tables": json.loads(
                    json.dumps(
                        tables,
                        default=lambda value: (
                            value.isoformat() if isinstance(value, datetime) else str(value)
                        ),
                    )
                ),
                "table_count": len(tables),
                "isolation_level": "REPEATABLE READ",
                "read_only": True,
                "scope": (
                    "Original business/audit rows; no reconstructed or expected financial results"
                ),
            }
    if kind == "RUN_RECOVERY":
        from app.services.recovery import run_recovery

        request = RecoveryKey.model_validate(data)
        return run_recovery(engine, user_id, request.idempotency_key, now).model_dump(mode="json")
    if kind == "ISSUE_EARLY_QUOTE":
        from app.services.simulated_redemption_quote import issue_fixed_early_quote

        position = PositionIdentity.model_validate(data)
        return issue_fixed_early_quote(engine, user_id, position.position_id, now).model_dump(
            mode="json"
        )
    if kind == "DECLARE_POLICY":
        from app.services.scenario_policy_declaration import prepare_declared_policy

        declaration = PolicyDeclaration.model_validate(data)
        return prepare_declared_policy(
            engine,
            user_id,
            declaration.configuration,
            declaration.idempotency_key,
            declaration.expected_epoch_id,
            now,
        ).model_dump(mode="json")
    with Session(engine) as session, session.begin():
        _context(session, user_id, engine)
        if kind == "CONFIRM_POLICY":
            from app.services.policy_lifecycle import confirm_proposal

            confirmation = ProposalConfirmation.model_validate(data)
            return confirm_proposal(
                session,
                user_id,
                confirmation.proposal_id,
                confirmation.reviewed_hash,
                confirmation.accepted,
                now,
            ).model_dump(mode="json")
        if kind == "CREATE_GOAL":
            from app.services.goals import create_goal_projection

            goal = CreateGoal.model_validate(data)
            return create_goal_projection(
                session, user_id, goal.policy_id, goal.expected_version_id, goal.account_id, now
            ).model_dump(mode="json")
        if kind == "CHANGE_POLICY":
            from app.services.policy_lifecycle import change_policy

            change = PolicyChange.model_validate(data)
            return change_policy(
                session,
                user_id,
                change.policy_id,
                change.expected_version_id,
                change.configuration,
                change.reviewed_hash,
                change.accepted,
                change.reason,
                change.idempotency_key,
                now,
            ).model_dump(mode="json")
        from app.services.policy_lifecycle import revoke_policy, suspend_policy

        state = PolicyState.model_validate(data)
        stop = suspend_policy if kind == "SUSPEND_POLICY" else revoke_policy
        return stop(session, user_id, state.policy_id, state.expected_version_id, now).model_dump(
            mode="json"
        )
