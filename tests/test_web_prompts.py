"""Tests for the Settings view's prompt-inspection expansion (V11) —
host/web/settings.py's "What telcontar tells the model" panel, the read-only
view of the composed ORGANIZE/QUERY/ANALYZE system prompts.

Uses the same NiceGUI headless `user` fixture as tests/test_web_ui.py. The
fixtures that make that harness safe (sys.modules['__main__'] preservation,
the fast render-poll, and web-session isolation) live in tests/web_harness.py
and are imported from there rather than re-derived — see that module's
docstring for the gotchas each one guards against.
"""

from __future__ import annotations

import pytest
from nicegui.testing import User

from tests.web_harness import (  # noqa: F401
    _fast_refresh,
    _preserve_dunder_main,
    _reset_session_registry,
)

# read_user_config() reads ~/.telcontar/config.env — a real per-machine file
# that may hold real values from manual testing. Blank it for every test here,
# mirroring tests/test_web_ui.py's own Settings-view section (U3) so this
# module stays hermetic regardless of what's saved on the machine running it.


@pytest.fixture(autouse=True)
def _blank_saved_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.settings.read_user_config", lambda: {})


async def test_settings_shows_prompt_inspection_expansion(user: User) -> None:
    await user.open("/settings")

    await user.should_see(marker="expansion-prompts")
    await user.should_see(marker="prompt-organize")
    await user.should_see(marker="prompt-query")
    await user.should_see(marker="prompt-analyze")


async def test_settings_prompt_inspection_shows_all_three_prompts(user: User) -> None:
    await user.open("/settings")
    await user.should_see(marker="prompt-organize")

    organize = user.find(marker="prompt-organize").elements.pop().value
    query = user.find(marker="prompt-query").elements.pop().value
    analyze = user.find(marker="prompt-analyze").elements.pop().value

    # ORGANIZE: the corpus is already analyzed (P6) — not something to do here.
    assert "already" in organize.lower() and "analyzed" in organize.lower()
    # QUERY: read-only — registry tools offered, no mutating tools mentioned.
    assert "list_documents" in query
    assert "execute_plan" not in query
    # ANALYZE: the injection-resistance delimiter explanation (S2/M10).
    assert "UNTRUSTED DOCUMENT CONTENT" in analyze


async def test_settings_prompt_inspection_shows_resolved_profile(user: User) -> None:
    # tests/conftest.py's autouse _fake_llm_env fixture pins PROFILE=is_it_project,
    # a real bundled profile — resolution should succeed.
    await user.open("/settings")
    await user.should_see(marker="prompt-profile-status")

    status = user.find(marker="prompt-profile-status").elements.pop()
    assert "is_it_project" in status.text
    assert "text-negative" not in status.classes


async def test_settings_prompt_inspection_surfaces_profile_load_failure(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transparency feature must not hide a profile-load failure — it must
    show which profile name actually loaded (or that none did), not silently
    render the internal "default" fallback as if nothing went wrong."""
    monkeypatch.setenv("PROFILE", "not_a_real_profile_xyz")

    await user.open("/settings")
    await user.should_see(marker="prompt-profile-status")

    status = user.find(marker="prompt-profile-status").elements.pop()
    assert "failed to load" in status.text
    assert "not_a_real_profile_xyz" in status.text
    assert "text-negative" in status.classes

    # Degrades gracefully: the prompts still render (generic fallback content)
    # rather than going blank just because the profile failed to load.
    organize = user.find(marker="prompt-organize").elements.pop().value
    assert '"default" domain profile' in organize


async def test_settings_prompt_inspection_notes_digest_and_instructions_omitted(
    user: User,
) -> None:
    await user.open("/settings")

    await user.should_see("corpus digest")
    await user.should_see("steering instructions")
