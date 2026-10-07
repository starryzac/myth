"""Staged narrow native MVP asset permission, independently accepted from FULL.

No financial execution is performed here. The original native declaration and
confirmation persist their actual policy/first version in the same transaction
as the exact signed USER wrapper and independent FULL relationship.
"""

import json
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_assets import PREFIX as PREFIX
from app.api.v1.zhiyu_assets import anchored, decode_record, failure, signed_user
from app.api.v1.zhiyu_assets import command_key as command_key
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.models import PolicyVersion
from app.domain.boundary_types import BoundaryModel
from app.domain.full_asset_execution import Hash
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import AssetAuthorization, UUIDReference, configuration_hash
from app.services.audit_chain import audit_read_scope, verify_audit_chain
from app.services.demo_console import _epoch
from app.services.full_asset_execution_dispatch import fresh_read
from app.services.full_policy_lifecycle import read_full_policy
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError, confirm_proposal
from app.services.user_policy_declaration import UserDeclarationRequest, declare_user_policy
from app.services.zhiyu_orchestration import (
    remember_operation,
    remember_rejection,
    replay_operation,
    serial_user,
    store,
)
from fastapi import APIRouter, Depends
from pydantic import StrictBool, model_validator
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PROTOCOL: Literal["zhiyu-asset-native-permission-v1"] = "zhiyu-asset-native-permission-v1"
router = APIRouter(prefix=PREFIX, tags=["独立原MVP资产权限"], dependencies=[Depends(isolated)])


class PermissionCandidateRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    full_policy_id: UUIDReference
    expected_full_policy_version_id: UUIDReference
    configuration: dict[str, Any]
    client_request_id: UUIDReference


class PermissionConfirmationRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    candidate_id: UUIDReference
    reviewed_hash: Hash
    accepted: StrictBool
    client_request_id: UUIDReference

    @model_validator(mode="after")
    def explicit_native_permission(self) -> Self:
        if self.accepted is not True:
            raise ValueError("Native MVP permission requires independent explicit USER acceptance")
        return self


class FullPermissionRelationship(BoundaryModel):
    source_kind: Literal["FULL"] = "FULL"
    policy_id: UUID
    version_id: UUID
    configuration_hash: Hash
    relationship_is_authority: Literal[False] = False


class NativePermissionBinding(BoundaryModel):
    protocol: Literal["zhiyu-asset-native-permission-v1"] = PROTOCOL
    source_kind: Literal["MVP"] = "MVP"
    dsl_version: Literal["MVP_V1"] = "MVP_V1"
    user_id: UUID
    epoch_id: UUID
    candidate_id: UUID
    canonical_configuration: dict[str, Any]
    configuration_hash: Hash
    native_policy_id: UUID
    native_version_id: UUID
    native_proposal_id: UUID
    native_declaration_evidence_id: UUID
    original_declaration_request: UserDeclarationRequest
    original_confirmation_request: PermissionConfirmationRequest
    full_relationship: FullPermissionRelationship
    principal_at_confirmation: LocalActorPrincipal
    confirmed_at: datetime
    bank_authority: Literal[False] = False
    execution_confirmation_created: Literal[False] = False

    @model_validator(mode="after")
    def truly_independent_native_acceptance(self) -> Self:
        require_local_user(self.principal_at_confirmation, self.user_id, self.confirmed_at)
        original = self.original_confirmation_request
        canonical = canonical_permission(self.canonical_configuration)
        if (
            canonical != self.canonical_configuration
            or configuration_hash(canonical) != self.configuration_hash
            or original.reviewed_hash != self.configuration_hash
            or original.candidate_id != self.candidate_id
            or original.expected_epoch_id != self.epoch_id
            or self.original_declaration_request.expected_epoch_id != self.epoch_id
            or self.original_declaration_request.configuration != canonical
            or self.original_declaration_request.idempotency_key
            != command_key(self.epoch_id, "asset-permission", self.candidate_id)
            or self.original_declaration_request.source_proposal_id is not None
        ):
            raise ValueError("Native permission originals, USER and canonical hash must agree")
        return self


def canonical_permission(configuration: dict[str, Any]) -> dict[str, Any]:
    try:
        return AssetAuthorization.model_validate(configuration).model_dump(mode="json")
    except (ValueError, TypeError, RecursionError):
        raise PolicyLifecycleError(
            "ASSET_PERMISSION_CONFIGURATION_INVALID", "这里只接收完整原MVP资产授权字段", 422
        ) from None


def permission_request(body: BoundaryModel, path: str, kind: str) -> dict[str, Any]:
    return {"kind": kind, "path": PREFIX + path, "body": body.model_dump(mode="json")}


