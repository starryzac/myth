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
        full_read = request.method == "GET" and request.url.path.startswith(
            (
                "/api/v1/evidence/",
                "/api/v1/decision-search",
                "/api/v1/seasonal-reserve-adoptions/",
                "/api/v1/planning/",
                "/api/v1/delivery",
                "/api/v1/full-policies",
                "/api/v1/catalog/",
                "/api/v1/autonomy-envelope/",
                "/api/v1/policy-declarations",
                "/api/v1/policy-suggestions/",
                "/api/v1/transactions/",
                "/api/v1/finite-planning/sessions/",
                "/api/v1/reconciliation/",
                "/api/v1/interventions",
                "/api/v1/scenario-simulation/",
                "/api/v1/scenario-risk-review/",
                "/api/v1/goal-release-authorizations/",
                "/api/v1/goal-cash-releases/",
                "/api/v1/full-payment-relations/",
                "/api/v1/full-asset-executions/",
                "/api/v1/dynamic-goal-actions/",
                "/api/v1/boundary/action-set/",
                "/api/v1/boundary/full-action-set/",
                "/api/v1/boundary/actual-action-set/",
                "/api/v1/boundary/periodic-action-producers/",
                "/api/v1/boundary/composed-action-set/",
                "/api/v1/boundary/recovery-composed-action-set/",
                "/api/v1/boundary/registered-action-set/",
                "/api/v1/full-recovery-actions/",
                "/api/v1/full-recovery-next-actions/",
                "/api/v1/full-maturity-actions/",
                "/api/v1/joint-goal-actions/",
                "/api/v1/full-policy-dependencies/",
            )
        )
        dashboard_read = request.url.path == "/api/v1/dashboard" and request.method == "GET"
        policy_preview_read = request.method == "POST" and (
            request.url.path.startswith("/api/v1/policy-financial-previews/")
            or (
                request.url.path.startswith(("/api/v1/policies/", "/api/v1/full-policies/"))
                and request.url.path.endswith(
                    (
                        "/change-preview",
                        "/financial-change-preview",
                        "/financial-change-preview-history",
                    )
                )
            )
        )
        full_goal_read = request.url.path.startswith("/api/v1/goals/") and (
            (
                request.method == "GET"
                and (
                    request.url.path.endswith(("/full-model", "/dynamic-reserve"))
                    or "/full-model/commands/by-key/" in request.url.path
                )
            )
            or (request.method == "POST" and request.url.path.endswith("/full-model/preview"))
        )
        envelope_read = request.method == "POST" and request.url.path == (
            "/api/v1/autonomy-envelope/assess"
        )
        difference_read = request.method == "POST" and request.url.path == (
            "/api/v1/boundary-differences/compare"
        )
        finite_read = request.method == "POST" and request.url.path == (
            "/api/v1/finite-planning/analyze"
        )
        additional_full_preview_read = request.method == "POST" and (
            request.url.path
            in {
                "/api/v1/scenario-simulation/compare",
                "/api/v1/scenario-risk-review/compare",
                "/api/v1/goal-reallocation/preview",
                "/api/v1/planning/full-goal-repairs/preview",
                "/api/v1/planning/full-goal-adjustments/preview",
                "/api/v1/full-maturity-replanning/preview",
                "/api/v1/goal-cash-releases/preview",
                "/api/v1/full-payment-relations/preview",
                "/api/v1/full-asset-executions/preview",
                "/api/v1/full-policy-compilations/preview",
                "/api/v1/dynamic-goal-actions/preview",
                "/api/v1/full-recovery-actions/preview",
                "/api/v1/full-recovery-next-actions/preview",
                "/api/v1/full-maturity-actions/preview",
                "/api/v1/joint-goal-actions/preview",
            }
            or (
                request.url.path.startswith("/api/v1/seasonal-reserve-adoptions/")
                and request.url.path.endswith("/preview")
            )
            or (
                request.url.path.startswith("/api/v1/goal-release-authorizations/policies/")
                and request.url.path.endswith("/preview")
            )
        )
        if (
            request.method == "GET"
            or request.scope.get("zhiyu_read_only") is True
            or (audit_read and request.method == "POST")
            or policy_preview_read
            or full_goal_read
            or envelope_read
            or difference_read
            or finite_read
            or additional_full_preview_read
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
        demo_read = request.method == "GET" and getattr(
            request.scope.get("route"), "path", None
        ) in {
            "/api/v1/demo/presets",
            "/api/v1/demo/state",
            "/api/v1/demo/commands/{command_id}",
        }
        zhiyu_read = request.scope.get("zhiyu_read_only") is True or (
            request.method == "GET"
            and request.url.path
            in {
                "/api/v1/zhiyu/environment",
                "/api/v1/zhiyu/presets",
                "/api/v1/zhiyu/state",
            }
        )
        if (
            audit_read
            or full_read
            or full_goal_read
            or dashboard_read
            or policy_preview_read
            or compilation_read
            or decision_read
            or demo_read
            or zhiyu_read
            or envelope_read
            or difference_read
            or finite_read
            or additional_full_preview_read
        ):
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
