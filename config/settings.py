from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Paths ─────────────────────────────────────────────────────────────────────

# User-level config dir: ~/.telcontar/ (cross-platform hidden dir in home)
_USER_CONFIG_DIR: Path = Path.home() / ".telcontar"
_USER_CONFIG: Path = _USER_CONFIG_DIR / "config.env"

# Package root: config/ → project root (or site-packages/ when installed)
_PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent

# ── Settings model ────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # .env (CWD) takes priority; ~/.telcontar/config.env is the fallback for
        # installed-tool use where no project-local .env exists.
        env_file=(".env", str(_USER_CONFIG)),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM endpoint — validated by load(), not here, so Settings() can be
    # instantiated even when the wizard hasn't run yet.
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-5"
    llm_api_version: str = ""  # Azure only; leave empty for Mammouth

    # Safety
    approval_mode: Literal["always", "destructive_only", "never"] = "always"
    quarantine_dir: Path = Path("_quarantine")
    journal_path: Path = Path(".organizer/journal.jsonl")
    events_path: Path = Path(".organizer/events.jsonl")
    plans_dir: Path = Path(".organizer/plans")

    # Domain profile — adapts the engine to a kind of document corpus
    profile: str = "is_it_project"
    profiles_dir: Path = Path("profiles")

    # Document memory — persistent, content-addressed registry
    registry_path: Path = Path(".organizer/registry.json")
    # Knowledge graph — derived projection of the registry + event journal
    graph_path: Path = Path(".organizer/graph.json")
    # Archived-documents journal — log of documents withdrawn from active memory
    archive_path: Path = Path(".organizer/archive.jsonl")

    # Egress / extraction
    max_snippet_chars: int = 4000
    # JSON array of absolute paths, e.g. '["C:/Users/me/docs"]'. Empty = no restriction.
    allowlist_dirs: list[Path] = Field(default_factory=list)
    # Gate for non-local output sinks (e.g. a MediaWiki MCP integration). Built-in
    # local_markdown is always allowed; external sinks require this flag = True.
    egress_allow_external_sinks: bool = False


# ── Public helpers ─────────────────────────────────────────────────────────────


def load() -> Settings:
    """Load and validate settings, pulling the API key from the OS keyring if needed."""
    settings = Settings()

    # If the API key wasn't found in env/files, check the OS credential store.
    if not settings.llm_api_key:
        key = _keyring_get()
        if key:
            settings = settings.model_copy(update={"llm_api_key": key})

    if not settings.llm_base_url or not settings.llm_api_key:
        raise ValueError(
            "LLM endpoint not configured. Launch organizer-host and complete the setup wizard."
        )

    return settings


def is_configured() -> bool:
    """True if the minimum required settings (URL + API key) are present."""
    try:
        s = Settings()
    except Exception:
        return False

    if not s.llm_base_url:
        return False

    if s.llm_api_key:
        return True

    return bool(_keyring_get())


def save_user_config(updates: dict[str, str]) -> None:
    """Persist settings to ~/.telcontar/config.env, storing the API key in the OS keyring.

    Non-sensitive values are written as plain KEY=VALUE lines.  The API key is
    stored via the OS credential manager (Windows Credential Manager, macOS
    Keychain, SecretService on Linux).  If keyring is unavailable, it falls
    back to the config file.
    """
    _USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    api_key = updates.pop("llm_api_key", None)

    if api_key is not None:
        stored = _keyring_set(api_key)
        if not stored:
            # Keyring unavailable — fall back to plain file (less secure).
            updates["llm_api_key"] = api_key

    # Read the existing file so we can merge rather than overwrite.
    existing: dict[str, str] = _read_config_file()

    for k, v in updates.items():
        existing[k.upper()] = v

    lines = [f"{k}={v}" for k, v in existing.items()]
    _USER_CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_user_config() -> dict[str, str]:
    """Return the raw key→value pairs from ~/.telcontar/config.env (lowercase keys).

    Does NOT include the API key (stored in keyring); callers that need to
    check whether a key exists should call is_configured().
    """
    result = _read_config_file()
    return {k.lower(): v for k, v in result.items()}


# ── Internal helpers ──────────────────────────────────────────────────────────


def _read_config_file() -> dict[str, str]:
    """Parse ~/.telcontar/config.env into an uppercase-keyed dict."""
    if not _USER_CONFIG.exists():
        return {}
    result: dict[str, str] = {}
    for line in _USER_CONFIG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip().upper()] = v.strip()
    return result


def _keyring_get() -> str:
    """Return the API key from the OS credential store, or '' on any failure."""
    try:
        import keyring  # type: ignore[import-untyped]

        return keyring.get_password("telcontar", "llm_api_key") or ""
    except Exception:
        return ""


def _keyring_set(api_key: str) -> bool:
    """Store api_key in the OS credential store. Returns True on success."""
    try:
        import keyring  # type: ignore[import-untyped]

        keyring.set_password("telcontar", "llm_api_key", api_key)
        return True
    except Exception:
        return False
