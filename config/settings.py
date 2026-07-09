from __future__ import annotations

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
    # The directory being organized this run — set by the host as the TARGET_DIR
    # env var when it launches the MCP server subprocess. None outside a run (e.g.
    # some test harnesses), in which case path-confinement guards fall back to
    # just the `.organizer` working dir.
    target_dir: Path | None = None
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
    # S8: audit trail of document content sent to the LLM endpoint
    egress_path: Path = Path(".organizer/egress.jsonl")

    # Egress / extraction
    max_snippet_chars: int = 4000
    # S5: bounds on untrusted-document parsing (markitdown/pypdf) — a crash/DoS/
    # zip-bomb guard, not a sandbox. Input files larger than this are rejected
    # before parsing; parsing itself is wall-clock-bounded (works cross-platform,
    # including Windows, unlike a signal-based timeout).
    max_extract_file_bytes: int = 200_000_000
    max_extract_timeout_secs: float = 30.0
    # JSON array of absolute paths, e.g. '["C:/Users/me/docs"]'. Empty defaults to
    # the run's target directory (see effective_allowlist_dirs) — an explicit,
    # non-empty list here always overrides that default and is used as-is.
    allowlist_dirs: list[Path] = Field(default_factory=list)
    # Gate for non-local output sinks (e.g. a MediaWiki MCP integration). Built-in
    # local_markdown is always allowed; external sinks require this flag = True.
    egress_allow_external_sinks: bool = False

    def effective_allowlist_dirs(self) -> list[Path]:
        """The allowlist actually enforced (M7/S3): an explicit `allowlist_dirs`
        always wins; otherwise default to `[target_dir]` (confinement on by
        default) rather than "no restriction". Stays empty if neither is set."""
        if self.allowlist_dirs:
            return self.allowlist_dirs
        if self.target_dir is not None:
            return [self.target_dir]
        return []


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
            "LLM endpoint not configured. Launch telcontar and complete the setup wizard."
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


class PlaintextKeyFallbackNeeded(Exception):
    """Raised by save_user_config when the OS keyring is unavailable and the
    caller hasn't explicitly opted into storing the key in plaintext (S8).

    The caller (the setup wizard / config screen) is expected to warn the user
    loudly and re-call with ``allow_plaintext_fallback=True`` only on their
    explicit confirmation — never silently.
    """


def save_user_config(updates: dict[str, str], allow_plaintext_fallback: bool = False) -> None:
    """Persist settings to ~/.telcontar/config.env, storing the API key in the OS keyring.

    Non-sensitive values are written as plain KEY=VALUE lines. The API key is
    stored via the OS credential manager (Windows Credential Manager, macOS
    Keychain, SecretService on Linux). If the keyring is unavailable, this
    raises ``PlaintextKeyFallbackNeeded`` instead of silently writing the key in
    plaintext — pass ``allow_plaintext_fallback=True`` only after the caller has
    explicitly warned the user and gotten their confirmation.
    """
    _USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    api_key = updates.pop("llm_api_key", None)

    if api_key is not None:
        stored = _keyring_set(api_key)
        if not stored:
            if not allow_plaintext_fallback:
                raise PlaintextKeyFallbackNeeded(
                    "The OS keyring is unavailable — storing the API key would fall back "
                    "to plaintext at ~/.telcontar/config.env unless explicitly confirmed."
                )
            # Explicit, user-confirmed fallback — less secure, but no longer silent.
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
