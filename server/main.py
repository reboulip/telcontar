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


def _confinement_roots(cfg) -> list[Path]:
    """Roots every path-taking tool is confined to (M2/S3): the run's target
    directory (if the host set one via TARGET_DIR) plus the server's own working
    directory, where `.organizer` and the quarantine dir live by default."""
    roots: list[Path] = []
    if cfg.target_dir is not None:
        roots.append(cfg.target_dir)
    roots.append(Path.cwd())
    return roots


def _check_within_root(path: str, cfg) -> None:
    from server.guards import check_within_root

    check_within_root(Path(path), _confinement_roots(cfg))


def _log_egress(path: str, content: str, tool: str, cfg) -> None:
    """Record a file whose content was sent to the LLM endpoint (S8/M12).

    ``content`` is what the tool actually returned (post-truncation), so the
    logged size reflects what really left the machine, not the file's full
    on-disk size.
    """
    from server import egress as _egress

    size = len(content.encode("utf-8", errors="replace"))
    _egress.append(cfg.egress_path, _egress.EgressEntry.new(path, size, tool))


def _log_egress_from_disk(path: str, tool: str, cfg) -> None:
    """Like ``_log_egress``, but sizes from the file itself rather than a
    returned string — used where a tool doesn't expose each input's individual
    contribution to its output (e.g. ``compare_documents``' combined diff).
    A conservative (upper-bound) estimate of what could have been exposed."""
    from server import egress as _egress

    try:
        size = Path(path).stat().st_size
    except OSError:
        return
    _egress.append(cfg.egress_path, _egress.EgressEntry.new(path, size, tool))


# ── Read-only tools ──────────────────────────────────────────────────────────


@mcp.tool()
def list_dir(path: str) -> dict:
    """Enumerate directory entries with metadata (size, type, mtime)."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    return tools.list_dir(path)


@mcp.tool()
def walk_tree(path: str, max_depth: int = 3) -> dict:
    """Recursively enumerate a directory tree up to max_depth levels deep.

    Complements list_dir (a single level): use it during ANALYZE to discover
    documents nested in subfolders in one call and to redesign the whole layout,
    not just the top level. Directory entries carry a nested `children` list until
    max_depth is reached, where deeper dirs are marked `truncated` (call walk_tree
    again on that subpath to go deeper)."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    hidden = frozenset({".organizer", cfg.quarantine_dir.name})
    return tools.walk_tree(path, max_depth, hidden_names=hidden)


@mcp.tool()
def read_file(path: str, max_chars: int = 4000) -> str:
    """Return file content up to max_chars characters."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    check_allowlist(Path(path), cfg.effective_allowlist_dirs())
    _check_within_root(path, cfg)
    result = tools.read_file(path, min(max_chars, cfg.max_snippet_chars))
    _log_egress(path, result, "read_file", cfg)
    return result


@mcp.tool()
def extract_text(path: str, max_chars: int = 4000) -> str:
    """Extract plain text from a PDF, Office, or Outlook email (.msg) file."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    check_allowlist(Path(path), cfg.effective_allowlist_dirs())
    _check_within_root(path, cfg)
    result = tools.extract_text(
        path,
        min(max_chars, cfg.max_snippet_chars),
        cfg.max_extract_file_bytes,
        cfg.max_extract_timeout_secs,
    )
    _log_egress(path, result, "extract_text", cfg)
    return result


@mcp.tool()
def compute_checksum(path: str) -> dict:
    """Compute a file's sha256 checksum, used as its unique document id."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    return tools.compute_checksum(path)


# ── Batch document-content tools (O1) ─────────────────────────────────────────


@mcp.tool()
def read_file_batch(paths: list[str], max_chars: int = 4000) -> dict:
    """Batch form of read_file: fetch text for many files in one round trip.

    Returns `{path: content | {"error": message}}` keyed by the exact path
    strings passed in — one bad path never fails the whole batch."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    allowed = cfg.effective_allowlist_dirs()
    results: dict[str, str | dict] = {}
    ok_paths: list[str] = []
    for path in paths:
        try:
            check_allowlist(Path(path), allowed)
            _check_within_root(path, cfg)
            ok_paths.append(path)
        except Exception as exc:
            results[path] = {"error": str(exc)}
    batch = tools.read_file_batch(ok_paths, min(max_chars, cfg.max_snippet_chars))
    for path, value in batch.items():
        results[path] = value
        if isinstance(value, str):
            _log_egress(path, value, "read_file_batch", cfg)
    return results


