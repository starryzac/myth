"""Credential-authenticated local USER sessions, with no bank permission in the response."""

from typing import Annotated, Literal

from app.api.dependencies import ClockDependency, DemoUserDependency
from app.api.v1.full_policies import require_no_query
from app.domain.boundary_types import BoundaryModel
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.services.local_actor_sessions import (
    COOKIE_NAME,
    LocalAuthenticationError,
    issue_local_user_session,
    verify_local_actor_session,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, StrictStr

router = APIRouter(
    prefix="/api/v1/local-actor",
    tags=["本地模拟身份会话"],
    dependencies=[Depends(require_no_query)],
)


class LocalLoginRequest(BoundaryModel):
    username: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    secret: Annotated[StrictStr, Field(min_length=1, max_length=1024)]


class LocalLogoutRequest(BoundaryModel):
    pass


class LocalSessionResponse(BoundaryModel):
    simulation: Literal[True] = True
    principal: LocalActorPrincipal
    bank_authority: Literal[False] = False
    confirms_financial_action: Literal[False] = False


def get_local_actor_principal(
    request: Request, user: DemoUserDependency, now: ClockDependency
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
        raise HTTPException(401, "需要当前本地身份会话")
    try:
        return verify_local_actor_session(token, user.id, now)
    except (LocalAuthenticationError, ValueError, UnicodeError):
        raise HTTPException(401, "本地身份会话无效或未配置") from None


LocalActorDependency = Annotated[LocalActorPrincipal, Depends(get_local_actor_principal)]


@router.post("/login", response_model=LocalSessionResponse)
def login(
    body: LocalLoginRequest,
    request: Request,
    response: Response,
    user: DemoUserDependency,
    now: ClockDependency,
) -> LocalSessionResponse:
    try:
        token, principal = issue_local_user_session(user.id, body.username, body.secret, now)
    except LocalAuthenticationError:
        raise HTTPException(401, "本地凭证无效或未配置") from None
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
    return LocalSessionResponse(principal=principal)


@router.get("/session", response_model=LocalSessionResponse)
def read_session(principal: LocalActorDependency, response: Response) -> LocalSessionResponse:
    response.headers["Cache-Control"] = "no-store"
    return LocalSessionResponse(principal=principal)


@router.post("/logout")
def logout(body: LocalLogoutRequest, response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/api/v1", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"simulation": True, "logged_out": True, "bank_authority": False}
