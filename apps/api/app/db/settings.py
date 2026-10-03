from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    database_url: str = (
        "postgresql+psycopg://bounded:bounded_local_only@127.0.0.1:54329/bounded_funds"
    )
    simulation_mode: bool = True
    llm_enabled: bool = False

    @field_validator("simulation_mode")
    @classmethod
    def require_simulation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Only the simulation environment is supported")
        return value