@mcp.tool()
def extract_text_batch(paths: list[str], max_chars: int = 4000) -> dict:
    """Batch form of extract_text: extract text from many PDF/Office/.msg files
    in one round trip. Returns `{path: content | {"error": message}}` keyed by
    the exact path strings passed in — one bad path never fails the whole batch."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    allowed = cfg.effective_allowlist_dirs()
    results: dict[str, str | dict] = {}
    ok_paths: list[str] = []
    for path in paths:
        try:
            check_allowlist(Path(path), allowed)
            _check_within_root(path, cfg)
            ok_paths.append(path)
        except Exception as exc:
            results[path] = {"error": str(exc)}
    batch = tools.extract_text_batch(
        ok_paths,
        min(max_chars, cfg.max_snippet_chars),
        cfg.max_extract_file_bytes,
        cfg.max_extract_timeout_secs,
    )
    for path, value in batch.items():
        results[path] = value
        if isinstance(value, str):
            _log_egress(path, value, "extract_text_batch", cfg)
    return results


@mcp.tool()
def compute_checksum_batch(paths: list[str]) -> dict:
    """Batch form of compute_checksum: sha256 for many files in one round trip.

    Returns `{path: checksum_hex | {"error": message}}` keyed by the exact
    path strings passed in — one bad path never fails the whole batch."""
    cfg = _get_settings()
    results: dict[str, str | dict] = {}
    ok_paths: list[str] = []
    for path in paths:
        try:
            _check_within_root(path, cfg)
            ok_paths.append(path)
        except Exception as exc:
            results[path] = {"error": str(exc)}
    results.update(tools.compute_checksum_batch(ok_paths))
    return results


@mcp.tool()
def compare_documents(path_a: str, path_b: str, max_chars: int = 4000) -> dict:
    """Extract text from two files and return a unified diff between them — e.g. to
    compare successive versions of a document (two COPIL decks)."""
    cfg = _get_settings()
    from server.guards import check_allowlist

    allowed = cfg.effective_allowlist_dirs()
    check_allowlist(Path(path_a), allowed)
    check_allowlist(Path(path_b), allowed)
    _check_within_root(path_a, cfg)
    _check_within_root(path_b, cfg)
    result = tools.compare_documents(
        path_a,
        path_b,
        min(max_chars, cfg.max_snippet_chars),
        cfg.max_extract_file_bytes,
        cfg.max_extract_timeout_secs,
    )
    _log_egress_from_disk(path_a, "compare_documents", cfg)
    _log_egress_from_disk(path_b, "compare_documents", cfg)
    return result


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


@mcp.tool()
def set_plan_rationale(plan_id: str, rationale: str) -> dict:
    """Attach a plain-language rationale to a plan (shown above the ops at approval)."""
    cfg = _get_settings()
    return tools.set_plan_rationale(plan_id, rationale, cfg.plans_dir)


@mcp.tool()
def set_plan_folder_notes(plan_id: str, notes: dict) -> dict:
    """Attach per-folder purpose notes to a plan for the approval-view target-layout
    preview. `notes` maps each target folder path to a short one-line purpose note
    (e.g. {"01_decisions": "Formal decision records", "_quarantine": "Duplicates and
    drafts"}); shown beside each folder when the user reviews the plan."""
    cfg = _get_settings()
    return tools.set_plan_folder_notes(plan_id, notes, cfg.plans_dir)


# ── Plan-building tools (write to plan, do not execute) ──────────────────────


@mcp.tool()
def propose_rename(path: str, new_name: str, plan_id: str) -> dict:
    """Stage a rename operation in the named plan."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    return tools.propose_rename(path, new_name, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_move(path: str, dest_dir: str, plan_id: str) -> dict:
    """Stage a move operation in the named plan."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    _check_within_root(dest_dir, cfg)
    return tools.propose_move(path, dest_dir, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_quarantine(path: str, plan_id: str) -> dict:
    """Stage a quarantine operation in the named plan."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    return tools.propose_quarantine(path, plan_id, cfg.plans_dir, cfg.quarantine_dir)


@mcp.tool()
def propose_create_file(path: str, content: str, plan_id: str) -> dict:
    """Stage creating a new file in the named plan; raises if the path already exists."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    return tools.propose_create_file(path, content, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_update_file(path: str, content: str, plan_id: str, overwrite: bool = False) -> dict:
    """Stage writing content to an existing (or new) file in the named plan. Refuses
    to replace an existing file unless overwrite=True — pass it explicitly for the
    rare legitimate overwrite; it is shown to the user at approval."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    return tools.propose_update_file(path, content, plan_id, cfg.plans_dir, overwrite)


@mcp.tool()
def propose_create_dir(path: str, plan_id: str) -> dict:
    """Stage creating a directory (and parents) in the named plan; idempotent."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
    return tools.propose_create_dir(path, plan_id, cfg.plans_dir)


@mcp.tool()
def propose_archive_document(checksum: str, plan_id: str, reason: str = "") -> dict:
    """Stage withdrawing a document from active memory in the named plan: flips its
    registry status to archived and moves its file to quarantine on execution."""
    cfg = _get_settings()
    return tools.propose_archive_document(
        checksum, reason, plan_id, cfg.plans_dir, cfg.registry_path, cfg.quarantine_dir
    )


@mcp.tool()
def propose_compress_quarantine(plan_id: str, delete_originals: bool = True) -> dict:
    """Stage losslessly compressing loose quarantined files into a verified zip
    archive in the named plan (reclaims space on execution)."""
    cfg = _get_settings()
    return tools.propose_compress_quarantine(
        plan_id, cfg.plans_dir, cfg.quarantine_dir, delete_originals
    )


# ── Gated execution tools ────────────────────────────────────────────────────


@mcp.tool()
def execute_plan(plan_id: str) -> dict:
    """Apply all operations in an approved plan; journals each one and reconciles
    registry paths so document records follow their files."""
    cfg = _get_settings()
    return tools.execute_plan(
        plan_id,
        cfg.plans_dir,
        cfg.journal_path,
        cfg.registry_path,
        cfg.quarantine_dir,
        cfg.archive_path,
    )


@mcp.tool()
def write_index(path: str) -> dict:
    """Emit INDEX.md (tree + changelog) and manifest.json for the organized tree at path."""
    cfg = _get_settings()
    _check_within_root(path, cfg)
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
    _check_within_root(path, _get_settings())
    return _sink_results([s.write_summary(path, content) for s in _resolve_output_sinks()])


@mcp.tool()
def write_folder_readme(path: str, content: str) -> dict:
    """Write the LLM-composed per-folder description to the profile's active output
    sink(s). The built-in local_markdown sink persists it as README.md."""
    _check_within_root(path, _get_settings())
    return _sink_results([s.write_folder_readme(path, content) for s in _resolve_output_sinks()])


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
    _check_within_root(path, cfg)
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
def record_document_batch(documents: list[dict]) -> dict:
    """Record (upsert) many analyzed documents in the registry in one call.

    Each item in `documents` has the same shape as `record_document`'s
    parameters (checksum, path, title, type, summary, provenance, date,
    entities, attributes, status). One invalid document never fails the whole
    batch — its validation error is collected instead. Returns
    `{"recorded": [record, ...], "errors": [{"index", "checksum", "path",
    "error"}, ...]}`.
    """
    cfg = _get_settings()
    for doc in documents:
        path = doc.get("path")
        if path:
            _check_within_root(path, cfg)
    return tools.record_document_batch(documents, cfg.registry_path, _get_profile())


@mcp.tool()
def get_document(checksum: str) -> dict | None:
    """Return a single registry record by checksum, or null if not recorded."""
    cfg = _get_settings()
    return tools.get_document(checksum, cfg.registry_path)


@mcp.tool()
def lookup_documents(checksums: list[str]) -> dict:
    """Batch registry lookup: {checksum: record | null}. Efficient known/new
    partitioning for a corpus without one get_document call per checksum."""
    cfg = _get_settings()
    return tools.lookup_documents(checksums, cfg.registry_path)


@mcp.tool()
def rehome_documents(paths: dict[str, str]) -> dict:
    """Update registry records' recorded path by checksum: {checksum: new_path}.

    Registry-only mutation (no plan, no approval gate) used by the
    deterministic host pre-pass to reconcile records whose on-disk location
    changed outside a plan-tracked op."""
    cfg = _get_settings()
    for new_path in paths.values():
        _check_within_root(new_path, cfg)
    return tools.rehome_documents(paths, cfg.registry_path)


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
# archive_document itself is no longer an agent-callable tool (S1) — it's only
# reached via propose_archive_document → execute_plan, same as compress_quarantine.


@mcp.tool()
def list_archived() -> list:
    """Return all archived-document log entries in chronological order."""
    cfg = _get_settings()
    return tools.list_archived(cfg.archive_path)


def main() -> None:
    import argparse
    from importlib.metadata import PackageNotFoundError, version

    def _version() -> str:
        try:
            return version("telcontar")
        except PackageNotFoundError:  # pragma: no cover - source checkout without install
            return "0.0.0+unknown"

    parser = argparse.ArgumentParser(
        prog="telcontar-server",
        description="MCP stdio server exposing telcontar's guarded file tools.",
    )
    parser.add_argument("--version", action="version", version=f"telcontar-server {_version()}")
    # Tolerate any extra args a launching MCP host may pass; --help/--version
    # are handled here and exit before the (blocking) stdio server starts.
    parser.parse_known_args()

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
