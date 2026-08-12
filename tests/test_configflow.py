"""Tests for host/configflow.py — the setup wizard (U2) / settings view (U3)
shared, framework-agnostic configuration-flow logic.
"""

from __future__ import annotations

from host.configflow import (
    AZURE_API_VERSION,
    SERVICE_HINTS,
    build_wizard_updates,
    plaintext_warning,
    profile_options,
    validate_credentials,
)


class TestProfileOptions:
    def test_returns_at_least_one_option(self) -> None:
        options = profile_options()
        assert options
        assert all(isinstance(label, str) and isinstance(value, str) for label, value in options)

    def test_includes_bundled_is_it_project_profile(self) -> None:
        values = [value for _label, value in profile_options()]
        assert "is_it_project" in values


class TestValidateCredentials:
    def test_wizard_url_missing_message(self) -> None:
        assert (
            validate_credentials("", "key", "model", key_required=True)
            == "Please enter the web address of your AI service."
        )

    def test_settings_url_missing_message_differs_from_wizard(self) -> None:
        assert (
            validate_credentials("", "key", "model", key_required=False)
            == "Please enter the web address."
        )

    def test_wizard_key_required_and_missing(self) -> None:
        assert (
            validate_credentials("https://x", "", "model", key_required=True)
            == "Please enter your API key."
        )

    def test_settings_key_not_required(self) -> None:
        # Blank key is valid when key_required=False (settings' "keep the
        # saved key" rule) — falls through to the model check.
        assert validate_credentials("https://x", "", "", key_required=False) == (
            "Please enter the model name."
        )

    def test_model_missing_message(self) -> None:
        assert (
            validate_credentials("https://x", "key", "", key_required=True)
            == "Please enter the model name."
        )

    def test_all_present_returns_none(self) -> None:
        assert validate_credentials("https://x", "key", "model", key_required=True) is None

    def test_validation_order_is_url_then_key_then_model(self) -> None:
        # Every field blank — the URL error must win, not the others.
        assert (
            validate_credentials("", "", "", key_required=True)
            == "Please enter the web address of your AI service."
        )


class TestBuildWizardUpdates:
    def test_openai_compatible_has_no_api_version(self) -> None:
        updates = build_wizard_updates(
            "https://x", "key", "model", "is_it_project", "openai_compatible"
        )
        assert updates == {
            "llm_base_url": "https://x",
            "llm_api_key": "key",
            "llm_model": "model",
            "profile": "is_it_project",
        }

    def test_azure_adds_api_version(self) -> None:
        updates = build_wizard_updates("https://x", "key", "model", "is_it_project", "azure")
        assert updates["llm_api_version"] == AZURE_API_VERSION


class TestPlaintextWarning:
    def test_references_the_given_button_label(self) -> None:
        message = plaintext_warning("Save & continue →")
        assert '"Save & continue →"' in message
        assert "keyring is unavailable" in message.lower()

    def test_default_recovery_action_is_go_back(self) -> None:
        assert "go back" in plaintext_warning("Save")

    def test_recovery_action_is_overridable(self) -> None:
        assert "cancel" in plaintext_warning("Save", "cancel")


def test_service_hints_cover_both_services() -> None:
    assert set(SERVICE_HINTS) == {"openai_compatible", "azure"}
    for hints in SERVICE_HINTS.values():
        assert hints["url_hint"]
        assert hints["url_placeholder"]
        assert hints["model_hint"]
        assert hints["model_placeholder"]
