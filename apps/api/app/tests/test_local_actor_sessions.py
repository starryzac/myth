"""Synthetic credentials/session clocks. No human or financial-effect evidence."""

import base64
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.services.local_actor_sessions import (
    LocalActorSettings,
    LocalAuthenticationError,
    issue_local_user_session,
    local_actor_configured,
    verify_local_actor_session,
)
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

USER = UUID(int=606)
NOW = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
SECRET = "SYNTHETIC_ONLY_USER_CREDENTIAL_606"
KEY = "61" * 32


class SyntheticSettings(LocalActorSettings):
    model_config = SettingsConfigDict(env_file=None, env_prefix="SYNTHETIC_ONLY_LOCAL_ACTOR_")


def settings() -> LocalActorSettings:
    return SyntheticSettings(user_secret=SecretStr(SECRET), signing_key=SecretStr(KEY))


def issue() -> tuple[str, LocalActorPrincipal]:
    return issue_local_user_session(USER, "bounded-user", SECRET, NOW, settings=settings())


def signed(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = (
        base64.urlsafe_b64encode(hmac.digest(bytes.fromhex(KEY), encoded.encode(), "sha256"))
        .decode()
        .rstrip("=")
    )
    return encoded + "." + signature


def payload() -> dict[str, Any]:
    token, _ = issue()
    raw = token.split(".")[0]
    return cast(dict[str, Any], json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))))


def test_actual_signature_current_session_and_user_gate_do_not_grant_bank_authority() -> None:
    token, principal = issue()
    restored = verify_local_actor_session(
        token, USER, NOW + timedelta(seconds=1), settings=settings()
    )
    assert restored == principal and restored.role == "USER"
    assert restored.human_identity_verified is False
    require_local_user(restored, USER, NOW + timedelta(seconds=899))
    later, another = issue()
    assert later != token and another.session_id != principal.session_id
    assert "secret" not in restored.model_dump() and "signing_key" not in restored.model_dump()


@pytest.mark.parametrize(
    "username,secret", [("agent", SECRET), ("bounded-user", "wrong"), ("", "")]
)
def test_invalid_credentials_cannot_choose_the_user_role(username: str, secret: str) -> None:
    with pytest.raises(LocalAuthenticationError):
        issue_local_user_session(USER, username, secret, NOW, settings=settings())


@pytest.mark.parametrize(
    "change",
    ["signature", "role", "owner", "issuer", "extra", "bool_clock", "long", "empty", "unicode"],
)
def test_untrusted_tokens_and_rehashed_but_unsigned_payloads_are_rejected(change: str) -> None:
    token, _ = issue()
    if change == "signature":
        token = token.split(".")[0] + "." + "A" * 43
    elif change in {"role", "owner", "issuer", "extra", "bool_clock"}:
        data = payload()
        if change == "role":
            data["role"] = "DEMO_ADMIN"
        elif change == "owner":
            data["user_id"] = str(UUID(int=99))
        elif change == "issuer":
            data["issuer"] = "client"
        elif change == "extra":
            data["amount_cents"] = 1
        else:
            data["iat"] = True
        encoded = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
        token = encoded + "." + token.split(".")[1]
    elif change == "long":
        token = "A" * 2049
    elif change == "empty":
        token = ""
    else:
        token = "身份." + "A" * 43
    with pytest.raises(LocalAuthenticationError):
        verify_local_actor_session(token, USER, NOW, settings=settings())


@pytest.mark.parametrize(
    "change",
    ["owner", "extra", "bool_clock", "long_window", "no_window", "unknown_role", "bad_uuid"],
)
def test_even_signed_payloads_must_obey_exact_finite_server_contract(change: str) -> None:
    data = payload()
    if change == "owner":
        data["user_id"] = str(UUID(int=9))
    elif change == "extra":
        data["actor"] = "USER"
    elif change == "bool_clock":
        data["iat"] = True
    elif change == "long_window":
        data["exp"] = data["iat"] + 901
    elif change == "no_window":
        data["exp"] = data["iat"]
    elif change == "unknown_role":
        data["role"] = "ROOT"
    else:
        data["session_id"] = "not-a-uuid"
    with pytest.raises(LocalAuthenticationError):
        verify_local_actor_session(signed(data), USER, NOW, settings=settings())


@pytest.mark.parametrize("seconds,user", [(-1, USER), (900, USER), (1, UUID(int=99))])
def test_expired_future_and_other_owner_sessions_are_not_current(seconds: int, user: UUID) -> None:
    token, _ = issue()
    with pytest.raises(LocalAuthenticationError):
        verify_local_actor_session(
            token, user, NOW + timedelta(seconds=seconds), settings=settings()
        )


@pytest.mark.parametrize("role", ["AGENT", "SYSTEM", "REVIEWER", "DEMO_ADMIN"])
def test_server_signed_other_roles_cannot_originate_new_payee_permission(role: str) -> None:
    data = payload()
    data["role"] = role
    actor = verify_local_actor_session(signed(data), USER, NOW, settings=settings())
    with pytest.raises(ValueError):
        require_local_user(actor, USER, NOW)


def test_configuration_missing_partial_weak_and_key_changes_cannot_authenticate() -> None:
    token, _ = issue()
    for config in (
        SyntheticSettings(),
        SyntheticSettings(user_secret=SecretStr(SECRET)),
        SyntheticSettings(user_secret=SecretStr("short"), signing_key=SecretStr(KEY)),
        SyntheticSettings(user_secret=SecretStr(SECRET), signing_key=SecretStr("0" * 64)),
        SyntheticSettings(user_secret=SecretStr(SECRET), signing_key=SecretStr("G" * 64)),
    ):
        assert local_actor_configured(config) is False
        with pytest.raises(LocalAuthenticationError):
            verify_local_actor_session(token, USER, NOW, settings=config)
    changed = SyntheticSettings(user_secret=SecretStr(SECRET), signing_key=SecretStr("62" * 32))
    with pytest.raises(LocalAuthenticationError):
        verify_local_actor_session(token, USER, NOW, settings=changed)


def test_non_ascii_configured_secret_remains_protected_and_comparable() -> None:
    config = SyntheticSettings(
        user_secret=SecretStr("本地合成凭证" * 8), signing_key=SecretStr(KEY)
    )
    token, _ = issue_local_user_session(
        USER, "bounded-user", "本地合成凭证" * 8, NOW, settings=config
    )
    assert verify_local_actor_session(token, USER, NOW, settings=config).role == "USER"