def permission_identity(session: Session, user_id: UUID, engine: Engine) -> dict[str, Any]:
    return {**identity(session, user_id, engine), "user_id": str(user_id)}


def decode_permission_binding(raw: dict[str, Any]) -> NativePermissionBinding:
    for key, model in (
        ("full_relationship", FullPermissionRelationship),
        ("principal_at_confirmation", LocalActorPrincipal),
        ("original_declaration_request", UserDeclarationRequest),
        ("original_confirmation_request", PermissionConfirmationRequest),
    ):
        if not isinstance(raw.get(key), dict) or set(raw[key]) != set(model.model_fields):
            raise failure("原权限关系/签署/声明字段缺失，不用默认值修补")
    if (
        raw.get("execution_confirmation_created") is not False
        or raw["full_relationship"].get("relationship_is_authority") is not False
    ):
        raise failure("原规划关联不能代替金融授权或具体执行同意")
    return decode_record(NativePermissionBinding, raw)


def current_full_relationship(
    engine: Engine, user_id: UUID, body: PermissionCandidateRequest, now: datetime
) -> FullPermissionRelationship:
    with fresh_read(engine) as read, audit_read_scope(read):
        epoch = _epoch(read, user_id, body.expected_epoch_id)
        audit = verify_audit_chain(read, user_id, epoch)
        full = read_full_policy(read, user_id, body.full_policy_id, now)
        canonical = canonical_permission(body.configuration)
        if (
            audit.status != "VALID"
            or audit.chain_status != "VALID"
            or audit.reference_status != "VALID"
            or audit.actual_count != audit.expected_count
            or full.epoch_id != epoch
            or full.template_name != "AssetAuthorizationPolicy"
            or full.current_version.version_id != body.expected_full_policy_version_id
            or not full.planning_confirmation_valid
            or full.reference_validation != "CURRENT"
            or full.effective_status not in {"CONFIRMED", "ACTIVE"}
            or full.current_version.configuration.get("scope") != canonical["scope"]
            or full.current_version.configuration.get("goal_id") != canonical["goal_id"]
        ):
            raise failure("需要同轮已核实当前FULL资产规划；关联本身不授金融权限")
        return FullPermissionRelationship(
            policy_id=full.policy_id,
            version_id=full.current_version.version_id,
            configuration_hash=full.current_version.content_hash,
        )


def verified_permission_candidate(
    session: Session, user_id: UUID, candidate_id: UUID
) -> tuple[PermissionCandidateRequest, dict[str, Any]]:
    raw = anchored(session, user_id, "OPERATION", str(candidate_id))
    if raw is None:
        raise failure("原资产权限候选尚未核实，请保留原请求定位", "ASSET_PERMISSION_NOT_FOUND")
    request, result = raw.get("request"), raw.get("result")
    if (
        not isinstance(request, dict)
        or set(request) != {"kind", "path", "body"}
        or not isinstance(request.get("body"), dict)
        or set(request["body"]) != set(PermissionCandidateRequest.model_fields)
        or not isinstance(result, dict)
        or raw.get("operation_status", "COMPLETED") != "COMPLETED"
    ):
        raise failure("原资产权限候选已拒绝或原件形状不完整")
    body = PermissionCandidateRequest.model_validate_json(json.dumps(request["body"]))
    canonical = canonical_permission(body.configuration)
    if (
        request
        != permission_request(body, "/mvp-permissions/candidates", "ASSET_PERMISSION_CANDIDATE")
        or body.client_request_id != candidate_id
        or result.get("candidate_id") != str(candidate_id)
        or result.get("source_kind") != "MVP"
        or result.get("dsl_version") != "MVP_V1"
        or result.get("protocol") != PROTOCOL
        or result.get("canonical_configuration") != canonical
        or result.get("configuration_hash") != configuration_hash(canonical)
        or result.get("can_confirm") is not True
        or result.get("bank_authority") is not False
    ):
        raise failure("原MVP候选canonical、范围或摘要改变")
    return body, result


