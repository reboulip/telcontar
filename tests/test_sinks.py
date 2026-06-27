"""Tests for the output-sink abstraction (I5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.sinks import LocalMarkdownSink, Sink, resolve_sinks


class TestLocalMarkdownSink:
    def test_satisfies_sink_protocol(self) -> None:
        assert isinstance(LocalMarkdownSink(), Sink)

    def test_is_not_external(self) -> None:
        sink = LocalMarkdownSink()
        assert sink.external is False
        assert sink.name == "local_markdown"

    def test_write_summary_persists_file(self, tmp_path: Path) -> None:
        result = LocalMarkdownSink().write_summary(str(tmp_path), "synthèse")
        assert (tmp_path / "SUMMARY.md").read_text(encoding="utf-8") == "synthèse"
        assert result["written"] == str(tmp_path / "SUMMARY.md")

    def test_write_folder_readme_persists_file(self, tmp_path: Path) -> None:
        folder = tmp_path / "decisions"
        folder.mkdir()
        result = LocalMarkdownSink().write_folder_readme(str(folder), "les décisions")
        assert (folder / "README.md").read_text(encoding="utf-8") == "les décisions"
        assert result["written"] == str(folder / "README.md")


class TestResolveSinks:
    def test_resolves_builtin_local_markdown(self) -> None:
        sinks = resolve_sinks(["local_markdown"], allow_external=False)
        assert len(sinks) == 1
        assert isinstance(sinks[0], LocalMarkdownSink)

    def test_empty_list_resolves_to_no_sinks(self) -> None:
        assert resolve_sinks([], allow_external=False) == []

    def test_external_sink_blocked_when_egress_disallowed(self) -> None:
        with pytest.raises(PermissionError, match="external"):
            resolve_sinks(["mediawiki"], allow_external=False)

    def test_external_sink_not_built_in_even_when_allowed(self) -> None:
        with pytest.raises(NotImplementedError, match="separate"):
            resolve_sinks(["mediawiki"], allow_external=True)

    def test_builtin_resolved_alongside_blocked_external(self) -> None:
        # the local one is fine, but the external one still gates the call
        with pytest.raises(PermissionError):
            resolve_sinks(["local_markdown", "mediawiki"], allow_external=False)
