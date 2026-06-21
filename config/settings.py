from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM endpoint — required at startup
    llm_base_url: str
    llm_api_key: str
    llm_model: str = "gpt-5"
    llm_api_version: str = ""  # Azure only; leave empty for Mammouth

    # Safety
    approval_mode: Literal["always", "destructive_only", "never"] = "always"
    quarantine_dir: Path = Path("_quarantine")
    journal_path: Path = Path(".organizer/journal.jsonl")

    # Egress / extraction
    max_snippet_chars: int = 4000
    # JSON array of absolute paths, e.g. '["C:/Users/me/docs"]'. Empty = no restriction.
    allowlist_dirs: list[Path] = Field(default_factory=list)


def load() -> Settings:
    """Load and validate settings from environment / .env file."""
    return Settings()
