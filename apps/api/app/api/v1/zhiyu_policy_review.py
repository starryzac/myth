"""Original financial review/commit services behind exact extension routes."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid5

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.local_actor_sessions import LocalLoginRequest, LocalLogoutRequest
from app.api.v1.zhiyu_catalog import identity, isolated, saved_operation
from app.api.v1.zhiyu_next import EngineDependency
from app.db.models import DecisionRun, EvidenceItem, Policy
from app.domain.full_policy_change_multi import SourceKind
from app.domain.full_policy_reviewed_change import ConfirmReviewedChangeRequest, ReviewRequest
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.services.audit_chain import audit_read_scope
from app.services.demo_console import _epoch
from app.services.full_policy_reviewed_change import (
    confirm_reviewed_change,
    lookup_reviewed_change,
    read_review,
    register_review,
)
from app.services.full_policy_reviewed_change import (
    identity as review_identity,
)
from app.services.historical_read import historical_ledger_scope
from app.services.local_actor_sessions import (
    COOKIE_NAME,
    LocalAuthenticationError,
    issue_local_user_session,
    verify_local_actor_session,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_local_actor import load_credentials
from app.services.zhiyu_orchestration import (
    MARKER,
    remember_rejection,
    replay_operation,
    serial_user,
)
from app.services.zhiyu_policy_catalog import (
    CatalogChangePreviewRequest,
    backend_for,
    preview_catalog_change,
)
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/v1/zhiyu-next", tags=["知余规则影响与本地确认"], dependencies=[Depends(isolated)]
)


def principal(
    request: Request, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> LocalActorPrincipal:
    cookie = request.cookies.get(COOKIE_NAME)
    authorization = request.headers.get("authorization")
    bearer = None
    if authorization is not None:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "本地身份会话无效")
        bearer = authorization[7:]
    if cookie is not None and bearer is not None and cookie != bearer:
        raise HTTPException(401, "本地身份会话冲突")
    token = bearer or cookie
    if token is None:
        raise HTTPException(401, "请先建立当前本地确认会话")
    try:
        return verify_local_actor_session(token, user.id, now, settings=load_credentials(engine))
    except (LocalAuthenticationError, ValueError, OSError, UnicodeError):
        raise HTTPException(401, "本地身份会话无效或未配置") from None


PrincipalDependency = Annotated[LocalActorPrincipal, Depends(principal)]


def rejection_id(epoch: UUID, phase: str, key: str) -> UUID:
    return uuid5(epoch, f"zhiyu-next-policy-change:{phase}:{key}")


def reject_if_uncommitted(
    engine: Engine,
    user_id: UUID,
    phase: str,
    body: ReviewRequest,
    original: dict[str, Any],
    error: PolicyLifecycleError,
    now: datetime,
) -> None:
    # A post-commit read failure cannot turn a real committed version into REJECTED.
    with Session(engine) as read:
        if read.get(DecisionRun, review_identity(user_id, phase, body.idempotency_key)) is not None:
            return
        epoch = _epoch(read, user_id)
    remember_rejection(
        engine, user_id, rejection_id(epoch, phase, body.idempotency_key), original, error, now
    )


def rejection(session: Session, user_id: UUID, phase: str, key: str) -> dict[str, Any] | None:
    epoch = _epoch(session, user_id)
    request_id = rejection_id(epoch, phase, key)
    if session.get(EvidenceItem, uuid5(epoch, f"{MARKER}:OPERATION:{request_id}")) is None:
        return None
    actual, original = saved_operation(session, user_id, request_id)
    if actual["status"] != "REJECTED" or original["kind"] != "POLICY_CHANGE_" + phase:
        raise PolicyLifecycleError("REQUEST_SCOPE_MISMATCH", "原变更拒绝记录范围不匹配", 409)
    return {
        "status": "REJECTED",
        "idempotency_key": key,
        "error": actual["result"],
        "original_request": {"path": original["path"], "body": original["body"]},
    }


@router.post("/local-actor/login")
def login(
    body: LocalLoginRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    try:
        token, actor = issue_local_user_session(
            user.id, body.username, body.secret, now, settings=load_credentials(engine)
        )
    except (LocalAuthenticationError, ValueError, OSError):
        raise HTTPException(401, "本地确认凭证无效或未配置") from None
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=900,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/v1",
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        **identity(session, user.id, engine),
        "principal": actor.model_dump(mode="json"),
        "bank_authority": False,
        "confirms_financial_action": False,
    }


@router.get("/local-actor/session")
def actor_session(
    response: Response,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {
        **identity(session, user.id, engine),
        "principal": actor.model_dump(mode="json"),
        "bank_authority": False,
        "confirms_financial_action": False,
    }


@router.post("/local-actor/logout")
def logout(
    body: LocalLogoutRequest,
    response: Response,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
) -> dict[str, Any]:
    response.delete_cookie(COOKIE_NAME, path="/api/v1", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {**identity(session, user.id, engine), "logged_out": True, "bank_authority": False}


@router.post("/policies/{source_kind}/{policy_id}/change-preview")
def preview(
    source_kind: SourceKind,
    policy_id: UUID,
    body: CatalogChangePreviewRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    expected = (
        "FULL_POLICY"
        if backend_for(body.template_name, body.dsl_version) == "FULL"
        else "MVP_POLICY"
    )
    if source_kind != expected:
        raise PolicyLifecycleError("REQUEST_SCOPE_MISMATCH", "预览策略范围不匹配", 409)
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = preview_catalog_change(session, user.id, policy_id, body, now)
        return {**identity(session, user.id, engine), **actual.model_dump(mode="json")}


def exclude_goal_bridge(engine: Engine, user_id: UUID, kind: SourceKind, policy_id: UUID) -> None:
    if kind == "MVP_POLICY":
        with Session(engine) as read:
            policy = read.get(Policy, policy_id)
            if (
                policy is not None
                and policy.user_id == user_id
                and policy.policy_type == "goal_saving"
            ):
                raise PolicyLifecycleError(
                    "GOAL_MODEL_CHANGE_NOT_OPEN",
                    "完整目标修改须同时重新绑定目标模型；当前仅提供影响预览",
                    409,
                )


@router.post("/policies/{source_kind}/{policy_id}/change-review")
def review(
    source_kind: SourceKind,
    policy_id: UUID,
    body: ReviewRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    original = {
        "kind": "POLICY_CHANGE_REVIEW",
        "path": f"/api/v1/zhiyu-next/policies/{source_kind}/{policy_id}/change-review",
        "body": body.model_dump(mode="json"),
    }
    with serial_user(engine, user.id):
        try:
            epoch = _epoch(session, user.id, body.expected_epoch_id)
            replay_operation(
                session, user.id, rejection_id(epoch, "REVIEW", body.idempotency_key), original
            )
            exclude_goal_bridge(engine, user.id, source_kind, policy_id)
            actual = register_review(engine, user.id, source_kind, policy_id, body, now, actor)
            return {**identity(session, user.id, engine), "review": actual.model_dump(mode="json")}
        except PolicyLifecycleError as error:
            reject_if_uncommitted(engine, user.id, "REVIEW", body, original, error, now)
            raise


@router.post("/policies/{source_kind}/{policy_id}/change-confirm")
def confirm(
    source_kind: SourceKind,
    policy_id: UUID,
    body: ConfirmReviewedChangeRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    original = {
        "kind": "POLICY_CHANGE_CONFIRM",
        "path": f"/api/v1/zhiyu-next/policies/{source_kind}/{policy_id}/change-confirm",
        "body": body.model_dump(mode="json"),
    }
    with serial_user(engine, user.id):
        try:
            epoch = _epoch(session, user.id, body.expected_epoch_id)
            replay_operation(
                session, user.id, rejection_id(epoch, "CONFIRM", body.idempotency_key), original
            )
            exclude_goal_bridge(engine, user.id, source_kind, policy_id)
            actual = confirm_reviewed_change(
                engine, user.id, source_kind, policy_id, body, now, actor
            )
            # COMMITTED_BUT_FINANCIAL_UNKNOWN is a known commit, not a bank UNKNOWN.
            return {**identity(session, user.id, engine), "commit": actual.model_dump(mode="json")}
        except PolicyLifecycleError as error:
            reject_if_uncommitted(engine, user.id, "CONFIRM", body, original, error, now)
            raise


@router.get("/policy-change-reviews/by-key/{key}")
def review_by_key(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    key: Annotated[str, Path(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")],
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        failed = rejection(session, user.id, "REVIEW", key)
        if failed is not None:
            return {**identity(session, user.id, engine), **failed}
        review_id = review_identity(user.id, "REVIEW", key)
        if session.get(DecisionRun, review_id) is None:
            return {
                **identity(session, user.id, engine),
                "status": "NOT_FOUND_NOT_FINAL",
                "idempotency_key": key,
            }
        actual = read_review(engine, user.id, review_id, now)
        return {
            **identity(session, user.id, engine),
            "status": "RECORDED",
            "idempotency_key": key,
            "review": actual.model_dump(mode="json"),
            "original_request": {
                "path": (
                    f"/api/v1/zhiyu-next/policies/{actual.source_kind}"
                    f"/{actual.policy_id}/change-review"
                ),
                "body": actual.request.model_dump(mode="json"),
            },
        }


@router.get("/policy-reviews/{review_id}")
def review_original(
    review_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    actual = read_review(engine, user.id, review_id, now)
    return {**identity(session, user.id, engine), "review": actual.model_dump(mode="json")}


@router.get("/policy-change-commands/{key}")
def change_original(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    key: Annotated[str, Path(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")],
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        failed = rejection(session, user.id, "CONFIRM", key)
        if failed is not None:
            return {**identity(session, user.id, engine), **failed}
        actual = lookup_reviewed_change(engine, user.id, key, now)
        return {**identity(session, user.id, engine), **actual.model_dump(mode="json")}
