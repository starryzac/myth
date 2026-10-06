"""Invocation-local real API client, restricted to an already-owned isolated simulator."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Self
from uuid import UUID

from app.db.models import User
from app.db.testing import require_test_database
from app.domain.demo_identity import DEMO_USER_ID, DEMO_USER_REF
from app.domain.full_native_operations import native_request
from app.services.audit_chain import current_audit_epoch
from app.services.local_actor_sessions import (
    LOCAL_USERNAME,
    LocalActorSettings,
    local_actor_configured,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def require_native_target(engine: Engine, user_id: UUID, now: datetime) -> datetime:
    require_test_database(engine.url.database)
    if (
        engine.url.get_backend_name() != "postgresql"
        or engine.url.host != "127.0.0.1"
        or engine.url.port != 54329
        or user_id != DEMO_USER_ID
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("FULL_NATIVE_OWNED_LOCAL_SIMULATOR_REQUIRED")
    return now.astimezone(UTC)


class FullNativeSteps:
    """No reset, source/permission cache, forged actor, custom URL, or dependency bypass."""

    def __init__(self, engine: Engine, user_id: UUID, expected_epoch_id: UUID, now: datetime):
        self.engine = engine
        self.user_id = user_id
        self.expected_epoch_id = expected_epoch_id
        self.now = require_native_target(engine, user_id, now)
        self._client: TestClient | None = None

    def _context(self, now: datetime) -> None:
        checked = require_native_target(self.engine, self.user_id, now)
        if checked < self.now:
            raise ValueError("FULL_NATIVE_CLOCK_MOVED_BACKWARDS")
        with Session(self.engine) as session, session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            actual = session.scalar(text("SELECT current_database()"))
            user = session.get(User, self.user_id)
            epoch = current_audit_epoch(session, self.user_id)
            if (
                actual != self.engine.url.database
                or user is None
                or user.external_ref != DEMO_USER_REF
                or not user.is_simulated
                or epoch is None
                or epoch.status != "OPEN"
                or epoch.id != self.expected_epoch_id
            ):
                raise ValueError("FULL_NATIVE_OWNER_OR_OPEN_EPOCH_NOT_PROVEN")
        self.now = checked

    def __enter__(self) -> Self:
        if self._client is not None:
            raise ValueError("FULL_NATIVE_INVOCATION_ALREADY_OPEN")
        self._context(self.now)
        from app.api.dependencies import get_engine, get_now
        from app.main import create_app

        app = create_app()
        app.dependency_overrides[get_engine] = lambda: self.engine
        app.dependency_overrides[get_now] = lambda: self.now
        self._client = TestClient(app)
        self._client.__enter__()
        return self

    def close(self) -> None:
        self.__exit__(None, None, None)

    def __exit__(self, *args: object) -> None:
        if self._client is not None:
            self._client.__exit__(*args)
            self._client = None

    def dispatch(
        self, kind: str, inputs: dict[str, Any], now: datetime, *, fault: str = "NONE"
    ) -> dict[str, Any]:
        plan = native_request(kind, inputs, fault=fault)
        if self._client is None:
            raise ValueError("FULL_NATIVE_INVOCATION_NOT_OPEN")
        self._context(now)
        body = plan.body
        if plan.server_login:
            settings = LocalActorSettings()
            if not local_actor_configured(settings) or settings.user_secret is None:
                raise PolicyLifecycleError(
                    "LOCAL_ACTOR_NOT_CONFIGURED", "Original local USER login is not configured", 401
                )
            body = {"username": LOCAL_USERNAME, "secret": settings.user_secret.get_secret_value()}
        # No headers, URL, role, principal, receipts, or facts are accepted from a case.
        response = self._client.request(plan.method, plan.path, json=body)
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("FULL_NATIVE_ORIGINAL_RESPONSE_NOT_OBJECT")
        # Actual error responses are retained. Credentials/cookies/headers are never recorded.
        return {
            "status_code": response.status_code,
            "result": value,
            "original_response_text": response.text,
            "response_body_sha256": sha256(response.content).hexdigest(),
        }
