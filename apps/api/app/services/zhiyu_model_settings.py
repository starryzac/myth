"""Private extension-only model configuration; never a financial authorization."""

import ipaddress
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any, Self
from urllib.parse import urlsplit

from app.db.settings import REPOSITORY_ROOT
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

MODEL_ENV_PATH = REPOSITORY_ROOT / ".runtime" / "zhiyu-next" / "model.env"
_CONFIGURATION_LOCK = Lock()


class ModelConfigurationError(ValueError):
    """Only static messages may cross the API boundary."""

    def __init__(self, code: str = "LLM_CONFIGURATION_INVALID") -> None:
        self.code = code
        self.message = (
            "模型私有配置保存失败，请检查扩展版目录权限。"
            if code == "LLM_CONFIGURATION_WRITE_FAILED"
            else "模型私有配置无效，请检查地址、模型和有限调用参数。"
        )
        super().__init__(self.message)


def _base_url(value: str) -> str:
    if not value:
        return ""
    if len(value) > 2048 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("A plain model base URL is required")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("A plain model base URL is required") from None
    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise ValueError("A plain model base URL is required")
    if parsed.scheme == "http":
        loopback = hostname.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            pass
        if not loopback:
            raise ValueError("Remote model services require HTTPS")
    return value.rstrip("/")


class ModelPublicStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool
    configured: bool
    base_url: str
    model: str
    api_key_configured: bool
    api_key_mask: str | None
    timeout_seconds: float
    max_calls_per_turn: int
    configuration_source: str = "EXTENSION_PRIVATE_LOCAL"
    grants_authority: bool = False


class _LiteralModelEnvSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], env_files: Any) -> None:
        super().__init__(settings_cls)
        self.env_files = env_files

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self().get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        files = self.env_files
        if files is None:
            return {}
        if isinstance(files, (str, os.PathLike)):
            files = [files]
        values: dict[str, Any] = {}
        for filename in files:
            path = Path(filename)
            if not path.is_file():
                continue
            for name, value in dotenv_values(path, encoding="utf-8", interpolate=False).items():
                field_name = name.lower().removeprefix("llm_")
                if name.lower().startswith("llm_") and field_name in self.settings_cls.model_fields:
                    values[field_name] = value
        return values


class ZhiyuModelSettings(BaseSettings):
    """The old Demo .env is deliberately never read by this settings class."""

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=MODEL_ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=15.0, ge=0.1, le=120.0, allow_inf_nan=False)
    max_calls_per_turn: int = Field(default=3, ge=1, le=8)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # An explicit private UI save must take effect even with startup env values.
        # python-dotenv otherwise expands ${...} even inside single quotes, which
        # would corrupt literal API keys or substitute unrelated environment data.
        private_source = _LiteralModelEnvSource(
            settings_cls, getattr(dotenv_settings, "env_file", None)
        )
        return init_settings, private_source, env_settings, file_secret_settings

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _base_url(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if len(value) > 160 or any(ord(char) < 32 for char in value):
            raise ValueError("A finite model identifier is required")
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        secret = value.get_secret_value()
        if len(secret) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in secret):
            raise ValueError("A finite private API key is required")
        return value

    def public_status(self) -> ModelPublicStatus:
        key_configured = self.api_key is not None
        return ModelPublicStatus(
            enabled=self.enabled,
            configured=bool(self.base_url and self.model and key_configured),
            base_url=self.base_url,
            model=self.model,
            api_key_configured=key_configured,
            # No suffix, prefix or length of the actual secret is exposed.
            api_key_mask="********" if key_configured else None,
            timeout_seconds=self.timeout_seconds,
            max_calls_per_turn=self.max_calls_per_turn,
        )


class ModelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    enabled: bool | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    timeout_seconds: float | None = Field(default=None, ge=0.1, le=120, allow_inf_nan=False)
    max_calls_per_turn: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def no_conflicting_key_request(self) -> Self:
        if self.clear_api_key and self.api_key is not None and self.api_key.get_secret_value():
            raise ValueError("Choose either a new key or explicit key removal")
        return self


def load_model_settings() -> ZhiyuModelSettings:
    try:
        # BaseSettings accepts this runtime keyword; the pydantic mypy plugin
        # synthesizes a field-only constructor and does not declare it.
        return ZhiyuModelSettings(_env_file=MODEL_ENV_PATH)  # type: ignore[call-arg]
    except (ValueError, OSError):
        raise ModelConfigurationError() from None


def _environment_line(name: str, value: str) -> str:
    # Single-quoted dotenv does not interpret dollar expressions or shell syntax.
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"LLM_{name}='{escaped}'\n"


def save_model_settings(update: ModelSettingsUpdate) -> ZhiyuModelSettings:
    """Atomically save only to the extension's fixed private model.env.

    Null or an empty key retains the existing key; clear_api_key=True removes it.
    This method must be exposed only through the local operator configuration route,
    never through a model tool or a conversation action.
    """
    with _CONFIGURATION_LOCK:
        return _save_model_settings(update)


def _save_model_settings(update: ModelSettingsUpdate) -> ZhiyuModelSettings:
    current = load_model_settings()
    values: dict[str, Any] = current.model_dump()
    for name in ("enabled", "base_url", "model", "timeout_seconds", "max_calls_per_turn"):
        value = getattr(update, name)
        if value is not None:
            values[name] = value
    if update.clear_api_key:
        values["api_key"] = None
    elif update.api_key is not None and update.api_key.get_secret_value():
        values["api_key"] = update.api_key
    try:
        candidate = ZhiyuModelSettings(**values)
    except ValueError:
        raise ModelConfigurationError() from None
    secret = candidate.api_key.get_secret_value() if candidate.api_key is not None else ""
    private_content = "# Zhiyu extension private model configuration; never share this file.\n"
    private_content += _environment_line("ENABLED", "true" if candidate.enabled else "false")
    private_content += _environment_line("BASE_URL", candidate.base_url)
    private_content += _environment_line("MODEL", candidate.model)
    private_content += _environment_line("API_KEY", secret)
    private_content += _environment_line("TIMEOUT_SECONDS", str(candidate.timeout_seconds))
    private_content += _environment_line("MAX_CALLS_PER_TURN", str(candidate.max_calls_per_turn))
    temporary: Path | None = None
    try:
        # Refuse links: operator configuration must remain in this extension checkout.
        if any(path.is_symlink() for path in (MODEL_ENV_PATH, *MODEL_ENV_PATH.parents)):
            raise OSError("Private model configuration cannot use links")
        MODEL_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(
            prefix=".model-", suffix=".env", dir=MODEL_ENV_PATH.parent
        )
        temporary = Path(filename)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            os.chmod(temporary, 0o600)
            stream.write(private_content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, MODEL_ENV_PATH)
        temporary = None
    except OSError:
        raise ModelConfigurationError("LLM_CONFIGURATION_WRITE_FAILED") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return candidate
