"""Finite local signed sessions. No client role or financial data establishes authority."""

import base64
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.db.settings import REPOSITORY_ROOT
from app.domain.local_actor_session_types import ActorRole, LocalActorPrincipal
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ISSUER = "bounded-funds-local-actor-v1"
LOCAL_USERNAME = "bounded-user"
COOKIE_NAME = "bounded_funds_local_actor"


class LocalActorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BF_LOCAL_",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    user_secret: SecretStr | None = None
    signing_key: SecretStr | None = None


class LocalAuthenticationError(ValueError):
    pass


def _clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise LocalAuthenticationError("An aware trusted server clock is required")
    return now.astimezone(UTC)


def _configuration(settings: LocalActorSettings) -> tuple[str, bytes]:
    if settings.user_secret is None or settings.signing_key is None:
        raise LocalAuthenticationError("Local actor credentials are not configured")
    secret = settings.user_secret.get_secret_value()
    key = settings.signing_key.get_secret_value()
    if len(secret.encode("utf-8")) < 24 or len(key) != 64:
        raise LocalAuthenticationError("Local actor credentials are not configured")
    try:
        binary = bytes.fromhex(key)
    except ValueError as error:
        raise LocalAuthenticationError("Local actor credentials are not configured") from error
    if (
        len(binary) != 32
        or binary == b"\0" * 32
        or hmac.compare_digest(secret.encode("utf-8"), key.encode("utf-8"))
    ):
        raise LocalAuthenticationError("Local actor credentials are not configured")
    return secret, binary


def local_actor_configured(settings: LocalActorSettings | None = None) -> bool:
    try:
        _configuration(settings or LocalActorSettings())
    except (LocalAuthenticationError, ValueError):
        return False
    return True


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for char in value
    ):
        raise LocalAuthenticationError("Invalid local session")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise LocalAuthenticationError("Invalid local session") from error


def issue_local_user_session(
    user_id: UUID,
    username: str,
    provided_secret: str,
    now: datetime,
    *,
    settings: LocalActorSettings | None = None,
) -> tuple[str, LocalActorPrincipal]:
    secret, key = _configuration(settings or LocalActorSettings())
    # Both comparisons execute. User role comes only from this server mapping.
    name_matches = hmac.compare_digest(username.encode("utf-8"), LOCAL_USERNAME.encode("utf-8"))
    secret_matches = hmac.compare_digest(provided_secret.encode("utf-8"), secret.encode("utf-8"))
    if not name_matches or not secret_matches:
        raise LocalAuthenticationError("Invalid local credentials")
    issued = _clock(now).replace(microsecond=0)
    principal = LocalActorPrincipal(
        user_id=user_id,
        role="USER",
        session_id=uuid4(),
        issued_at=issued,
        expires_at=issued + timedelta(seconds=900),
    )
    body = {
        "issuer": ISSUER,
        "user_id": str(user_id),
        "session_id": str(principal.session_id),
        "role": principal.role,
        "iat": int(issued.timestamp()),
        "exp": int(principal.expires_at.timestamp()),
    }
    encoded = _encode(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _encode(hmac.digest(key, encoded.encode("ascii"), "sha256"))
    return encoded + "." + signature, principal


def verify_local_actor_session(
    token: str,
    expected_user_id: UUID,
    now: datetime,
    *,
    settings: LocalActorSettings | None = None,
) -> LocalActorPrincipal:
    _, key = _configuration(settings or LocalActorSettings())
    if not isinstance(token, str) or len(token) > 2048 or token.count(".") != 1:
        raise LocalAuthenticationError("Invalid local session")
    encoded, supplied = token.split(".")
    decoded = _decode(encoded)
    if not hmac.compare_digest(
        _decode(supplied), hmac.digest(key, encoded.encode("ascii"), "sha256")
    ):
        raise LocalAuthenticationError("Invalid local session")
    try:
        payload: Any = json.loads(decoded)
        if (
            type(payload) is not dict
            or set(payload) != {"issuer", "user_id", "session_id", "role", "iat", "exp"}
            or payload["issuer"] != ISSUER
            or type(payload["iat"]) is not int
            or type(payload["exp"]) is not int
            or type(payload["user_id"]) is not str
            or type(payload["session_id"]) is not str
            or payload["role"] not in {"USER", "AGENT", "REVIEWER", "SYSTEM", "DEMO_ADMIN"}
        ):
            raise ValueError("Invalid local session")
        role: ActorRole = payload["role"]
        principal = LocalActorPrincipal(
            user_id=UUID(payload["user_id"]),
            session_id=UUID(payload["session_id"]),
            role=role,
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
        if (
            principal.user_id != expected_user_id
            or not principal.issued_at <= _clock(now) < principal.expires_at
        ):
            raise ValueError("Invalid local session")
    except (ValueError, TypeError, KeyError, OverflowError, OSError) as error:
        raise LocalAuthenticationError("Invalid local session") from error
    return principal
