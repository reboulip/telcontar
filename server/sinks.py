"""Output-sink abstraction: where the engine's Markdown artifacts are emitted.

A ``Sink`` is a destination for the synthesized outputs (project summary,
per-folder READMEs). The built-in ``local_markdown`` sink writes them to local
files — the default for every profile. Other sinks (e.g. a MediaWiki wiki) are
*external*: they live in separate MCP integrations, not in this codebase, and
are gated behind the ``egress_allow_external_sinks`` setting so nothing leaves
the machine without an explicit opt-in.

The active sinks come from the profile's ``[sinks] default`` list
(``Profile.sinks_default``); ``resolve_sinks`` turns those names into instances,
refusing any name that is not a built-in unless external egress is allowed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from server import tools as _tools


@runtime_checkable
class Sink(Protocol):
    """A destination for the engine's synthesized Markdown outputs."""

    name: str
    external: bool

    def write_summary(self, target_dir: str, content: str) -> dict: ...

    def write_folder_readme(self, folder: str, content: str) -> dict: ...


class LocalMarkdownSink:
    """Built-in sink: persist outputs as local Markdown files (the default)."""

    name = "local_markdown"
    external = False

    def write_summary(self, target_dir: str, content: str) -> dict:
        return _tools.write_summary(target_dir, content)

    def write_folder_readme(self, folder: str, content: str) -> dict:
        return _tools.write_folder_readme(folder, content)


# Registry of sinks built into this codebase. External sinks (e.g. MediaWiki)
# are intentionally absent — they are shipped as separate MCP integrations.
_BUILTIN_SINKS: dict[str, type] = {"local_markdown": LocalMarkdownSink}


def resolve_sinks(names: list[str], *, allow_external: bool) -> list[Sink]:
    """Instantiate the active sinks named in the profile's ``sinks_default``.

    Built-in sinks are created directly. Any name that is not built in is treated
    as an external sink: it raises ``PermissionError`` unless ``allow_external``
    is set, and even then raises ``NotImplementedError`` because external sinks
    are provided as separate MCP integrations rather than implemented here.
    """
    sinks: list[Sink] = []
    for nm in names:
        cls = _BUILTIN_SINKS.get(nm)
        if cls is not None:
            sinks.append(cls())
            continue
        if not allow_external:
            raise PermissionError(
                f"Output sink {nm!r} is external; set egress_allow_external_sinks=True "
                f"to permit non-local egress."
            )
        raise NotImplementedError(
            f"External output sink {nm!r} is not built in; provide it as a separate "
            f"MCP integration."
        )
    return sinks
