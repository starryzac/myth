"""Single synthetic-user MVP dependencies; authenticated identities arrive in FULL."""

from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import create_database_engine, database_session
from app.db.settings import DatabaseSettings
from app.domain.demo_identity import DEMO_USER_ID, DEMO_USER_REF
from app.services.policy_compilation import CandidateProvider


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_database_engine()


def get_session(
    request: Request, engine: Annotated[Engine, Depends(get_engine)]
) -> Iterator[Session]:
    with database_session(engine) as session:
        audit_read = request.url.path.startswith("/api/v1/audit/")
        dashboard_read = request.url.path == "/api/v1/dashboard" and request.method == "GET"
        policy_preview_read = (
            request.method == "POST"
            and request.url.path.startswith("/api/v1/policies/")
            and request.url.path.endswith("/change-preview")
        )
        if (
            request.method == "GET"
            or (audit_read and request.method == "POST")
            or policy_preview_read
        ):
            # One database snapshot for aggregate facts across multiple SELECTs.
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        compilation_read = request.method == "GET" and request.url.path.startswith(
            "/api/v1/policy-compilations/"
        )
        decision_read = request.method == "GET" and getattr(
            request.scope.get("route"), "path", None
        ) in {
            "/api/v1/decisions",
            "/api/v1/decisions/{run_id}",
            "/api/v1/decisions/{run_id}/explanation",
            "/api/v1/actions/{action_id}/decision",
            "/api/v1/actions/{action_id}/receipt",
        }
        if audit_read or dashboard_read or policy_preview_read or compilation_read or decision_read:
            session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


SessionDependency = Annotated[Session, Depends(get_session, scope="function")]


def get_demo_user(session: SessionDependency) -> User:
    user = session.get(User, DEMO_USER_ID)
    if user is None or user.external_ref != DEMO_USER_REF or not user.is_simulated:
        raise HTTPException(status_code=404)
    return user


DemoUserDependency = Annotated[User, Depends(get_demo_user)]


def get_now() -> datetime:
    """Trusted application clock; clients cannot backdate policy authority."""
    return datetime.now(UTC)


ClockDependency = Annotated[datetime, Depends(get_now)]


@lru_cache(maxsize=1)
def get_compiler_settings() -> DatabaseSettings:
    return DatabaseSettings()


def get_candidate_provider() -> CandidateProvider | None:
    """An explicit server adapter may be injected; no external provider is configured."""
    return None


CompilerSettingsDependency = Annotated[DatabaseSettings, Depends(get_compiler_settings)]
CandidateProviderDependency = Annotated[CandidateProvider | None, Depends(get_candidate_provider)]
