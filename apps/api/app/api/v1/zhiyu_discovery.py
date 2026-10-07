"""Trusted historical discovery; exact originals and explicit USER rule review."""

from datetime import datetime
from typing import Any
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_catalog import (
    AcceptedRequest,
    Hash,
    identity,
    isolated,
    original_request,
)
from app.api.v1.zhiyu_next import ClientRequest, EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.models import PolicyProposal
from app.domain.local_actor_session_types import require_local_user
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.audit_chain import audit_read_scope, verify_audit_chain
from app.services.demo_console import _epoch
from app.services.historical_read import historical_ledger_scope
from app.services.policy_discovery import SOURCE_TYPE, discover_policies
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, confirm_proposal
from app.services.zhiyu_orchestration import (
    remember_operation,
    remember_rejection,
    replay_operation,
    serial_user,
)
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/v1/zhiyu-next/policy-discovery",
    tags=["知余真实历史候选"],
    dependencies=[Depends(isolated)],
)


class DiscoveryRequest(ClientRequest):
    expected_epoch_id: UUID


class DiscoveryConfirmationRequest(AcceptedRequest):
    proposal_id: UUID
    reviewed_hash: Hash


def _originals(session: Session, user: UUID, now: datetime) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(PolicyProposal)
            .where(PolicyProposal.user_id == user, PolicyProposal.source_type == SOURCE_TYPE)
            .order_by(PolicyProposal.created_at, PolicyProposal.id)
            .limit(513)
        )
    )
    if len(rows) > 512:
        raise PolicyLifecycleError(
            "DISCOVERY_CAPACITY_EXCEEDED", "历史候选超过本次完整读取容量", 409
        )
    results = []
    for row in rows:
        reason = None
        observations: list[dict[str, Any]] = []
        configuration = validate_configuration(row.proposed_configuration)
        try:
            originals = _evidence(session, user, row.evidence_ids, now, lock=False)
            observations = [
                item.content for item in originals if item.source_type == SOURCE_TYPE
            ]
            if len(observations) != 1 or observations[0].get("configuration_hash") != (
                configuration_hash(configuration)
            ):
                raise PolicyLifecycleError("INVALID_EVIDENCE", "历史候选与原观察不一致", 409)
        except PolicyLifecycleError as error:
            reason = error.code
        results.append(
            {
                "proposal_id": str(row.id),
                "configuration": configuration,
                "configuration_hash": configuration_hash(configuration),
                "status": row.status,
                "source_status": "VERIFIED_HISTORICAL_OBSERVATION" if reason is None else "UNKNOWN",
                "source_error_code": reason,
                "observations": observations,
                "evidence_ids": row.evidence_ids,
                "created_at": row.created_at.isoformat(),
                "confirmed_policy_id": (
                    str(row.confirmed_policy_id) if row.confirmed_policy_id else None
                ),
                "confirmation_required": True,
                "future_obligation_guaranteed": False,
                "bank_authority": False,
            }
        )
    return results


@router.get("")
def read(
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        audit = verify_audit_chain(session, user.id, epoch_id=_epoch(session, user.id))
        if audit.status != "VALID":
            raise PolicyLifecycleError("AUDIT_CHAIN_INVALID", "需核实当前原审计后读取候选", 409)
        return {
            **identity(session, user.id, engine),
            "protocol": "zhiyu-next-historical-discovery-read-v1",
            "bank_authority": False,
            "proposals": _originals(session, user.id, now),
        }


@router.post("")
def discover(
    body: DiscoveryRequest,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    original = original_request(body, "/api/v1/zhiyu-next/policy-discovery", "POLICY_DISCOVERY")
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            _epoch(session, user.id, body.expected_epoch_id)
            replay = replay_operation(session, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
            result = {
                **identity(session, user.id, engine),
                "client_request_id": str(body.client_request_id),
                "bank_authority": False,
                "discovery": discover_policies(session, user.id, now).model_dump(mode="json"),
                "proposals": _originals(session, user.id, now),
            }
            remember_operation(session, user.id, body.client_request_id, original, result, now)
            return result
    except PolicyLifecycleError as error:
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise


@router.post("/confirm")
def confirm(
    body: DiscoveryConfirmationRequest,
    user: DemoUserDependency,
    engine: EngineDependency,
    principal: PrincipalDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_local_user(principal, user.id, now)
    original = original_request(
        body, "/api/v1/zhiyu-next/policy-discovery/confirm", "POLICY_DISCOVERY_CONFIRM"
    )
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            _epoch(session, user.id, body.expected_epoch_id)
            replay = replay_operation(session, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
            proposal = session.get(PolicyProposal, body.proposal_id)
            if (
                proposal is None
                or proposal.user_id != user.id
                or proposal.source_type != SOURCE_TYPE
            ):
                raise PolicyLifecycleError("NOT_FOUND", "原历史发现候选不存在", 404)
            actual = confirm_proposal(
                session, user.id, body.proposal_id, body.reviewed_hash, body.accepted, now
            )
            result = {
                **identity(session, user.id, engine),
                "client_request_id": str(body.client_request_id),
                "bank_authority": False,
                "original_result": actual.model_dump(mode="json"),
                "authenticated_principal": principal.model_dump(mode="json"),
            }
            remember_operation(session, user.id, body.client_request_id, original, result, now)
            return result
    except PolicyLifecycleError as error:
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise
