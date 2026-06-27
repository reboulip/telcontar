"""MCP server entrypoint — exposes file tools over stdio transport."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from server import tools

mcp = FastMCP("directory-organizer")

_settings = None
_profile = None


def _get_settings():
    global _settings
    if _settings is None:
        from config.settings import load

        _settings = load()
    return _settings


def _get_profile():
    global _profile
    if _profile is None:
        from server.profile import load_profile

        cfg = _get_settings()
        _profile = load_profile(cfg.profile, cfg.profiles_dir)
    return _profile


# ── Read-only tools ──────────────────────────────────────────────────────────


@mcp.tool()
def list_dir(path: str) -> dict:
    """Enumerate directory entries with metadata (size, type, mtime)."""
    return tools.list_dir(path)


@mcp.tool()
def read_file(path: str, max_chars: int = 4000) -> str:
    """Return file content up to max_chars characters."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    check_allowlist(Path(path), cfg.allowlist_dirs)
    return tools.read_file(path, min(max_chars, cfg.max_snippet_chars))


@mcp.tool()
def extract_text(path: str, max_chars: int = 4000) -> str:
    """Extract plain text from a PDF or Office file via markitdown."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    check_allowlist(Path(path), cfg.allowlist_dirs)
    return tools.extract_text(path, min(max_chars, cfg.max_snippet_chars))


@mcp.tool()
def compute_checksum(path: str) -> dict:
    """Compute a file's sha256 checksum, used as its unique document id."""
    return tools.compute_checksum(path)


@mcp.tool()
def compare_documents(path_a: str, path_b: str, max_chars: int = 4000) -> dict:
    """Extract text from two files and return a unified diff between them — e.g. to
    compare successive versions of a document (two COPIL decks)."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    check_allowlist(Path(path_a), cfg.allowlist_dirs)
    check_allowlist(Path(path_b), cfg.allowlist_dirs)
    return tools.compare_documents(path_a, path_b, min(max_chars, cfg.max_snippet_chars))


# ── Plan management tools ────────────────────────────────────────────────────


@mcp.tool()
def create_plan() -> dict:
    """Create a new empty plan; returns plan_id and initial metadata."""
    cfg = _get_settings()
    return tools.create_plan(cfg.plans_dir)


@mcp.tool()
def get_plan(plan_id: str) -> dict:
    """Load and return a plan by plan_id, including all proposed ops."""
    cfg = _get_settings()
    return tools.get_plan(plan_id, cfg.plans_dir)


@mcp.tool()
def list_plans() -> list:
    """Return all plans with their current state and ops."""
    cfg = _get_settings()
    return tools.list_plans(cfg.plans_dir)


@mcp.tool()
def review_plan(plan_id: str) -> dict:
    """Deduplication and pre-flight check; returns a report without modifying the plan."""
    cfg = _get_settings()
    return tools.review_plan(plan_id, cfg.plans_dir)


@mcp.tool()
def approve_plan(plan_id: str) -> dict:
    """Transition a plan from pending to approved, authorizing execution."""
    cfg = _get_settings()
    return tools.approve_plan(plan_id, cfg.plans_dir)


# ── Plan-building tools (write to plan, do not execute) ──────────────────────


@mcp.tool()
def propose_rename(path: str, new_name: str, plan_id: str) -> dict:
    """Stage a rename operation in the named plan."""
    cfg = _get_settings()
    return tools.propose_rename(path, new_name, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_move(path: str, dest_dir: str, plan_id: str) -> dict:
    """Stage a move operation in the named plan."""
    cfg = _get_settings()
    return tools.propose_move(path, dest_dir, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_quarantine(path: str, plan_id: str) -> dict:
    """Stage a quarantine operation in the named plan."""
    cfg = _get_settings()
    return tools.propose_quarantine(path, plan_id, cfg.plans_dir, cfg.quarantine_dir)


# ── Direct file operations ───────────────────────────────────────────────────


@mcp.tool()
def move_file(path: str, dest_dir: str) -> dict:
    """Move a file to dest_dir; raises if the destination already exists."""
    return tools.move_file(path, dest_dir)


@mcp.tool()
def rename_file(path: str, new_name: str) -> dict:
    """Rename a file in place; raises if the new name already exists."""
    return tools.rename_file(path, new_name)


@mcp.tool()
def create_file(path: str, content: str) -> dict:
    """Write content to path; raises if the file already exists."""
    return tools.create_file(path, content)


@mcp.tool()
def update_file(path: str, content: str) -> dict:
    """Overwrite or create content at path."""
    return tools.update_file(path, content)


@mcp.tool()
def create_dir(path: str) -> dict:
    """Create a directory (and parents); idempotent if it already exists."""
    return tools.create_dir(path)


# ── Gated execution tools ────────────────────────────────────────────────────


@mcp.tool()
def execute_plan(plan_id: str) -> dict:
    """Apply all operations in an approved plan; journals each one and reconciles
    registry paths so document records follow their files."""
    cfg = _get_settings()
    return tools.execute_plan(plan_id, cfg.plans_dir, cfg.journal_path, cfg.registry_path)


@mcp.tool()
def write_index(path: str) -> dict:
    """Emit INDEX.md (tree + changelog) and manifest.json for the organized tree at path."""
    cfg = _get_settings()
    return tools.write_index(path, cfg.journal_path)


def _resolve_output_sinks() -> list:
    """Resolve the active output sinks from the profile, gating external egress."""
    from server.sinks import resolve_sinks

    cfg = _get_settings()
    profile = _get_profile()
    return resolve_sinks(profile.sinks_default, allow_external=cfg.egress_allow_external_sinks)


def _sink_results(results: list[dict]) -> dict:
    """Collapse per-sink results to a single dict when only one sink is active."""
    if len(results) == 1:
        return results[0]
    return {"sinks": results}


@mcp.tool()
def write_summary(path: str, content: str) -> dict:
    """Write the LLM-composed project synthesis to the profile's active output
    sink(s). The built-in local_markdown sink persists it as SUMMARY.md."""
    return _sink_results([s.write_summary(path, content) for s in _resolve_output_sinks()])


@mcp.tool()
def write_folder_readme(path: str, content: str) -> dict:
    """Write the LLM-composed per-folder description to the profile's active output
    sink(s). The built-in local_markdown sink persists it as README.md."""
    return _sink_results([s.write_folder_readme(path, content) for s in _resolve_output_sinks()])


# ── Recovery tools ───────────────────────────────────────────────────────────


@mcp.tool()
def undo_last() -> dict:
    """Revert the most recent journaled operation."""
    cfg = _get_settings()
    return tools.undo_last(cfg.journal_path, cfg.plans_dir)


# ── Document registry (the engine's persistent memory) ───────────────────────


@mcp.tool()
def record_document(
    checksum: str,
    path: str,
    title: str,
    type: str,
    summary: str,
    provenance: str,
    date: str | None = None,
    entities: list[dict] | None = None,
    attributes: dict | None = None,
    status: str = "active",
) -> dict:
    """Record (upsert) an analyzed document in the registry, keyed by checksum.

    `type` must be one of the active profile's document types. `entities` is a list
    of {name, role, kind} — the author is just an entity with role "author".
    Only include people explicitly named in the document; never infer an author.
    `provenance` is the document's knowledge contribution (why it is here).
    """
    cfg = _get_settings()
    return tools.record_document(
        checksum=checksum,
        path=path,
        title=title,
        type=type,
        summary=summary,
        provenance=provenance,
        date=date,
        entities=entities,
        attributes=attributes,
        status=status,
        registry_path=cfg.registry_path,
        profile=_get_profile(),
    )


@mcp.tool()
def get_document(checksum: str) -> dict | None:
    """Return a single registry record by checksum, or null if not recorded."""
    cfg = _get_settings()
    return tools.get_document(checksum, cfg.registry_path)


@mcp.tool()
def list_documents() -> list:
    """Return all recorded documents with their metadata, oldest first."""
    cfg = _get_settings()
    return tools.list_documents(cfg.registry_path)


@mcp.tool()
def get_registry() -> dict:
    """Return the entire document registry as a single object."""
    cfg = _get_settings()
    return tools.get_registry(cfg.registry_path)


@mcp.tool()
def find_duplicates() -> list:
    """Return fuzzy candidate-duplicate clusters (same info / replaceable) to judge."""
    cfg = _get_settings()
    return tools.find_duplicates(cfg.registry_path)


@mcp.tool()
def find_modified_documents() -> list:
    """Return groups sharing a title but differing in content (recently modified)."""
    cfg = _get_settings()
    return tools.find_modified_documents(cfg.registry_path)


# ── Project event journal (the project narrative) ────────────────────────────


@mcp.tool()
def create_event(sentence: str, date: str | None = None) -> dict:
    """Record a project event: a short, verb-led sentence stamped with the date it
    occurred (ISO YYYY-MM-DD, or null if unknown). Distinct from the undo journal."""
    cfg = _get_settings()
    return tools.create_event(sentence, date, cfg.events_path)


@mcp.tool()
def list_events() -> list:
    """Return all recorded project events in chronological order."""
    cfg = _get_settings()
    return tools.list_events(cfg.events_path)


# ── Knowledge graph (derived from registry + events) ─────────────────────────


@mcp.tool()
def build_graph() -> dict:
    """Rebuild the knowledge graph (documents, entities, events as nodes/edges)
    from the registry and event journal, persist it, and return {nodes, edges}."""
    cfg = _get_settings()
    return tools.build_graph(cfg.registry_path, cfg.events_path, cfg.graph_path)


@mcp.tool()
def get_graph() -> dict:
    """Return the persisted knowledge graph as {nodes, edges}; empty if never built."""
    cfg = _get_settings()
    return tools.get_graph(cfg.graph_path)


@mcp.tool()
def get_actors() -> list:
    """Return the project's main actors — top entities ranked from the knowledge
    graph, capped at the active profile's salient_cap. Build the graph first."""
    cfg = _get_settings()
    return tools.get_actors(cfg.graph_path, _get_profile().salient_cap)


# ── Archived-documents journal ("retirer de la mémoire") ─────────────────────


@mcp.tool()
def archive_document(checksum: str, reason: str = "") -> dict:
    """Withdraw a document from active memory: flip its registry status to archived,
    move its file to quarantine (journaled, reversible via undo_last), and append to
    the archive log. Never deletes. Identify the document by its checksum."""
    cfg = _get_settings()
    return tools.archive_document(
        checksum,
        reason,
        cfg.registry_path,
        cfg.quarantine_dir,
        cfg.journal_path,
        cfg.archive_path,
    )


@mcp.tool()
def list_archived() -> list:
    """Return all archived-document log entries in chronological order."""
    cfg = _get_settings()
    return tools.list_archived(cfg.archive_path)


def main() -> None:
    mcp.run(transport="stdio")
