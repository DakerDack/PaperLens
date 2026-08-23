from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MINERU_COMMAND = (
    PROJECT_ROOT / ".venv-mineru312" / "Scripts" / "mineru.exe"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    paperlens_env: Literal["development", "test", "production"] = "development"
    paperlens_data_dir: Path = Path("./data")
    paperlens_model_mode: Literal["mock", "live"] = "mock"
    max_pdf_mb: int = Field(default=30, ge=1, le=200)
    mineru_command: str = str(DEFAULT_MINERU_COMMAND)
    mineru_backend: Literal["pipeline"] = "pipeline"
    mineru_timeout_seconds: int = Field(default=300, ge=30, le=1800)
    hy3_base_url: str = "https://tokenhub.tencentmaas.com/v1"
    hy3_model: str = "hy3"
    hy3_api_key: str = ""
    hy3_timeout_seconds: int = Field(default=120, ge=10, le=600)
    hy3_max_retries: int = Field(default=2, ge=0, le=2)

    @field_validator("paperlens_data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("mineru_command")
    @classmethod
    def resolve_mineru_command(cls, value: str) -> str:
        candidate = Path(value).expanduser()
        if candidate.name == value and not value.startswith("."):
            return value
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return str(candidate.resolve())


settings = Settings()
