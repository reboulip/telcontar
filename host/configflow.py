"""Shared, NiceGUI-free configuration-flow logic for the setup wizard (U2)
and settings view (U3) — originally the now-retired Textual TUI's
SetupScreen/ConfigScreen equivalent, kept framework-agnostic so the NiceGUI
web UI reads from one source of truth for profile options, service-specific
hints, credential validation, and the plaintext-keyring-fallback warning copy.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

# Package root: host/configflow.py → host/ → project root (or site-packages/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Human-readable labels for built-in profiles.
_PROFILE_LABELS: dict[str, str] = {
    "is_it_project": "IS/IT project — technical and business documents",
    "personal_files": "Personal files — invoices, contracts, administrative",
    "research_papers": "Research papers — academic and scientific articles",
}


def profile_options() -> list[tuple[str, str]]:
    """Return [(display_label, profile_id), ...] for a Select/dropdown.

    Reads TOML files from the bundled profiles/ directory. Falls back to a
    single safe default if the directory cannot be found.
    """
    profiles_dir = _PROJECT_ROOT / "profiles"
    options: list[tuple[str, str]] = []
    try:
        for path in sorted(profiles_dir.glob("*.toml")):
            stem = path.stem
            label = _PROFILE_LABELS.get(stem)
            if label is None:
                try:
                    data = tomllib.loads(path.read_text(encoding="utf-8"))
                    label = data.get("description") or data.get("name") or stem
                except Exception:
                    label = stem
            options.append((label, stem))
    except Exception:
        pass
    return options or [("General documents", "is_it_project")]


AZURE_API_VERSION = "2025-01-01-preview"

# Per-service hint/placeholder text for the API-details step — identical
# between the wizard and (if a service picker is ever added there) settings.
SERVICE_HINTS: dict[str, dict[str, str]] = {
    "openai_compatible": {
        "url_hint": (
            "Enter the base URL of your OpenAI-compatible service "
            "(e.g. Mammouth, OpenAI, a local inference server, etc.)."
        ),
        "url_placeholder": "https://…",
        "model_hint": "The exact model identifier documented by your provider.",
        "model_placeholder": "e.g. gpt-5",
    },
    "azure": {
        "url_hint": (
            "Azure OpenAI: use your deployment endpoint "
            "(ends with /openai/deployments/<model-name>)."
        ),
        "url_placeholder": "https://your-resource.openai.azure.com/openai/deployments/gpt-5",
        "model_hint": (
            "The deployment name you created in Azure OpenAI Studio "
            "(often, but not always, the same as the model)."
        ),
        "model_placeholder": "e.g. gpt-5",
    },
}


def validate_credentials(url: str, key: str, model: str, *, key_required: bool) -> str | None:
    """Validate url/key/model in the frozen order (url -> key -> model),
    returning the first error message or None. ``key_required=True`` is the
    wizard's stricter first-run case (an empty key is an error); settings
    passes ``key_required=False`` since a blank key there means "keep the
    saved key" — that also changes the URL message's wording to match each
    screen's existing, test-pinned copy.
    """
    if not url:
        return (
            "Please enter the web address of your AI service."
            if key_required
            else "Please enter the web address."
        )
    if key_required and not key:
        return "Please enter your API key."
    if not model:
        return "Please enter the model name."
    return None


def build_wizard_updates(
    url: str, key: str, model: str, profile: str, service: str
) -> dict[str, str]:
    """Build the settings-update dict for the first-run wizard's save step.
    Always includes the API key (the wizard requires one) — settings' own
    blank-key-preserves-existing rule is a separate builder, below."""
    updates: dict[str, str] = {
        "llm_base_url": url,
        "llm_api_key": key,
        "llm_model": model,
        "profile": profile,
    }
    if service == "azure":
        updates["llm_api_version"] = AZURE_API_VERSION
    return updates


APPROVAL_OPTIONS: list[tuple[str, str]] = [
    ("Always ask before any changes", "always"),
    ("Only ask before moving or quarantining files", "destructive_only"),
    ("Never ask — full automatic mode", "never"),
]


def build_settings_updates(
    url: str, key: str, model: str, profile: str, approval_mode: str
) -> dict[str, str]:
    """Build the settings-update dict for the settings view's save step.
    Unlike ``build_wizard_updates``, ``key`` is only included when non-empty
    — the blank-key-preserves-existing-key rule (U3's literal requirement)."""
    updates: dict[str, str] = {
        "llm_base_url": url,
        "llm_model": model,
        "profile": profile,
        "approval_mode": approval_mode,
    }
    if key:
        updates["llm_api_key"] = key
    return updates


def plaintext_warning(button_label: str, recovery_action: str = "go back") -> str:
    """Plain-text (no Rich/HTML markup — this module is UI-agnostic) warning
    shown when the OS keyring is unavailable and the user must explicitly
    confirm a plaintext fallback. ``button_label`` must match the actual
    button the user is being told to press again (U8's fix: the TUI wizard
    used to say "Finish" while its button read "Save & continue →")."""
    return (
        f'Your OS keyring is unavailable. Press "{button_label}" again to '
        "store your API key in PLAINTEXT at ~/.telcontar/config.env, or "
        f"{recovery_action} and fix your keyring first."
    )
