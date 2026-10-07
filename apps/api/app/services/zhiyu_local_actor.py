"""Per-round local simulation credentials, explicitly separate from model settings."""

import json
import os
import secrets
from pathlib import Path
from typing import Any

from app.domain.policy_configuration import configuration_hash
from app.services.local_actor_sessions import LocalActorSettings, local_actor_configured
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from pydantic import SecretStr
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[4]
PROTOCOL = "zhiyu-next-local-credentials-v1"


def credential_path(engine: Engine) -> Path:
    require_zhiyu_next_engine(engine)
    return (
        ROOT
        / ".runtime"
        / "zhiyu-next"
        / os.environ["ZHIYU_NEXT_ROUND"]
        / "local-actor.private.json"
    )


def load_credentials(engine: Engine, *, create: bool = False) -> LocalActorSettings:
    environment = require_zhiyu_next_engine(engine)
    path = credential_path(engine)
    if not path.parent.is_dir() or path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Registered credential directory is required")
    if not path.exists() and create:
        value: dict[str, Any] = {
            "protocol": PROTOCOL,
            "environment_id": environment,
            "round_id": os.environ["ZHIYU_NEXT_ROUND"],
            "username": "bounded-user",
            "user_secret": secrets.token_urlsafe(32),
            "signing_key": secrets.token_hex(32),
        }
        value["content_hash"] = configuration_hash(value)
        # Exclusive creation: a second API process never rotates a live signing key.
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            path.chmod(0o600)
        except FileExistsError:
            pass
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise ValueError("Invalid local credential file")
    value = json.loads(raw)
    if type(value) is not dict or set(value) != {
        "protocol",
        "environment_id",
        "round_id",
        "username",
        "user_secret",
        "signing_key",
        "content_hash",
    }:
        raise ValueError("Invalid local credential fields")
    if (
        value["protocol"] != PROTOCOL
        or value["environment_id"] != environment
        or value["round_id"] != os.environ["ZHIYU_NEXT_ROUND"]
        or value["username"] != "bounded-user"
        or not isinstance(value["user_secret"], str)
        or not isinstance(value["signing_key"], str)
        or value["content_hash"]
        != configuration_hash({key: item for key, item in value.items() if key != "content_hash"})
    ):
        raise ValueError("Local credentials differ from their registered environment")
    settings = LocalActorSettings(
        **{
            "_env_file": None,
            "user_secret": SecretStr(value["user_secret"]),
            "signing_key": SecretStr(value["signing_key"]),
        }
    )
    if not local_actor_configured(settings):
        raise ValueError("Invalid local credential configuration")
    return settings