def verify_original_permission_result(
    session: Session,
    user_id: UUID,
    request_id: UUID,
    raw: dict[str, Any],
) -> dict[str, Any]:
    original = raw.get("request")
    if (
        not isinstance(original, dict)
        or set(original) != {"kind", "path", "body"}
        or not isinstance(original.get("body"), dict)
    ):
        raise failure("原权限请求缺少完整path/body")
    kind = original["kind"]
    if kind == "ASSET_PERMISSION_CANDIDATE":
        body: BoundaryModel = PermissionCandidateRequest.model_validate_json(
            json.dumps(original["body"])
        )
        path = "/mvp-permissions/candidates"
    elif kind == "ASSET_PERMISSION_CONFIRM":
        body = PermissionConfirmationRequest.model_validate_json(json.dumps(original["body"]))
        path = "/mvp-permissions/confirm"
    else:
        raise failure("原请求不属于这条独立MVP资产权限协议")
    if (
        original != permission_request(body, path, kind)
        or original["body"]["client_request_id"] != str(request_id)
        or original["body"]["expected_epoch_id"] != str(_epoch(session, user_id))
        or set(original["body"]) != set(type(body).model_fields)
    ):
        raise failure("原资产权限path/body/owner/epoch/clientUUID不一致")
    status = raw.get("operation_status", "COMPLETED")
    if status == "COMPLETED" and kind == "ASSET_PERMISSION_CANDIDATE":
        verified_permission_candidate(session, user_id, request_id)
    elif status == "COMPLETED":
        result = raw["result"]
        if (
            result.get("client_request_id") != str(request_id)
            or result.get("source_kind") != "MVP"
            or result.get("dsl_version") != "MVP_V1"
            or result.get("protocol") != PROTOCOL
            or result.get("permission_confirmed") is not True
            or result.get("bank_authority") is not False
            or result.get("execution_confirmation_created") is not False
        ):
            raise failure("原资产权限确认结果并非真实独立MVP签署")
        binding = decode_permission_binding(result["original_signed_binding"])
        stored = anchored(session, user_id, "BINDING", f"asset-permission:{binding.candidate_id}")
        version = session.get(PolicyVersion, binding.native_version_id)
        if (
            stored != binding.model_dump(mode="json")
            or binding.user_id != user_id
            or binding.original_confirmation_request.model_dump(mode="json") != original["body"]
            or result["candidate_id"] != str(binding.candidate_id)
            or result["policy_id"] != str(binding.native_policy_id)
            or result["current_version_id"] != str(binding.native_version_id)
            or result["configuration_hash"] != binding.configuration_hash
            or result["canonical_configuration"] != binding.canonical_configuration
            or result["full_relationship"] != binding.full_relationship.model_dump(mode="json")
            or version is None
            or version.user_id != user_id
            or version.policy_id != binding.native_policy_id
            or version.configuration != binding.canonical_configuration
            or version.content_hash != binding.configuration_hash
            or version.confirmation.get("accepted") is not True
            or version.confirmation.get("reviewed_hash") != binding.configuration_hash
            or version.confirmed_at != binding.confirmed_at
        ):
            raise failure("原真实MVP版本、签署或独立FULL关联摘要改变")
    elif status != "REJECTED":
        raise failure("资产权限原操作状态未知")
    return original


@router.get("/mvp-permissions/schema")
def permission_schema(
    session: SessionDependency, engine: EngineDependency, user: DemoUserDependency
) -> dict[str, Any]:
    return {
        **permission_identity(session, user.id, engine),
        "protocol": PROTOCOL,
        "source_kind": "MVP",
        "dsl_version": "MVP_V1",
        "template_name": "AssetAuthorizationPolicy",
        "configuration_schema": AssetAuthorization.model_json_schema(),
        "bank_authority": False,
        "relationship_is_authority": False,
        "summary": "独立原MVP资产权限；与FULL规划另行确认，不提交资产或银行动作。",
    }


@router.post("/mvp-permissions/candidates")
def permission_candidate(
    body: PermissionCandidateRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    original = permission_request(body, "/mvp-permissions/candidates", "ASSET_PERMISSION_CANDIDATE")
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            _epoch(session, user.id, body.expected_epoch_id)
            replay = replay_operation(session, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
            relationship = current_full_relationship(engine, user.id, body, now)
            canonical = canonical_permission(body.configuration)
            result = {
                **permission_identity(session, user.id, engine),
                "protocol": PROTOCOL,
                "client_request_id": str(body.client_request_id),
                "candidate_id": str(body.client_request_id),
                "source_kind": "MVP",
                "dsl_version": "MVP_V1",
                "template_name": "AssetAuthorizationPolicy",
                "canonical_configuration": canonical,
                "configuration_hash": configuration_hash(canonical),
                "full_relationship": relationship.model_dump(mode="json"),
                "can_confirm": True,
                "issues": [],
                "bank_authority": False,
                "permission_confirmed": False,
                "execution_confirmation_created": False,
                "summary": "请审阅资产类别、额度及罚息开关；确认仅建权，仍不执行资金。",
            }
            remember_operation(session, user.id, body.client_request_id, original, result, now)
            return result
    except PolicyLifecycleError as error:
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise


@router.post("/mvp-permissions/confirm")
def permission_confirm(
    body: PermissionConfirmationRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    signed_user(actor, user.id, now)
    original = permission_request(body, "/mvp-permissions/confirm", "ASSET_PERMISSION_CONFIRM")
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            _epoch(session, user.id, body.expected_epoch_id)
            replay = replay_operation(session, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
            candidate_body, candidate = verified_permission_candidate(
                session, user.id, body.candidate_id
            )
            relationship = current_full_relationship(engine, user.id, candidate_body, now)
            if (
                candidate_body.expected_epoch_id != body.expected_epoch_id
                or candidate["configuration_hash"] != body.reviewed_hash
                or candidate["full_relationship"] != relationship.model_dump(mode="json")
                or anchored(session, user.id, "BINDING", f"asset-permission:{body.candidate_id}")
                is not None
            ):
                raise failure(
                    "请审阅同轮原候选；已签原权限不能更换确认请求或FULL关系",
                    "ASSET_PERMISSION_REVIEW_MISMATCH",
                )
            declaration_request = UserDeclarationRequest(
                configuration=candidate["canonical_configuration"],
                expected_epoch_id=body.expected_epoch_id,
                idempotency_key=command_key(
                    body.expected_epoch_id, "asset-permission", body.candidate_id
                ),
                source_proposal_id=None,
            )
            declaration = declare_user_policy(session, user.id, declaration_request, now)
            actual = confirm_proposal(
                session, user.id, declaration.proposal_id, body.reviewed_hash, True, now
            )
            version = session.get(PolicyVersion, actual.current_version_id)
            if (
                version is None
                or version.user_id != user.id
                or version.policy_id != actual.policy_id
                or version.configuration != candidate["canonical_configuration"]
                or version.content_hash != body.reviewed_hash
                or version.confirmation.get("accepted") is not True
                or version.confirmation.get("reviewed_hash") != body.reviewed_hash
                or version.confirmed_at != now
            ):
                raise failure("真实原MVP版本与此次明确用户确认不一致")
            binding = NativePermissionBinding(
                user_id=user.id,
                epoch_id=body.expected_epoch_id,
                candidate_id=body.candidate_id,
                canonical_configuration=candidate["canonical_configuration"],
                configuration_hash=body.reviewed_hash,
                native_policy_id=actual.policy_id,
                native_version_id=actual.current_version_id,
                native_proposal_id=declaration.proposal_id,
                native_declaration_evidence_id=declaration.evidence_id,
                original_declaration_request=declaration_request,
                original_confirmation_request=body,
                full_relationship=relationship,
                principal_at_confirmation=actor,
                confirmed_at=now,
            )
            store(
                session,
                user.id,
                "BINDING",
                f"asset-permission:{body.candidate_id}",
                binding.model_dump(mode="json"),
                now,
            )
            result = {
                **permission_identity(session, user.id, engine),
                "protocol": PROTOCOL,
                "client_request_id": str(body.client_request_id),
                "candidate_id": str(body.candidate_id),
                "source_kind": "MVP",
                "dsl_version": "MVP_V1",
                "template_name": "AssetAuthorizationPolicy",
                "policy_id": str(actual.policy_id),
                "current_version_id": str(actual.current_version_id),
                "configuration_hash": body.reviewed_hash,
                "canonical_configuration": candidate["canonical_configuration"],
                "full_relationship": relationship.model_dump(mode="json"),
                "native_result": actual.model_dump(mode="json"),
                "original_signed_binding": binding.model_dump(mode="json"),
                "permission_confirmed": True,
                "bank_authority": False,
                "execution_confirmation_created": False,
            }
            remember_operation(session, user.id, body.client_request_id, original, result, now)
            return result
    except PolicyLifecycleError as error:
        # Native permission and its wrapper use one transaction. This is never
        # a financial command or a catch for an uncertain commit/response loss.
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise


@router.get("/mvp-permission-commands/{client_request_id}")
def permission_original(
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        raw = anchored(session, user.id, "OPERATION", str(client_request_id))
        if raw is None:
            return {
                **permission_identity(session, user.id, engine),
                "protocol": PROTOCOL,
                "source_kind": "MVP",
                "dsl_version": "MVP_V1",
                "client_request_id": str(client_request_id),
                "status": "NOT_FOUND_NOT_FINAL",
                "not_found_is_final": False,
                "original_request": None,
                "result": None,
                "bank_authority": False,
            }
        original = verify_original_permission_result(session, user.id, client_request_id, raw)
        return {
            **permission_identity(session, user.id, engine),
            "protocol": PROTOCOL,
            "source_kind": "MVP",
            "dsl_version": "MVP_V1",
            "client_request_id": str(client_request_id),
            "status": raw.get("operation_status", "COMPLETED"),
            "not_found_is_final": False,
            "original_request": {"path": original["path"], "body": original["body"]},
            "result": raw["result"],
            "bank_authority": False,
        }
