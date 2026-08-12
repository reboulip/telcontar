# Module Reference — Web UI

Per-file breakdown of `host/web/`, the NiceGUI-based web UI package. This page is the `host/web/`-specific continuation of the [Module Reference](core.md), which covers `config/`, `server/`, and `host/`'s other (non-web) modules. For auto-generated API docs, see the [API Reference](../../reference/api/server.md).

---

## `host/web/` (Phase 18, extended by Phase 19 T2/T3/T5/T6/T7, Phase 20 U1-U7/U10, Phase 21 V1/V5/V7/V11/V12/V13a/V13c/V15)

**Role:** NiceGUI-based web UI package — originally the first piece of a planned
Textual→NiceGUI migration. As of S6, `telcontar --web` (`host/main.py`, lazy import)
launched it in place of the then-default Textual TUI; as of U10, that flag was
gone and the default inverted — bare `telcontar` launched the web UI, with
`--tui` as an escape hatch back to the Textual TUI. As of Phase 22 (W1), the
Textual TUI and `--tui` were deleted outright — telcontar now always launches
the web UI. As of V1, that launch opens in a native `pywebview` window by
default rather than a browser tab (Windows only; falls back to the browser,
with a stderr warning, if `pywebview` isn't installed or the platform isn't
Windows) — `--browser` forces the browser tab. As
of U2 it has its own first-run setup wizard, at parity with the TUI's; U3 a settings
view reachable from every screen; U4 a TUI-faithful approval dialog plus a
sidebar tree that refreshes itself after `execute_plan`; U5 a TUI-faithful cost
estimate dialog; U6 a journal view + undo, with a visible toolbar affordance; and
U7 a read-only query view (`/query/{run_id}`), reached via a "Query this corpus"
button on the organize view once a run finishes. As of U1, the landing page itself
also offers direct Organize/Query/Settings entry points at parity with the TUI's
`StartupScreen` — Settings (U3's sidebar button) and the folder picker (Phase
19's T3 sidebar tree) were already in place, so U1's remaining piece was a Query
button beside Organize's, both now reporting real validation errors instead of
silently no-op'ing — see `host/main.py`, [core module reference](core.md). As of V5, it also has a corpus
browser at `/corpus/{run_id}` — a sortable, filterable table over the document
registry with a per-document detail pane, reached via a "Browse corpus" button
beside "Query this corpus" once a run finishes — merging what used to be the
separate V4 document-preview idea into one screen, and reachable without any
LLM call or agent turn at all (unlike query mode). No TUI equivalent exists;
see `host/web/corpus.py`/`host/web/corpus_view.py` below.

**`host/web/session.py`** — framework-agnostic per-run state, no `nicegui` import.
Key types: `RunSession` (`run_id`, `target`, `mode: Literal["organize", "query"] =
"organize"` (U7), `transcript`, `steps`, `activity_log: list[ActivityEntry]`
(V16), `activity`, `status`, `tokens`,
`progress`, `pending: PendingRequest | None`, `messages: asyncio.Queue`,
`history: list[dict] | None`, `narrator`, `task`, `fs_revision: int`),
`TranscriptItem` (`seq`/`speaker`/`text`), `StepRecord`
(`seq`/`tool`/`summary`/`args`/`detail`/`status: "running"|"ok"|"error"`),
`ActivityEntry` (`seq`/`text`, V16),
`PendingRequest` (`request_id`/`kind: "approval"|"cost"|"ask"` (`"ask"` added
V12)/`payload`/`future: asyncio.Future`). `mode`
(U7) is one `RunSession` type/registry serving both organize and query runs
rather than a parallel `QuerySession` type — query mode needs the exact same
`add_turn`/`open_step`/`close_step`/`status`/`tokens` primitives, and
`pending`/`progress` simply stay unused for query sessions.

As of T5/T6, `transcript: list[TranscriptItem]` is turns-only — genuine
user↔telcontar exchanges (chat, ask_user, approval/cost outcomes, done/error) —
and `TranscriptItem` no longer carries a `kind`/`lines` discriminator; tool
activity lives instead in `steps: list[StepRecord]`, sharing the same `_seq`
counter as `transcript` for a stable relative ordering. As of V16, a third such
list, `activity_log: list[ActivityEntry]`, shares that same counter too: it
persists one entry per macro-phase change, appended via `add_activity(text)`
(called alongside the pre-existing `activity: str` scalar, which is unchanged
and still what earlier tests assert against) — giving the web UI a reviewable
history of phase changes instead of a single line that's overwritten and lost.
Deliberately not folded into `transcript`: `activity_log`, like `steps`, is
telcontar's own narration, not a genuine user↔telcontar exchange — this data-model
separation is unchanged by X3's `thread()` below, which only merges the two into
one chronologically-sorted *view* for rendering. `thread() -> list[TranscriptItem
| ActivityEntry]` (X3) returns `transcript` and `activity_log` merged and sorted
by their shared `_seq` counter — a plain `sorted()`, no timestamps needed, since
both lists already share that one counter — so the run page's conversation
column can interleave turns and phase-change narration in the order they
actually happened, instead of rendering them in two visually separate columns;
`steps` is deliberately excluded, it stays in its own log-strip drawer (see
`host/web/steplog.py` above and `run_page` below). `add_turn(speaker, text)`
appends a turn. `open_step(tool, summary, args=None)` starts a step "running" and
tracks it as the session's one currently-open step; `close_step(result, *, ok) ->
StepRecord | None` pairs it with its result as pretty-printed JSON
(`{"args": step.args, "result": result}`, capped at `_MAX_STEP_DETAIL_CHARS =
20_000` chars with a "(truncated)" suffix — a batch read/extract result can be
megabytes of document text, and this is a display cap, distinct from the egress
cap `MAX_SNIPPET_CHARS` already enforces upstream) and marks it `"ok"`/`"error"`,
returning the closed `StepRecord` (`None` if none was open, U4 — was previously
`None` unconditionally) so a caller like `AgentBridge` can inspect `.tool` to
decide whether the call just mutated the tree.
A step left "running" forever (the run errored out mid-call before its matching
`tool_result` arrived) is the intentional, correct visual, not a bug — it shows
exactly where things stopped. These replace the old `append_step`/`_steps_item`
group-building logic that mirrored `OrganizerScreen`'s `_add_turn`/`_append_step`.
`new_pending`/`resolve_pending` manage the one in-flight approval/cost/ask request
per session; as of U4, `resolve_pending(result, *, request_id=None)` takes an optional
`request_id` that, when given, must match the *current* `pending.request_id` or the
call is silently ignored — stops a stale dialog (another browser tab, or one left
over after a reload) from resolving a different, newer pending request than the one
it was actually shown. `request_id` is optional so the app-shutdown hook (which has
no dialog and just rejects whatever is pending) keeps working unchanged.

`bump_fs_revision()` (U4) increments `fs_revision`, a counter signalling "the
target directory's contents changed on disk" — bumped by `AgentBridge` after a
tree-mutating tool result (see below) and, as of U6, also called directly by
the journal dialog's own undo-confirm handler (an undo changes what's on disk
too) — consumed by `run_page`'s sidebar-tree refresh and journal-count refresh,
so the poll loop can rebuild the tree/refresh the count only when something
actually changed instead of on every tick. `has_open_step() -> bool` (U6) reports
whether `_open_step` is set — true while a tool call is still running — and
gates `build_journal_dialog`'s Undo button: undo must be blocked in this state,
since `server.journal.pop_last` rewrites the whole journal file while the MCP
server subprocess may still be appending to it, and racing them can silently
drop audit records.
Module-level registry `create(target, *, mode="organize") -> RunSession` /
`get(run_id)` / `close(run_id)` / `all_sessions()`, keyed by a
`secrets.token_urlsafe(16)` run id —
deliberately unit-testable in plain pytest, since a page (`host/web/main.py`) only
polls and mutates this data rather than deciding how it's drawn. `get_sidebar_width()
-> int` / `set_sidebar_width(width: int) -> int` (T4) manage one in-memory
sidebar-width preference (`SIDEBAR_WIDTH_DEFAULT`/`_MIN`/`_MAX` = 380/240/1000px —
`_MAX` raised from 720 in V13b, since the step-detail section now shares this
same drawer and its codemirror content wants more horizontal room) for
the process's lifetime — a module-level global rather than a `RunSession` field,
since it must also apply on the picker route where no `RunSession` exists yet, and
telcontar is single-user so there's no other viewer's preference it could clobber.
`set_sidebar_width` clamps to `[SIDEBAR_WIDTH_MIN, SIDEBAR_WIDTH_MAX]` and returns
the clamped value actually stored.

`find_by_target(target, mode="query") -> RunSession | None` (X11) linear-scans
`_SESSIONS` for one whose `.mode` matches and whose `.target.resolve()` equals the
(resolved) target — lets the nav shell's Query tab (`host/web/shell.py`, below) and
the run page's "Query this corpus" button reuse one query session (and its MCP
subprocess) per target instead of minting a new one on every click. A companion
module-level "active run" tracker — `set_active(run_id)` / `get_active() ->
RunSession | None`, backed by a private `_active_run_id: str | None` — records
which *organize*-mode session the persistent nav header's Conversation/Corpus tabs
should point at: `create()` calls `set_active()` automatically whenever
`mode="organize"`, and `app_shell()` also calls it whenever it's given an explicit
`session=`. `get_active()` is what lets a route with no session of its own in
scope (e.g. `/settings`) still resolve a working Conversation/Corpus target for
the nav tabs, instead of just disabling them outright.

**`host/web/bridge.py`** — `AgentBridge(session)`, also `nicegui`-free. Implements
the same callback contract the (now-deleted) Textual TUI's `OrganizerScreen` used:
`on_event`/`on_approval_needed`/`on_cost_approval_needed`/`on_ask_user_needed`. As
of V12, `on_ask_user_needed` creates an `"ask"`-kind `PendingRequest`
(`session.new_pending("ask", {"questions": questions})`) and awaits its future —
the same request-scoped pattern `on_approval_needed`/`on_cost_approval_needed`
already use — resolved by the structured `build_ask_user_dialog`
(`host/web/dialogs.py`, below), rather than the pre-V12 design of rendering the
question into the transcript and reading the reply off `session.messages`, the
same queue `_send()` (`host/web/main.py`) already posts a `"user"` transcript turn
into; the old path posted the reply twice (once via the queue, once explicitly) —
`on_ask_user_needed` no longer touches `session.messages` at all, fixing it.
`start(instructions: str | None = None)` launches
`run(instructions)` as a detached `asyncio.Task` owned by the `RunSession` (not by
any one NiceGUI client); `run()` is a near-verbatim port of
`OrganizerScreen._agent_worker` onto a plain `asyncio.Task` — loads settings, opens
`mcp_session`, calls `run_agent_loop`, then loops on `session.messages.get()` for
O7-style follow-up continuations, threading one `_TokenLedger` across all of them.
`instructions` (S5) is the starter pane's optional steering text; it is passed
only to the first `run_agent_loop` call, never to a continuation, matching
`host/app.py`'s `_agent_worker(instructions=...)` contract.

As of T5/T6, `on_event`'s `tool_call`/`tool_result` handling no longer appends a
chat turn — that fixed the "telcontar talking to itself in bubbles" issue T5 was
written to address. `tool_call` narrates via `session.narrator.narrate(tool)` into
`session.activity` (the log zone's "current activity" line) — and, as of V16,
also into `session.activity_log` via `session.add_activity(phrase)`, appended
right alongside the `activity` assignment and only when the Narrator actually
returns a new phrase (a repeated phrase collapses to nothing before either is
touched) — and opens a step —
`session.open_step(tool, event.text, args)` — reading `tool`/`args` off
`event.data`; `tool_result` closes it — `session.close_step(result, ok=ok)` —
inferring `ok` from whether `event.data`'s `"result"` value is a dict containing
an `"error"` key. This relies on `host/agent.py`'s 5
`AgentEvent("tool_call"/"tool_result", ...)` emission sites (the pre-pass
analyzer's `_fetch_batch_content`, `_analyze_new_documents`'s
`record_document_batch` call, and the ORGANIZE/QUERY dispatch loops in
`run_agent_loop`/`run_query_loop`) now carrying structured `data`:
`{"tool": name, "args": args}` for `tool_call` (previously just the tool name,
no args) and `{"result": result}` for `tool_result` (previously no data at all).
Purely additive — no `run_agent_loop`/`run_query_loop` signature change, since
adding a kwarg there breaks explicit-signature `fake_run_agent` test doubles.

As of U4, `on_event`'s `tool_result` case also calls `session.bump_fs_revision()`
whenever `close_step` returns a closed step (see `session.py` above) whose `.tool`
is in the module-level `_TREE_MUTATING_TOOLS = frozenset({"execute_plan",
"write_index", "write_summary", "write_folder_readme"})` and the result was ok —
these are the only tools that change what's on disk under the target directory.
This is what drives `run_page`'s sidebar-tree refresh.

**`host/web/bridge.py` also exports `QueryBridge(session)`** (U7) — the same shape
(`on_event`/`start`/`run`) as `AgentBridge`, but driving `host.agent.run_query_loop`
instead of `run_agent_loop`. It has no `on_approval_needed`/`on_cost_approval_needed`/
`on_ask_user_needed` methods at all — their absence is itself the safety property,
since query mode is read-only by construction (`host.agent.QUERY_ALLOWED_TOOLS`).
`on_event` handles `thinking`/`tool_call`/`tool_result`/`tokens`/`warning`/`error`
the same way `AgentBridge` does (narration is skipped — `tool_call` just opens a
step, no `Narrator` call), but deliberately has no `"done"` case: `run_query_loop`
both emits a `"done"` event and returns the answer text, and the TUI's own
`QueryScreen.on_event` renders only from the return value — `QueryBridge.run()`
mirrors that, calling `session.add_turn` with the returned answer after each
`run_query_loop` call rather than reacting to the event (handling both would
render every answer twice). `start()` kicks the query worker off immediately (TUI
parity: `QueryScreen` starts its worker in `on_mount`, no explicit "start"
button). `run()` is a near-verbatim port of `QueryScreen._query_worker`: one MCP
session and one `_TokenLedger` for the whole chat, threading `history` across
questions for multi-turn context. `done`/`error` here are per-question, not
per-session: `QueryBridge` never sets `session.done`.

**`host/web/dialogs.py`** (U4, extended by U5/U6/V12) — one builder per `PendingRequest` kind, replacing
the dialog-building code that used to live inline in `run_page`'s
`_show_pending_dialog` closure. `build_approval_dialog(session, pending) ->
ui.dialog` is a faithful port of the TUI's `ApprovalModal`: title (plan id +
op count), the rationale (if any) with its "model-generated — not verified fact"
disclaimer, then a folder-notes disclaimer when folder notes are present. As of
V3, what follows is an actual before/after file tree instead of the old flat
`render_target_layout` text block plus a separate flat checkbox list (`render_target_layout`
itself was unchanged by V3 — the now-deleted Textual `ApprovalModal` used to call
it directly for its own preview; only this dialog's use of it was replaced).
`host.format.plan_tree_diff(ops, folder_notes, target) -> (before_lines,
after_lines, other_ops)` is a pure function (no filesystem I/O — the plan's ops
ARE the diff) that derives the tree from each op's `dst` per its type
(documented in the function's own docstring): `rename` -> new filename in the
same parent; `move` -> basename under the destination directory;
`quarantine`/`archive_document` with a computed destination -> that
destination; `create_file` -> its own `src` (nothing to show as "before"). As
of X4, it first calls the private `_chain_ops(ops, target) -> (chains,
other_ops)` helper, which walks the ops in plan order and chains a same-file
rename/move/quarantine/archive_document sequence (e.g. a rename immediately
followed by a move of the renamed file) into one `_FileChain` — resolving each
op's `src` through the chain first (mirroring `execute_plan`'s own `moved`
dict resolution, `server/tools.py`) before computing its destination — so the
file appears once in the before/after tree with its true start/end state,
instead of once per op with a wrong intermediate one. No op is ever dropped:
every op_id ends up in exactly one chain's `op_ids` or in `other_ops`.
Each side is a `list[PlanTreeLine]` (`depth`/`label`/`op_id: str | None`/
`op_ids: tuple[str, ...]`, a frozen dataclass) built by the private
`_render_file_tree` helper — files listed before subfolders at each level,
`depth` driving visual indent, not ASCII connectors baked into the label text,
since the dialog renders one label per line rather than raw preformatted text
and `host/format.py` stays free of any connector-rendering state. As of X7,
each row is `ui.row().classes("items-center gap-1 no-wrap tc-tree-row")`
wrapping `depth` calls to the new module-level `_render_tree_guides(depth)`
helper — `depth` bordered `.tc-tree-guide` spacer divs (`ui.element("div")`),
one per level, styled by `theme.css()`'s matching rules (below) — followed by
the (optional checkbox +) label, replacing the previous flat `margin-left:
{depth * 16}px` indent with real CSS vertical guide lines, visually consistent
with the sidebar tree's own now-enabled connectors (`host/web/shell.py`,
below). `op_ids` (X4) carries every op_id chained onto that line's
file — a single-element tuple for an unchained line; `op_id` stays `op_ids[0]`
for backward compatibility with any reader that only cares about the primary
id. Both are set on every leaf file line in both trees, but the dialog only
ever builds checkboxes from the after-tree's copy — the before tree is a
read-only reference. As of X4/X6, a chained after-tree line gets exactly ONE
checkbox for its whole chain — `.mark()`ed under every id in `line.op_ids` and
recorded in the dialog's `checkboxes` dict under each of those ids too — so
unchecking it excludes the whole chain from execution together
(`removed_op_ids`) instead of leaving the file in a half-applied state (e.g.
renamed but not yet moved). As of X6, every checkbox also carries a
`.tooltip(...)`: an unchained line gets "Uncheck to skip this change" (the
same text the "Other operations" checkboxes below also carry, as of this same
X6 change) while a chained
line gets "Uncheck to skip all of this file's chained changes" instead — and
both the after-tree column and the "Other operations" list each get their own
visible caption above the checkboxes, `ui.label("Unchecking a box excludes
that change from execution.").mark("after-checkbox-hint")` (two separate
instances, one per section, sharing the same marker), since X4's chaining
made a bare checkbox with no label text easy to miss the meaning of. `plan_tree_diff` also computes a per-op_id
`suffixes` dict, applied to both tree sides, so a tree-rendered op keeps the
same advisories a flat `fmt_op` row would have shown: the `(outside target)`
marker (`is_op_out_of_scope`, S4/M4) for any op whose source resolves outside
`target`, and — for `quarantine` specifically — its V10 stated reason
(`quarantine_reason`), falling back to "no reason given" — for a chain, the
`outside target` flag is OR'd across every chained op and the first
`quarantine`/`archive_document` reason encountered wins. Ops with no clean
tree slot (`create_dir`, `compress_quarantine`, `update_file`, and any
`quarantine`/`archive_document` with no destination) come back as `other_ops`
instead, rendered below the tree as an "Other operations" list using the same
`fmt_op(op, session.target, markup=False)` label the whole checklist used
pre-V3 — so the `(overwrite)` marker (`update_file`-only, and `update_file`
always lands in `other_ops`) is the one marker still exclusive to that
fallback bucket. Every op in the plan still ends up with exactly one checkbox,
in the after-tree (shared across its whole chain, if any) or in "Other
operations" — never both, never neither — preserving the `removed_op_ids`
safety contract. The card is now sized via an
inline style
(`width: 90vw; max-width: 1400px`) rather than a Tailwind `max-w-3xl` class —
Quasar's own dialog CSS outranks a same-specificity Tailwind swap. Approve
resolves with `ApprovalResult(approved=True,
removed_op_ids=[...])` for every unchecked op; Refine resolves with
`ApprovalResult(approved=False, refinement=text)` only if the field is non-blank
(otherwise a no-op, dialog stays open) — refinement therefore always takes
priority over approval, since Refine and Approve can never both fire from the same
click; Reject resolves with `ApprovalResult(approved=False)`. As of X5, the
dialog's `ops_json_path` caption (the path `_handle_execute_plan`,
`host/agent.py`, wrote via `_write_ops_json`) also gets a "Reveal in file
explorer" button (`.mark("reveal-ops-json")`, only rendered when
`ops_json_path` is non-empty) — resolves the path against `session.target`
with the same `.resolve().relative_to(...)` confinement check
`host/paths.py`'s `is_op_out_of_scope` uses elsewhere, and on success calls
`host.paths.reveal_in_file_manager(Path(ops_json_path))`; imports the `paths`
module itself (`from host import paths as host_paths`), not the function
directly, so tests can monkeypatch it. `reveal_in_file_manager(path: Path) ->
bool` (X5, `host/paths.py`) is a fire-and-forget `subprocess.Popen` dispatch —
Explorer's `/select,<path>` flag on Windows, `open -R <path>` on macOS, and (no
portable "select a specific file" verb existing on Linux) `xdg-open
<path.parent>` elsewhere — that never waits on or checks the child process's
exit code (`explorer.exe` exits 1 even on success) and returns `False`, never
raises, if `Popen` itself fails to spawn. `build_cost_dialog(session,
pending) -> ui.dialog` (U4 placeholder, made a faithful port of the TUI's
`CostEstimateModal` in U5) shows the title "Analyze this corpus?", a summary
line composed from the engine-side `pending.payload["data"]` dict
(`new`/`already_analyzed`/`estimated_tokens`/`batch_size` — `data` is the
source of truth, matching the approval dialog's `plan_data`-driven approach;
`pending.payload["summary"]` is used only as a fallback when `data` is empty,
a caller-convenience case exercised by one existing test), the same
"rough estimate from file sizes" disclaimer as the TUI, and Proceed/Cancel
buttons. `build_ask_user_dialog(session, pending) -> ui.dialog` (V12) replaces the
old design — the question rendered as a plain transcript turn, answered by typing
a normal chat message — with a structured modal: one radio-button group per
question from `pending.payload["questions"]` (only when the agent supplied
`options`), plus one free-text "Additional comment" input, and Submit/"Skip — you
decide" buttons. Submit composes the reply in code, never a second LLM
round-trip — `"<question text> → <selected option>"` per answered question, plus
an `"Additional comment: <text>"` line when non-blank — and resolves
`AskUserResult(reply=reply, provided=bool(reply))`; Skip resolves
`AskUserResult(reply="", provided=False)`, the same "proceed with your own best
judgement" signal a degenerate/no-callback case already produced. All three
dialogs are
`.props("persistent")` (no backdrop-click or Esc dismissal) and resolve through a
shared `_make_resolver(session, pending, dialog)` helper (V12) —
`session.resolve_pending(result, request_id=pending.request_id)` then
`dialog.close()` — factored out of `build_approval_dialog`/`build_cost_dialog`'s
previously separately hand-rolled copies of the same shape (pure refactor, no
behaviour change to either) and reused by `build_ask_user_dialog` too. This fixes a
live bug where the previous plain `ui.dialog()` could be dismissed without
resolving its future, permanently deadlocking the run with no visible symptom (the
same failure class as the reload-orphaning issue ROADMAP.md's "Break 1" spike
found, closed here for the dialog-dismissal path instead). V12 applies the same
treatment to `ask_user`'s new dialog too.

`build_journal_dialog(session) -> ui.dialog` (U6) is the toolbar-triggered
journal viewer — the web UI's counterpart to the TUI's `JournalScreen`. Unlike
the approval/cost/ask dialogs above, it isn't resolving a `PendingRequest` (nothing
is waiting on a future), so it's a plain, dismissible `ui.dialog()` — Esc/
backdrop-close just closes the viewer. Lists entries via
`host.web.journal.load_entries` rendered through `host.format.fmt_journal_entry`
inside a `@ui.refreshable body()`, with an "Undo last operation" button gated
behind a separate sibling confirm dialog (built once up front, not nested
inside `body`'s refreshable, so its buttons bind once rather than re-binding on
every `body.refresh()`); confirming calls `host.web.journal.do_undo`, refreshes
`body`, and — on success — calls `session.bump_fs_revision()` so the sidebar
tree and the toolbar's own journal count pick up the change. While
`session.has_open_step()` is true, the Undo button is replaced with an
explanatory label — undo is blocked while a tree-mutating step is in flight,
since `server.journal.pop_last` rewrites the whole journal file while the MCP
server subprocess may be appending to it. `load_entries`/`do_undo` are called
**synchronously**, not via `run.io_bound` — a deliberate deviation from every
other blocking-I/O call site in `host/web/`; see `host/web/journal.py` below
for why.

**`host/web/journal.py`** (U6) — journal load/undo logic, `nicegui`-free,
mirroring how `host/web/tree.py` relates to `host/web/shell.py`: this module
owns the filesystem/MCP-adjacent logic, `host/web/dialogs.py` owns the
rendering. `load_entries(target) -> list[dict]` wraps `server.journal.all_entries`
via `host.paths.resolve_journal_path`, wrapped in a defensive `try`/`except`
that returns `[]` on any error — mirrors the deleted `host.app.JournalScreen`'s
defensive handling, so a broken `Settings()`/config never blanks the view.
`do_undo(target) -> dict` wraps `server.tools.undo_last` via
`host.paths.resolve_journal_path`/`resolve_plans_dir`, returning its raw result
dict verbatim. Both `server.journal`/`server.tools` imports are late (inside
the functions), matching the same discipline the deleted Textual TUI used for
these same imports — avoids dragging their heavier dependency chains in at
module import time. Undo stays user-only and out of MCP by design: this module
calls `server.tools.undo_last` directly (a local function call, same machine),
the same way the deleted `host.app.JournalScreen` did; there is no
agent-reachable path to it.

**`host/web/steplog.py`** (U4) — the internal-step log-strip rendering lifted out
of `run_page`'s closure, originally intended so later screens (a journal view,
a query view) could reuse the same "one compact line per step, toggle opens
full detail in the shell's drawer" idiom instead of re-deriving it. In the
event, U6's journal dialog (`host/web/dialogs.py`'s `build_journal_dialog`)
didn't need it — journal entries render as plain formatted lines
(`host.format.fmt_journal_entry`), not as steps — so this module's reuse case
is still open, pending U7's query view. Still imports `nicegui` (renders
`ui.row`/`ui.label`/`ui.button`), unlike `session.py`/`bridge.py`/`tree.py` —
only `fmt_step_line(step) -> str` (`f"{glyph} {step.summary}"`, `_STEP_GLYPHS` — ▶
running / · ok / ✗ error) has no framework dependency. `StepLogState` is the
per-client render cursor (`step_seq`, `step_rows: dict[int, tuple[ui.row,
ui.label]]` keyed by step seq) — the same shape `_RenderState.step_rows` used to
carry inline in `main.py`. `render_step_row(log_column, shell, step)` renders one
row with its "code"-icon detail button (`shell.show_detail(...)`, `.mark(
f"step-detail-{step.seq}")` as of V13b, for per-row testability); `prune_log(state)`
caps the DOM at `_MAX_LOG_ROWS = 500`, deleting the oldest row first;
`sync_steps(log_column, shell, state, steps)` renders any step newer than
`state.step_seq` and refreshes the text of already-rendered rows whose
status/summary changed (a "running" step is updated in place once it closes) —
`run_page`'s `_refresh()` now just calls this once per tick.

**`host/web/shell.py`** (Phase 19 T2, extended by T3, T6, Phase 20 U3, Phase 21 V7/V13b,
Phase 23 X11) —
`app_shell(*, target: Path | None = None, on_select: Callable[[Path], None] | None
= None, session: RunSession | None = None, active: str | None = None, nav: bool =
True) -> Iterator[Shell]`, a `@contextmanager` mounted by every `@ui.page` route in
`host/web/main.py`, including the early-return branches (not-configured,
run-not-found), so the sidebar is visible on every screen instead of being
assembled per-page. As of U3, the drawer always renders an unconditional
"Settings" button (`.mark("btn-sidebar-settings")`) right below the "telcontar"
label, navigating to `/settings` — reachable from every route, mirroring the
TUI's app-level `ctrl+s` `action_open_settings` binding (`host/app.py`). As of V7,
that header row also carries a manual refresh icon button
(`.mark("btn-tree-refresh")`, tooltip "Refresh file tree") beside the "telcontar"
label, calling `shell.reload_tree()` on click. Builds a `ui.left_drawer` as a direct child of the page body —
NiceGUI's `require_top_level_layout` raises `RuntimeError` if the drawer is nested
inside another container — containing a `ui.tree` sourced from
`host.web.tree.build_nodes` (`.props("dense selected-color=primary")`
— the `selected-color` prop added X1, a pure Quasar prop with no new CSS, so a
node highlighted via `shell.tree.select(...)` (`run_page`, below) stands out;
as of X7, `no-connectors` was dropped from that same `.props(...)` string, so
Quasar's own built-in QTree connector lines now render — `.classes("w-full
tc-tree")` (was just `"w-full"`) is the hook `theme.css()`'s new `.tc-tree`
rules (below) use to mute their color and tighten row density — and — as of
V13b —
`max-height: 45vh; overflow-y: auto`, so the tree scrolls internally instead of
pushing what's stacked below it off-screen), plus the page's
main content column. As of V13b, the internal-step detail zone (T6) is stacked
directly below the tree inside this *same* left drawer — `ui.column().mark(
"detail-section")`, hidden by default (`.visible = False`) — rather than the
separate `ui.right_drawer` T6 originally created; it holds a title label
(`.mark("detail-title")`), a close button (`.mark("btn-detail-close")`, calls
`shell.hide_detail()`), and the detail body itself: `ui.codemirror("",
language="JSON", theme=theme.CODEMIRROR_THEME).disable().mark("detail-content")`.
`theme.CODEMIRROR_THEME` (V13b, `host/web/theme.py`, `= "basicDark"`) is passed
explicitly because `ui.codemirror` otherwise defaults to a light theme
regardless of the app's own dark palette, rendering as a jarring white panel on
the dark shell. `app_shell` yields a
`Shell` dataclass (`drawer`, `tree`, `content`, `detail_section`, `detail_title`,
`detail_content`, `target`,
`selected`, and — V7 — a private `_reloading: bool` re-entrancy flag) — there is
no longer a standalone `detail_drawer` field.
`Shell.show_detail(title: str, detail: str)` (T6, rewritten V13b) now populates
the existing widgets in place — `detail_title.set_text(title)`,
`detail_content.set_value(detail)` — and reveals the section
(`detail_section.visible = True`), instead of clearing and rebuilding a separate
drawer on every call; the codemirror instance itself is created once, at
`app_shell` build time, so only its title/value change per `show_detail` call.
The new `Shell.hide_detail()` (V13b) sets `detail_section.visible = False` back,
wired to the close button above. Never
`ui.code`/`ui.markdown`, since both render through a markdown fenced-code path and
step detail can carry untrusted document content that must never be interpreted
as markup; `ui.codemirror` takes the content as a plain value/prop instead, with
`.disable()` making it read-only display, not an editor. `host/web/main.py`'s
`run_page` is the only caller, reached via a per-step "code" icon button in the
log zone. The tree's `on_select` handler ignores placeholder-node clicks and
otherwise sets
`shell.selected` and calls the optional `on_select` callback; its `on_expand`
handler is async — the first time a real directory node is expanded it calls
`host.web.tree.load_children` via `run.io_bound`, splices the result into
`tree.props["nodes"]` in place (found via `host.web.tree.find_node`), and calls
`tree.update()`. As of V7, it re-locates the node a second time — in whatever
`tree.props["nodes"]` actually is *after* the await — before splicing children
into it, rather than mutating the pre-await reference directly: a concurrent
`reload_tree()` poll can replace the whole node list while `load_children` is
still running, detaching the earlier reference from what's actually live and
silently dropping the expand. This was a latent bug in the pre-V7 code (there
was only ever one writer of `nodes` before, so it never manifested) fixed as
part of introducing the second writer. `Shell.refresh_tree(root)` (T3) re-roots the tree at `root` and
updates `Shell.target` to match — used by the picker's "go up one level" button and
a Windows-only drive-root `ui.select` dropdown, both rendered only when `on_select`
is passed in (the picker route, `/`; hidden on `/run/{run_id}`, where the tree is
for verification only).

**`Shell.reload_tree()`** (V7) is now the one writer for `tree.props["nodes"]`:
rebuilds the tree from disk via `host.web.tree.rebuild_nodes` (off the event loop,
`run.io_bound`), preserving whatever the user had expanded (the same `expanded`
Quasar-prop read the old inline U4 refresh logic used), and only actually
replaces the prop — and calls `tree.update()` — when the rebuilt node list
differs from what's already there: a QTree `nodes` replacement re-renders the
whole subtree and can reset scroll/selection, so a genuine no-op poll must not
touch the prop at all, not just be fast about it. Guarded by `self._reloading`:
the page's own `REFRESH_INTERVAL` render-poll timer (`host/web/main.py`) and this
method's new periodic poll (below) are two *different* `ui.timer`s, so nothing
else stops them from calling in concurrently. A no-op when `self.target is None`
(the picker/setup/settings routes have no target directory to rebuild from).
Three call sites: the new manual refresh button above; a new periodic
`ui.timer(web_session.TREE_POLL_INTERVAL, shell.reload_tree)`, created only when
`app_shell()` is given a real `target` (never on the picker/setup/settings
routes); and `host/web/main.py`'s existing post-`execute_plan` `fs_revision`
refresh (U4, below), which now just awaits this method instead of inlining the
rebuild itself — `main.py` no longer imports `host.web.tree` directly as a
result. `web_session.TREE_POLL_INTERVAL` (V7, default `5.0`s) is a module-level
test-seam constant in `host/web/session.py`, the same pattern `REFRESH_INTERVAL`
already established — deliberately coarser than `REFRESH_INTERVAL`'s 0.5s, since
`rebuild_nodes` recurses over every expanded directory and a live tree poll is a
nice-to-have, not something needing sub-second latency.

`app_shell`'s signature stayed frozen through Phase 20/21 (U1-U7, V7 all mounted
through it unchanged) until Phase 23's X11 extended it with `session`/`active`/
`nav` for the persistent nav header below. `_apply_theme()` is a deliberately empty
hook for T7/T8's future `host/web/theme.py`. `host/web/shell.py` now shares
nicegui-importing duties with `host/web/main.py`.

**Persistent nav header (X11, logo added X13):** when `nav=True` (the default), `app_shell` opens
with a `ui.header()` — as of X13, `theme.LOGO_SVG` rendered via
`ui.html(...).classes("tc-logo")` beside the "telcontar" label (both inside a
`ui.row()`, so the mark stays vertically centered against the wordmark) — plus
a `ui.tabs(value=active)`
(`.props("dense")`) holding one `ui.tab` per entry in the module-level
`_NAV_TABS = ("conversation", "corpus", "query", "settings")`, each `.mark()`ed
`nav-conversation`/`nav-corpus`/`nav-query`/`nav-settings` — mounted before the
left-drawer sidebar, so every route gets the same top-level tab strip. `/setup`
is the one route that passes `nav=False`, hiding the header entirely, since the
first-run wizard has nowhere valid to navigate to yet. `session` is the
*organize*-mode `RunSession` driving the current route — `/run/{run_id}` and
`/corpus/{run_id}` pass their own; a query-mode session is deliberately never
passed here, since a query session must never become the nav shell's "active"
pointer (reserved for organize-mode runs). Passing `session` both updates the
module-level active-run pointer (`web_session.set_active(session.run_id)`, see
`session.py` above) and is used, alongside `effective_session = session or
web_session.get_active()`, to decide what the Conversation/Corpus tabs navigate
to — `effective_session` is what lets a route with no session of its own in scope
(`/settings`) still resolve a working target for those tabs instead of just
disabling them. The Conversation/Corpus tabs are `.disable()`d when
`effective_session is None`; the Query tab is disabled when there's no
`effective_target` (`target` or `effective_session.target`) or
`host.paths.find_organizer_root(effective_target)` finds no analyzed corpus above
it. `active` names which tab (if any) matches the current route, seeding
`ui.tabs(value=active)`'s initial selection. `tabs.on_value_change(_on_nav_change)`
no-ops when the new value already equals `active` (guards against the initial
`ui.tabs(value=active)` construction itself firing a spurious navigation) and
otherwise calls `ui.navigate.to(...)`: Conversation/Corpus go to
`/run/{effective_session.run_id}` / `/corpus/{effective_session.run_id}`; Query
reuses `web_session.find_by_target(effective_target, mode="query")` if one
already exists for that target, else creates one, before navigating to
`/query/{run_id}`; Settings always navigates to `/settings`. This is additive to
the left-drawer's own unconditional Settings button, above — both now route to
`/settings`, and neither replaced the other.

The drawer's width (T4) comes from `web_session.get_sidebar_width()` and is applied
via the Quasar `width` prop (`drawer.props(f"width={width}")`), never raw CSS,
because Quasar also offsets `.q-page-container` from that same prop — a CSS-only
width would leave the page content overlapped. A 6px `div.tc-sidebar-resize` handle
on the drawer's right edge is wired, once per page build (guarded by a
`window.__tcSidebarResizeWired` flag so re-running the snippet is a no-op), by a
small injected JS snippet (`_RESIZE_JS`, run via `ui.run_javascript`) that tracks
pointerdown/pointermove/pointerup on `document` rather than just the handle (so the
pointer can leave the 6px strip mid-drag) and live-resizes the drawer's CSS width
for visual feedback, clamped during the drag itself between `SIDEBAR_WIDTH_MIN`/
`_MAX` (`Math.max(__MIN__, Math.min(__MAX__, ...))` in the JS). As of V13b, those
two bounds are interpolated into `_RESIZE_JS` once, at module-import time, via
`.replace("__MIN__", str(web_session.SIDEBAR_WIDTH_MIN)).replace("__MAX__",
str(web_session.SIDEBAR_WIDTH_MAX))` — previously hardcoded JS literals (`240`/
`720`) that had already silently drifted out of sync the moment `SIDEBAR_WIDTH_MAX`
was raised to `1000` in this same change; interpolating instead of duplicating
means the two can't drift apart again. Only on pointerup does it emit a custom `tc_sidebar_resized`
event (via NiceGUI's `emitEvent`/`ui.on` bus) carrying the final pixel width; the
Python-side `_handle_resize` (registered with `ui.on("tc_sidebar_resized", ...,
throttle=0.05)`) is the only point that actually persists the preference, via
`web_session.set_sidebar_width()`, and re-applies the real Quasar `width` prop. The
drag itself is DOM-only and writes nothing to `session.py` until pointerup. As of
V15, `_RESIZE_JS` must be a self-invoking IIFE, not a bare arrow-function
expression: `run_javascript` evaluates the string via `eval`, which constructs but
never calls a bare function literal, so the handle's listeners were never actually
bound in any browser (not an Edge-specific regression, as first suspected) until
this fix. As of X2, the pointerdown handler also calls
`e.target.setPointerCapture(e.pointerId)`, so the drag keeps targeting the handle
element even once the cursor leaves the browser window, and the pointerup
handler's cleanup logic (clearing `cursor`/`userSelect`, emitting
`tc_sidebar_resized`) is factored into a shared `endDrag()` closure now bound to
FOUR events instead of just `pointerup`: `pointerup`, `pointercancel`,
`lostpointercapture`, and `window`'s `blur` (covers alt-tabbing away mid-drag).
This fixes a real bug — a drag released outside the window previously never
delivered `pointerup` to `document`, leaving `document.body.style.userSelect`
stuck at `'none'` (all text on the page unselectable) until the page reloaded.
It was the originally-suspected cause of the ROADMAP's "chat text not
selectable" report, but turned out to be secondary — see `run_web()`'s
`_native_window_args` below for the actual primary cause.

**`host/web/tree.py`** (Phase 19 T2, fleshed out by T3) — NiceGUI-free, mirroring
`session.py`/`bridge.py`'s invariant so it stays testable in plain pytest.
`build_nodes(root: Path) -> list[dict]` builds the top-level node list `ui.tree`
expects (`{"id": <absolute path str>, "label": <basename>, "children": [...]}`, id
always an absolute path string so it's a stable key across a page reload); the
root's own immediate children are loaded eagerly (one directory listing) so the
sidebar shows useful content on first render, while deeper levels stay lazy behind
a placeholder-child scheme — a not-yet-expanded directory gets one placeholder
child whose id ends in `PLACEHOLDER_SUFFIX` (a null byte + ellipsis, never a real
path), so `ui.tree` shows an expand arrow without this module walking into it.
`load_children(path) -> list[dict]` lists one directory's immediate entries —
files and folders both shown, since the sidebar's job includes letting the user
verify files actually moved/renamed, not just picking folders — sorted folders
before files, then alphabetically. It hides dotfiles (`.organizer`, `.git`, ...)
but deliberately *not* `_quarantine` (the only removal path — the user must be
able to see what landed there), never follows symlinks/junctions (Windows profile
directories like "Application Data" can loop back on themselves), and never
raises: a permission-denied or vanished directory yields an empty list rather than
blanking the whole tree. `find_node(nodes, id)` depth-first searches the nested
node list for a placeholder's real parent; `needs_loading(node)` reports whether a
node still carries the placeholder rather than real children — both support
`shell.py`'s expand handler. `rebuild_nodes(root, expanded_ids: set[str]) ->
list[dict]` (U4) is a non-destructive alternative to `build_nodes`: it rebuilds
the whole node list the same way, but for every directory id in `expanded_ids` it
eagerly loads real children — recursively, via the private `_rebuild_children`
helper — instead of leaving the lazy-load placeholder, so refreshing the sidebar
after a tree-mutating tool call doesn't collapse whatever the user had expanded. A
directory that no longer exists (renamed/moved away by the very op that triggered
the refresh) is silently dropped, the same tolerance `load_children` already has.
`list_drive_roots() -> list[Path]` wraps
`os.listdrives()` (Python 3.12+, Windows-only) so the picker can reach outside the
home directory, returning an empty list (never raising) on any other platform,
Python version, or enumeration error.

**`host/web/theme.py`** (T7, extended by T8/V13c, favicon/logo redesign X13) — product-identity helpers,
`nicegui`-free like `session.py`/`bridge.py`/`tree.py`. `window_title(target: Path
| None = None) -> str` returns `"telcontar"` with no target, or `f"telcontar —
{target.name}"` once one is selected, falling back to the full path string when
`.name` is empty (a Windows drive root, e.g. `Path("C:\\")`, so the title never
ends in a dangling "— "). T8 adds telcontar's visual identity — a
Númenórean/human-king (Aragorn's Quenya name) motif, gold and silver on a dark
base:

- `PALETTE: dict[str, str]` — exactly the 9 keyword names `nicegui.app.colors()`
  accepts (`primary`/`secondary`/`accent`/`dark`/`dark_page`/`positive`/
  `negative`/`info`/`warning`). Gold `primary` (`#C8A951`), mithril-silver
  `secondary` (`#AEB6C4`), `dark_page`/`dark` as the page background and
  elevated-surface dark tones. `positive`/`negative` stay in their own
  desaturated green/red hue families, deliberately never re-hued gold/silver —
  the approval dialog's Approve/Reject buttons are the highest-trust screen in
  the product and must stay unmistakable.
- `FAVICON_SVG` — an inline SVG string passed straight to `ui.run(favicon=...)`,
  which NiceGUI inlines as a data URL — no file, no network request. As of X13,
  a White Tree of Gondor mark (branching silver `#AEB6C4` trunk/roots, a small
  3-star gold `#C8A951` arc, on the same dark rounded-rect background) —
  replacing T8's original Elendil's-seven-pointed-star design.
- `LOGO_SVG` (X13) — the same White Tree motif at a larger `viewBox`, with more
  branch/root detail and the full 7-star gold arc, and no background `<rect>`
  (transparent, since it sits directly on the nav header's own dark surface
  rather than standing alone like the favicon). Rendered via `ui.html(...)` in
  `host/web/shell.py`'s header, next to the "telcontar" wordmark — legal there
  only because this is an in-repo constant, never registry/document content;
  see that module below.
- `font_face_css(font_dir: Path | None = None) -> str` — emits an `@font-face`
  rule for the vendored Cinzel woff2 (`host/web/assets/fonts/`) only if the file
  actually exists on disk; returns `""` otherwise, so a missing font is silently
  a plainer heading, never a 404.
- `css(font_dir: Path | None = None) -> str` — the one small CSS layer: binds the
  display typeface (Cinzel, falling back to "Trajan Pro" / "Palatino Linotype" /
  "Book Antiqua" / Georgia / serif — always present regardless of whether the
  font file exists) directly onto Quasar's own `.text-h1`...`.text-h6` heading
  classes, so every existing heading picks it up with no per-component class
  sprinkling, plus — as of V13c — a new `.tc-display` utility class (applied
  explicitly via `.classes(...)` at two call sites, the sidebar's brand label
  and the approval dialog's title) and Quasar's own `.q-message-name` (chat
  sender-name) slot, which picks up the face the same no-code-changes way the
  heading classes do, plus a mandatory contrast fix (`.q-btn.bg-primary { color:
  #0E1116 !important; }` — Quasar renders a filled `color="primary"` button with
  white label text by default, and white-on-gold is ~2.2:1 contrast, unreadable),
  plus — as of X13 — a `.tc-logo` rule (`display: inline-flex; align-items:
  center`) that vertically centers `LOGO_SVG` against the wordmark label beside
  it in the nav header, plus — as of X2 — a
  `.q-message-text, .q-message-text-content { user-select: text; -webkit-user-select: text; }`
  rule: defense in
  depth (layer 3, alongside the native-window `text_select=True` fix in
  `host/web/main.py`'s `run_web` and the `_RESIZE_JS` drag-cleanup fix in
  `host/web/shell.py`, both above) so chat message content stays selectable
  even if either of those regresses — a rule on the descendant beats an
  inherited `user-select: none` with no `!important` needed, plus — as of X7 —
  four tree-connector/density rules shared by both tree views in the product:
  `.tc-tree .q-tree__node:after, .tc-tree .q-tree__node-header:before {
  border-color: rgba(174, 182, 196, 0.35); }` mutes Quasar's default mid-grey
  QTree connector lines (rendered again now that `host/web/shell.py`'s sidebar
  tree dropped `no-connectors`, above) to a muted silver matching
  `PALETTE["secondary"]`; `.tc-tree .q-tree__node { padding-bottom: 0; }` and
  `.tc-tree .q-tree__node-header { min-height: 0; padding: 2px 4px; }` tighten
  that same tree's row density; `.tc-tree-guide { width: 16px; flex-shrink: 0;
  align-self: stretch; border-left: 1px solid rgba(174, 182, 196, 0.35); }`
  and `.tc-tree-row { padding: 1px 0; }` are the plan-approval dialog's
  before/after tree's CSS-only guide lines — that tree is hand-rolled indented
  rows, not a real `ui.tree`, so it has no built-in QTree connectors to
  re-enable; see `host/web/dialogs.py`'s `_render_tree_guides`, above.
- `FONT_DIR`, `FONT_URL_PATH` (`/tc-fonts`) — the static-assets directory and its
  `app.add_static_files` mount point, both consumed by `run_web()`.
- `CODEMIRROR_THEME: Final = "basicDark"` (V13b) — `ui.codemirror` defaults to a
  light theme regardless of `PALETTE`/`dark=True`; every read-only `ui.codemirror`
  in the app (`Shell.show_detail`'s step-detail body, and `host/web/settings.py`'s
  three prompt-inspection panels) now passes this explicitly instead of falling
  back to the mismatched light default.

**`host/web/forms.py`** (U2, extended by U3) — shared NiceGUI form fragments for
the setup wizard and the settings view; unlike `session.py`/`bridge.py`/`tree.py`/
`theme.py` it does import `nicegui`, since it renders actual UI elements.
`credential_inputs(...) -> CredentialInputs` renders the URL / API-key / model
input triple, with optional per-service hint text above the URL and model fields
(empty hint text renders as nothing rather than an empty caption), each element
`.mark()`ed (`input-url`/`input-key`/`input-model`) for NiceGUI's headless `user`
test fixture. As of U3, it also takes a `key_placeholder` parameter (default
`"Paste your key here"`, the wizard's copy) so `host/web/settings.py` can pass
`"Paste a new key, or leave empty to keep the current one"` instead — the
blank-key-preserves-existing rule spelled out inline in the field itself.
`save_with_plaintext_guard(build_updates, *, plaintext_confirmed,
button_label, recovery_action="go back") -> tuple[bool, str]` calls
`config.settings.save_user_config` via `run.io_bound` (so the file write + OS
keyring round-trip never blocks the event loop) using a *fresh* dict from
`build_updates()` on every call — never a cached one, since `save_user_config` pops
the API key out of its argument dict before raising `PlaintextKeyFallbackNeeded`,
so a caller reusing the same dict object across a retry would silently save without
the key. Returns `(success, warning_text)` — `""` on success, or
`host.configflow.plaintext_warning(...)`'s text on the fallback path; the caller
owns re-rendering and tracking `plaintext_confirmed` for the next call.

**`host/web/wizard.py`** (U2) — the setup wizard itself: `build_setup_wizard(*,
on_finish)`, a 1:1 port of `host/app.py`'s `SetupScreen` — same 5 steps (welcome,
service choice, API details, document profile, done), same validation
order/strings (via `host/configflow.py`), same plaintext-keyring
warn-then-confirm flow (via `forms.save_with_plaintext_guard`). State is a
page-closure `_WizardState` dataclass, never `app.storage` or a URL param — the
API key must never touch either. A `@ui.refreshable` `steps()` function keyed on
`state.step` renders only the active step and rebuilds on transition — real
per-step routing, NiceGUI's natural equivalent of the TUI's
mount-all-five-and-toggle-`.display` approach (`_show_step` in `host/app.py`).
Deliberately lives outside `host/web/main.py` — `main.py`'s `@ui.page` decorators
stay thin shells; the view-building logic lives in per-screen modules like this
one. `host/web/main.py` mounts it at `@ui.page("/setup")`, calling
`build_setup_wizard(on_finish=lambda: ui.navigate.to("/"))`, through
`app_shell(nav=False)` (X11: the wizard has nowhere valid to navigate yet, so the
persistent nav header is hidden here — see `host/web/shell.py` above).

**`host/web/settings.py`** (U3) — the settings view, a NiceGUI port of
`host/app.py`'s `ConfigScreen`: `build_settings_view(*, on_done)` fetches
`configflow.profile_options()` and `config.settings.read_user_config()` via
`run.io_bound` (off the event loop, same S5 discipline as the wizard), then
renders URL / API-key / model (`forms.credential_inputs`, with the
key-preserves-existing placeholder above) plus document-profile and
approval-mode `ui.select`s (`.mark("select-profile")`/`.mark("select-approval")`)
through a single `@ui.refreshable` form — one page, Save/Cancel, unlike the
wizard's multi-step routing, since there's no first-run narrative to walk
through. `_save()` validates via `configflow.validate_credentials(...,
key_required=False)`, builds the update dict via
`configflow.build_settings_updates(url, key, model, profile, approval_mode)` —
which omits `llm_api_key` entirely when `key` is blank, the same
blank-key-preserves-existing rule as `host/app.py`'s `ConfigScreen` — and saves
through the shared `forms.save_with_plaintext_guard(..., button_label="Save",
recovery_action="cancel")`. `host/web/main.py` mounts it at `@ui.page("/settings")`,
calling `build_settings_view(on_done=lambda: ui.navigate.back())` inside
`app_shell(active="settings")`; both the sidebar's Settings button and (X11) the
nav header's Settings tab (`host/web/shell.py`) route here from any screen.

As of V11, `build_settings_view` also awaits `_build_prompt_inspection()` after
rendering the form — a collapsed "What telcontar tells the model" `ui.expansion`
(`.mark("expansion-prompts")`) deliberately built OUTSIDE the `@ui.refreshable
form()` region, since a `refresh()` triggered by save-validation must never
recompose it. `_load_prompt_inspection_data()` (dispatched via `run.io_bound`,
bundling the TOML profile load and `NAMING.md` read into one blocking round trip)
builds a plain `config.settings.Settings()` — not `config.settings.load()`, which
raises without credentials — so the panel renders even before the setup wizard
has run, and calls `host.agent.composed_system_prompts(settings)` for the three
read-only ORGANIZE/QUERY/ANALYZE prompts and `host.agent._resolved_profile_name(settings)`
for the profile-load status shown above them (a load failure is surfaced
explicitly — `_try_load_profile` swallows the same failure and silently falls
back to a generic "default" profile name for the prompts themselves, which a
transparency view must not hide). Each prompt renders via disabled
`ui.codemirror(..., theme=theme.CODEMIRROR_THEME)` — the explicit `theme=` a V13b
addition, since `ui.codemirror` otherwise defaults to a light theme regardless of
the app's own dark palette — never `ui.markdown`/`ui.html`, matching `Shell.show_detail`'s
precedent (T6): a composed prompt can embed profile free-text and `NAMING.md`
content that must never be interpreted as markup. Editing is deliberately out of
scope — an editable prompt sits next to M10's injection-resistance guardrails and
needs its own security pass first — and the panel explicitly notes the two things
it can't show: the corpus digest and the user's pre-analysis steering
instructions, both composed at run time from a live target/registry this
target-free view never has.

**`host/web/query_view.py`** (U7, extended by V13a) — the query page's UI, `nicegui`-importing (like
`dialogs.py`/`steplog.py`/`shell.py`). `build_query_view(shell, session)` renders a
conversation column (`ui.chat_message`, reusing the same idiom `run_page` already
uses for organize turns, including `run_page`'s V13a bubble alignment/colour fix
— duplicated here rather than shared, per this pair's existing precedent) plus a question input/Ask button
(`.mark("query-input")`/`.mark("btn-query-ask")`) that echoes the question as a
`user`-speaker turn (`session.add_turn`) and pushes it onto `session.messages` —
and, below a separator, a step-log strip
(`host.web.steplog.sync_steps`/`StepLogState`, the same T5/T6 idiom `run_page`
established) instead of the TUI `QueryScreen`'s side-by-side dual-`RichLog` split.
Phase 20 is parity with a cleaner surface, not a redesign, and the log strip
already *is* the web UI's "tool timeline". No approval/cost/ask dialog wiring at
all — query mode is read-only by construction (`QUERY_ALLOWED_TOOLS`), so there is
nothing to gate. A `ui.timer` (same `web_session.REFRESH_INTERVAL` cadence as
`run_page`) drives `_refresh()`, which renders new transcript turns, syncs the
step log, and updates the status/token line.

**`host/web/corpus.py`** (V5) — registry load logic, `nicegui`-free, mirroring
`host/web/journal.py`'s contract exactly: this module owns the
filesystem-adjacent logic, `host/web/corpus_view.py` owns the rendering.
`list_documents(target: Path) -> list[dict]` loads the registry via
`server.registry.load(host.paths.resolve_registry_path(target))` and returns
`[rec.to_dict() for rec in registry.records()]`, or `[]` on any error (missing
file, corrupt JSON) — never raises, the same defensive contract as
`journal.load_entries`, so a missing/corrupt `registry.json` never blanks the
whole page, just shows the empty state. `get_document(target: Path, checksum:
str) -> dict | None` returns one record by checksum, or `None` if
missing/unreadable. `server.registry` is called directly, never through MCP —
there is no agent-reachable reason for a read-only *browse* to exist — and its
import is late (inside the functions), matching `journal.py`'s discipline of
not dragging in its dependency chain at module import time.
`registry_mtime(target: Path) -> tuple[float, int] | None` (X10) returns
`(mtime, size)` of `target`'s `registry.json` via a plain `.stat()` call — `None`
on any `OSError` (missing/unreadable), the same defensive contract as
`list_documents`/`get_document`. A cheap pre-check for `corpus_view.py`'s poll:
comparing this tuple against the last-seen one lets a tick skip re-parsing and
re-flattening the whole registry when nothing has actually changed, rather
than paying that cost every `CORPUS_POLL_INTERVAL`.
`find_by_path(target: Path, path: Path) -> dict | None` (X9) matches a record by
its recorded path — exact `os.path.normcase(os.path.normpath(...))` comparison
against each record's `path`, the same idiom `server/registry.py`'s
`_same_path` uses — no basename fallback, deliberately, since a fallback could
surface the wrong document's summary. Built on `list_documents`, so it inherits
its never-raises contract; used by `host/web/main.py`'s doc-preview pane below.

**`host/web/corpus_view.py`** (V5, row-click selection X12, live polling X10) —
the corpus-browser page's UI, `nicegui`-importing.
`_CorpusViewState` now holds all of the view's reloadable state, not just the
selection: `selected_checksum`/`current_filter` (X10) let a reload restore what
the user was looking at instead of resetting it; `records`/`records_by_checksum`/
`all_rows` (X10) are the reloadable data itself, lifted out of local variables so
`_reload` can replace them in place; `reloading`/`last_mtime` (X10) back the same
re-entrancy-guard and skip-unchanged disciplines `Shell.reload_tree`
(`host/web/shell.py`, above) already established for the sidebar tree poll.
`build_corpus_view(session) -> None` loads records via `await
run.io_bound(corpus.list_documents, session.target) or []` — the `or []` (X10)
guards against `run.io_bound`'s own documented contract of returning `None`
(not the callback's result) on cancellation/app-shutdown, the same pattern
already used elsewhere in `host/web/main.py` (`directory_overview`);
`corpus.list_documents` itself already never raises and never returns anything
but a list. As of X10, the page no longer early-returns when the registry has
no records at all: the search input, table, and detail pane are all built
unconditionally, and only an empty-state label's (`.mark("corpus-empty")`)
`.visible` toggles on whether `records` is empty — so a page opened moments into
a fresh Organize run, before any document has been recorded yet, still has a
live table that fills itself in once the poll below picks up the first batch,
instead of being stuck on the empty state until a manual reload. A new refresh
icon button (`.mark("btn-corpus-refresh")`, tooltip "Refresh corpus") sits
beside the "Corpus browser" heading, and a `ui.timer(
web_session.CORPUS_POLL_INTERVAL, _reload)` (X10, `host/web/session.py`'s
`CORPUS_POLL_INTERVAL = 5.0`s test-seam constant, above) polls automatically —
both call the same `_reload()`, below. Otherwise: a search `ui.input`
(`.mark("corpus-search")`), wired via `on_value_change`, filters rows Python-side
against each record's full, untruncated title/type/summary text — not the
truncated preview the table shows — beside a `ui.table` (`.mark("corpus-table")`,
`row_key="checksum"`, `pagination=10`, `.classes("cursor-pointer")` as of X12)
with title/type/date/status/summary/entities columns, all `sortable: True` for
native client-side Quasar sorting (no Python sort logic needed). Row values are
pre-flattened to plain strings by `_to_row`: `ui.table` crashes the browser on
list-valued cells, so `_entities_preview` turns the registry's `entities` list
into a short joined-names string (up to 3 names, `"+N"` for the rest) for the
table row, while the full list stays in the record dict for the detail pane.
The table carries no `selection=` prop and no checkbox column at
all — clicking anywhere on a row (`table.on("rowClick", _on_row_click)`) opens
its detail pane instead. `_on_row_click(e: GenericEventArguments)` defensively
unpacks Quasar's `[evt, row, index]` `rowClick` payload — bails out unless
`e.args` is a list of at least 2 items and `e.args[1]` is a dict with a truthy
`"checksum"` — rather than NiceGUI's typed `TableSelectionEventArguments` (the
prior V5 design, which needed `selection="single"` and its own checkbox
column); it sets `state.selected_checksum` and calls `_show_detail(checksum)`.

`_show_detail(checksum)` looks the record up in `state.records_by_checksum` —
the live, reload-replaceable dict, not a closed-over snapshot. As of X10, a
checksum no longer found there (the document was quarantined/archived since
the last reload) clears `state.selected_checksum` and collapses the pane back
to its placeholder rather than continuing to show stale content, instead of
just returning as before. On a found record it reveals the detail pane
(`.mark("corpus-detail")`) beside the table: title, a type/date/status meta
line, a new **Location** line (X10, `.mark("corpus-detail-location")`)
showing the record's `path` relativized to `session.target`
(`Path(path_str).relative_to(session.target)`, falling back to the raw path
on `ValueError`/`OSError` — a cross-drive or otherwise non-relative path),
full summary, full provenance, and every entity as its own `ui.label` line
(`f"{name} — {role} ({kind})"`, defensively `.get()`-read since older records
may carry incomplete entity dicts). Merges the former V4 (document preview)
concept into this one screen — previously only reachable by asking the agent.

`_apply_filter(value)` (X10: now reads/writes `state.current_filter` and reads
`state.all_rows`/`state.records` instead of closed-over locals) also gained a
skip-if-unchanged guard — `table.rows` is only reassigned when the computed row
list actually differs from what's already there, the same "a `ui.table` prop
replacement re-renders and can reset pagination/sort" discipline
`Shell.reload_tree` uses for the sidebar tree's `nodes` prop.

**`_reload()`** (X10, `async`, wired to both the manual refresh button and the
`ui.timer` poll above) mirrors `Shell.reload_tree`'s two disciplines: a
`state.reloading` re-entrancy guard (so an overlapping manual click and poll
tick can't race), and a cheap `corpus.registry_mtime(session.target)` pre-check
— when it comes back non-`None` and unchanged from `state.last_mtime`, the
reload returns immediately without touching `corpus.list_documents` at all. On
an actual change, it re-fetches via `run.io_bound(corpus.list_documents,
session.target)` (returning early, without updating `state`, if that call
itself comes back `None` — the same `run.io_bound` cancellation contract
`build_corpus_view`'s initial fetch guards with `or []`), rebuilds
`records`/`records_by_checksum`/`all_rows`, toggles `empty_label.visible`,
re-applies the live search filter (`_apply_filter(state.current_filter)`) so
the table reflects both the new data and whatever the user was already
filtering by, and — if a document is currently selected — calls
`_show_detail(state.selected_checksum)` again so the detail pane's
Location/other fields pick up any rename/move the reload just surfaced (or
collapses to the placeholder via the missing-checksum path above, if that
document was quarantined/archived in the meantime).

Every registry value rendered here is LLM-derived output
from attacker-controllable documents: `ui.label`/`ui.table` row values only,
never `ui.markdown`/`ui.html`/`ui.code`, which would interpret it as markup
instead of displaying it as text — the same rule V13b's step-detail view and
V11's prompt inspection already follow for untrusted content; see
`docs/developer/security-model.md`.

**`host/web/docpane.py`** (X9, new file) — the run screen's document-preview
pane, mirroring `corpus_view.py`'s detail pane field-for-field but
deliberately duplicated rather than refactored to share code with it this
sprint. `DocPane` (dataclass) holds the widget handles: `placeholder`,
`content`, `title`, `meta`, `summary`, `provenance`, `entities`.
`build_doc_pane(*, marker_prefix: str = "doc") -> DocPane` builds them, marked
`{prefix}-detail-placeholder`/`-content`/`-title`/`-meta`/`-summary`/
`-provenance`/`-entities` — the parameterized prefix (default `"doc"`) keeps
this non-breaking should a later sprint share it with `corpus_view.py`'s
`corpus-detail-*` markers instead. `DocPane.show(record: dict)` populates
title/meta (`type · date · status`)/summary/provenance/entities from a
registry record dict and reveals `content`, hiding `placeholder`.
`DocPane.show_unanalyzed(path: Path, meta_line: str)` is for a file with no
registry record yet — the filename as title, `meta_line` as meta, "Not
analyzed yet." as summary, no provenance/entities section, since there's
nothing to show and no extraction happens on this path. `DocPane.clear()`
hides `content` and restores `placeholder`. Same untrusted-content rule as
`corpus_view.py` above: every registry value renders via `ui.label` only,
never `ui.markdown`/`ui.html`/`ui.code`. Consumed by `host/web/main.py`'s
`run_page`, below.

**`host/web/main.py`** (extended by T5/T6/T7/T8, U1/U2/U3/U4/U6/U7, V1/V5/V12/V13a) — now shares nicegui-importing duties
with `host/web/shell.py` (T2). Pages are registered at import time (`@ui.page("/")`,
`@ui.page("/run/{run_id}")`, `@ui.page("/query/{run_id}")` (U7), `@ui.page("/corpus/{run_id}")`
(V5)) but nothing binds a port until `run_web(target: Path |
None = None, *, native: bool = True)` (V1 added `native`) is called, so importing the module is side-effect-free. Each
connected browser tab or native-window client polls `RunSession`/`TranscriptItem`/`StepRecord` state with
its own `ui.timer`, rather than the bridge touching NiceGUI elements directly —
this is what lets a page reload re-attach to an in-flight approval/cost/ask dialog
(via `session.pending`) instead of orphaning it. The browser tab/native window title (T7) comes from
`host.web.theme.window_title`: `run_web`'s `ui.run(...)` call passes it (with no
target) as the global default title, and `run_page` calls
`ui.page_title(theme.window_title(session.target))` from inside the page body —
not via `@ui.page(title=...)`, which is bound at decoration/import time and can't
see the per-request session's target — so the run's target directory lands in the
title of the very first HTML response. `index_page` (the picker route) never calls
`ui.page_title()`, since no directory is "selected" until a run exists.

Both page bodies now open with `with app_shell(...) as shell:` (T2), mounting the
persistent sidebar before any page-specific content. As of X11, every route also
passes its own `active=` tab name to `app_shell` (`/run/{run_id}`/`/corpus/{run_id}`
additionally pass `session=session`) so the nav header (`host/web/shell.py`, above)
highlights the current route and can navigate back to the run in progress; `/setup`
alone passes `nav=False`, since the first-run wizard has nowhere valid to navigate
to yet. The landing page (`/`, S5)
checks `config.settings.is_configured()` first: if unconfigured, it navigates to
`/setup` (`host/web/wizard.py`'s `build_setup_wizard`, U2) instead of any picker,
rather than the pre-U2 plain message pointing the user at the Textual TUI.
`/settings` (`host/web/settings.py`'s `build_settings_view`, U3) is registered
the same thin-shell way, reachable from the sidebar on every route. As of U7,
`/query/{run_id}` is registered the same thin-shell way too: it looks up the
query-mode `RunSession`, calls `QueryBridge(session).start()` on first mount if
not already started (TUI parity: `QueryScreen.on_mount` auto-starts its worker
too, no explicit "start" button), and delegates rendering to
`host.web.query_view.build_query_view`. As of V5, `/corpus/{run_id}` is
registered the same thin-shell way too: it looks up the `RunSession` (same
not-found handling as the other two run-scoped routes) and delegates to
`host.web.corpus_view.build_corpus_view(session)` — no bridge, no MCP session,
no agent turn, since it reads the registry directly (`host/web/corpus.py`)
rather than through the model. It reuses the *same* session/run_id the
organize run already created rather than minting a new one, since the corpus
page only ever reads `session.target`. Once configured, folder selection is the
sidebar tree, which now doubles as the directory picker (T3, superseding the
browse-view half of Phase 20's U1): clicking a node sets `shell.selected`
(which may now be a file, since the tree shows files too), and a "Use selected
directory" button starts the run only if `shell.selected.is_dir()`. As of U1, a
second "Query" button sits next to it: it applies the same `is_dir()` check,
then resolves the selection to the nearest `.organizer` ancestor via
`host.paths.find_organizer_root` (TUI parity with `StartupScreen._query` — a
picked folder may be a subfolder of what was actually organized, so the query
session is rooted at the found ancestor, not the raw selection) and creates a
query-mode session there; if no ancestor has a `.organizer`, it reports "No
analyzed corpus found ... Run Organize first." instead of starting one. Both
buttons now show a real message in a shared error label
(`.mark("startup-error")`) instead of silently no-op'ing when nothing valid is
selected. As of X1, `run_page` calls `shell.tree.select(str(session.target))`
right after `app_shell` mounts, highlighting the run's root in the sidebar
tree from first load — the periodic tree poll only ever replaces the `nodes`
prop, never `selected`, so the highlight survives every poll tick — and
renders a plain `ui.label(str(session.target)).mark("run-target-path")`
(with a tooltip for a long path) above the starter/main columns, so the
target directory stays visible in the main content area for the whole run,
not just on the pre-start starter pane. The organizer view (`/run/{run_id}`) now opens on a
**starter pane** shown before the run begins: a directory overview (reusing
`host.paths.directory_overview`, also offloaded via `run.io_bound`) plus an
optional free-text steering-instructions input (mirrors the Textual TUI's
pre-analysis steering box) and a "Start organizing" button. Only clicking that
button constructs the `AgentBridge` and calls `start(instructions=...)` — S4's
version started the run immediately on directory selection. Once started
(`session.started`), the starter pane hides and the main view (status/progress
bar/chat input/approval-cost-ask dialogs, now via `host/web/dialogs.py`,
U4/V12) takes
over. As of V14, the progress bar is a `ui.row()` (`.mark("progress-row")`)
pairing the `ui.linear_progress` with a sibling `ui.label()`
(`.mark("progress-percent")`) showing a rounded integer percent instead of
NiceGUI's raw 0–1 float — the row's own `.visible` is what's toggled on
`session.progress["total"]`, kept generic so a later current-document label
(V8b) can join as a third sibling. As of V8b, that third sibling exists:
`ui.label().mark("progress-current")` shows the in-flight document name(s)
from the O5/V8a `session.progress["current"]` list — the first filename plus
a `" +N"` suffix when more than one file is in flight, or `""` when the list
is empty (between batches, or on the pre-pass snapshot event, which omits
the key entirely). As of U7, the main view also shows a "Query this corpus" button, hidden
until `session.done` — mirroring the TUI's `OrganizerScreen`'s `g` keybinding,
gated the same way — that, as of X11, first reuses an existing query session
for the target via `web_session.find_by_target(session.target, mode="query")`
and only falls back to `web_session.create(...)` if none exists yet, before
navigating to
`/query/{run_id}`. As of V5, a "Browse corpus" button
(`.mark("btn-browse-corpus")`) sits beside it, gated the same `session.done`
way — set both at build time and again on every `_refresh()` tick, the same
two-places-set pattern the query button already needed — navigating to
`/corpus/{session.run_id}`: the same session, not a new one. As of X9, the
main view's conversation area sits in a `ui.row()` at 2/3 width beside a new
1/3-width document-preview pane (`.mark("doc-preview")`, built via
`host/web/docpane.py`'s `build_doc_pane()`, above) — polling `shell.selected`
(the same attribute the sidebar tree click handler, X11, already populates)
rather than being wired through the tree directly: `_refresh()` reacts only
when the selection actually changes since the last tick, offloading the
stat/registry lookup (`_load_preview`, new module-level helper) via
`run.io_bound` and showing either the matching registry record
(`host/web/corpus.py`'s `find_by_path`), a "not analyzed yet" placeholder
with filesystem metadata, or clearing the pane, depending on what's found.
As of T5/T6, that main view splits telcontar's own tool activity out of one
interleaved stream: a
`conversation_column` (turns only, `ui.chat_message`, rendering `session.transcript`)
sits above a separator and a scrolling
`log_column` (~25vh) rendering
`session.steps` as one compact line each — a status glyph (▶ running / · ok /
✗ error) plus the step's summary — with a small "code" icon button per row that
calls `shell.show_detail(step.summary, step.detail)` to open the full payload in
the step-detail section (T6, stacked in the left sidebar below the tree as of
V13b — no longer a right-side drawer). V16 added a third history,
`session.activity_log`, originally rendered (`.mark("activity-column")`) into
its own `activity_column` between the two, replacing the old single-line
`activity_label`. As of X3, that separate column is gone: `session.thread()`
(`session.py`, above) merges `transcript` and `activity_log` chronologically,
and `run_page` renders each item — via `_render_turn`/`_render_activity` — into
`conversation_column` itself, so a phase-change note (still `.mark(
"activity-entry")`, now a small centered caption rather than its own row)
appears inline right after whichever turn it chronologically followed, instead
of in a visually separate column; `_RenderState`'s former
`turn_seq`/`activity_seq` pair collapsed into one `thread_seq` walking
`session.thread()` once per tick. `session.activity` (the scalar) and the
underlying `transcript`/`activity_log` lists are unchanged by this — only the
rendering merged. `log_column`'s own step rendering is untouched: tool-call
activity still never renders as a chat bubble or inline caption.
As of V13a, each rendered bubble also carries
`.classes("w-full")`: NiceGUI's `.nicegui-column` CSS (`align-items: flex-start`)
otherwise shrink-wraps every `ui.chat_message` to its content width regardless of
`sent=`, hiding the left/right alignment `sent=` was already choosing correctly —
plus explicit `bg-color`/`text-color` Quasar props resolved against
`theme.PALETTE` (`secondary`/`dark` for the user, `primary`/`dark` for
telcontar), fixing low-contrast white-on-gold bubble text. As of U4 this rendering is
`host/web/steplog.py`'s `sync_steps`/`StepLogState` (above), not inline: `run_page`
owns one `steplog.StepLogState()` and calls `steplog.sync_steps(log_column, shell,
step_log_state, session.steps)` once per tick, which caps the DOM at
`_MAX_LOG_ROWS = 500` (oldest row deleted first) and lets an already-rendered
"running" step's line update in place once it closes — unlike `TranscriptItem`s,
`StepRecord`s mutate after creation.
`run_page`'s `with app_shell(...) as shell:` captures the `Shell` handle so
`steplog.render_step_row` can reach `shell.show_detail()`.

**Sidebar tree refresh (U4, extended by V7):** `_refresh()` is now `async`. On each tick, if
`session.fs_revision` has changed since the render cursor last saw it, `run_page`
now (V7) just awaits `shell.reload_tree()` — the expansion-preserving
rebuild-from-disk logic that used to live inline here (`host/web/shell.py`, above)
moved onto `Shell` itself, since it's now also reachable from a manual refresh
button and `app_shell`'s own periodic poll timer, neither of which has this
render loop's `fs_revision`/`RenderState` bookkeeping to key off. This
`fs_revision`-gated path only fires when a tree-mutating tool actually closed
since the last tick (see `bridge.py`'s
`_TREE_MUTATING_TOOLS` above), never on every 0.5s poll of this timer specifically
— but as of V7 the tree also refreshes on its own independent
`TREE_POLL_INTERVAL` timer and via the manual button, regardless of whether a
run is active at all, with `reload_tree()`'s own skip-if-unchanged check
preventing any of these from colliding or resetting scroll/selection needlessly.
It never collapses whatever the user had expanded — closing the gap where the sidebar tree
(Phase 19 T3) never updated as the agent moved/renamed/quarantined files.

`run_web(target: Path | None = None, *, native: bool = True)` (V1 added the
keyword-only `native` parameter) still binds an ephemeral local port and calls
`ui.run(host="127.0.0.1", ..., show=False if effective_native else True,
reload=False, title=..., dark=True, favicon=..., native=effective_native,
window_size=(1280, 860) if effective_native else None)` — never `0.0.0.0`, to
avoid exposing the approval gate on the LAN. `native` (default `True` — "one
command, one window") requests a native `pywebview` window instead of the
system browser; `host/main.py`'s `--browser` flag is the escape hatch that
passes `False`. Rather than trust the argument blindly, `run_web` re-checks
actual availability itself: `effective_native = native and sys.platform ==
"win32" and importlib.util.find_spec("webview") is not None` — `pywebview` is a
Windows-only dependency (gated `; sys_platform == 'win32'` in
`pyproject.toml`) and may still be missing even on Windows. If native was
requested but isn't usable, `run_web` prints a warning to stderr and falls back
to the browser instead of hard-exiting — NiceGUI's own native-mode path calls
`sys.exit(1)` on a missing `webview`, unacceptable now that this is the default
entry point (U10). `favicon=` is `str(_ICON_PATH)` (the vendored
`host/web/assets/telcontar.ico`) when `effective_native` and that file exists,
else `theme.FAVICON_SVG` unchanged — NiceGUI's `favicon=` kwarg is dual-purpose:
in native mode, a local file path is also applied as the native window/taskbar
icon (there is no separate "icon" kwarg), while the browser-mode favicon is
untouched. `reload=False` is load-bearing, not a style choice: with `reload=True`,
uvicorn forces a `SelectorEventLoop` on Windows, where
`asyncio.create_subprocess_exec` (used to launch the MCP server subprocess) raises
`NotImplementedError`. `dark=True` is load-bearing too (T8): Quasar only honours
the `dark`/`dark_page` `PALETTE` tokens in dark mode. Before `ui.run()`, `run_web`
applies telcontar's visual identity globally and exactly once — `app.colors(
**theme.PALETTE)` (never a per-page `ui.colors()`, which would silently override
this and fragment the identity across routes), `app.add_static_files(
theme.FONT_URL_PATH, theme.FONT_DIR)` to serve the vendored Cinzel woff2 when the
fonts directory exists, and `ui.add_css(theme.css(), shared=True)`. As of U7,
`run_web`'s `@app.on_shutdown` hook also cancels every session's driving task
(`session.task.cancel()`), alongside its existing pending-future rejection — an
organize or query session's MCP server subprocess previously had no lifecycle at
all past shutdown; a full lifecycle/reaper (nothing ever calls
`web_session.close()` today) is still future work, this is minimal hardening
only. In native mode, `run_web` points the window at the running server via
`app.native.window_args.update(_native_window_args(url))` — as of X2, no
longer a direct dict-key assignment. `_native_window_args(url: str) -> dict`
is a pure helper (`{"url": url, "text_select": True}`) split out so its
content stays unit-testable on Linux CI, where native mode never actually
activates. `text_select=True` is the **primary fix** for a "chat text not
selectable/copyable" bug report: pywebview's own default is
`text_select=False` for the whole native window, independent of any in-page
CSS — the `_RESIZE_JS` drag bug (above) was the originally-suspected cause
but turned out to be secondary. No automated coverage exercises the real
native window end to end (CI has no `webview`, and the headless test harness
never executes JavaScript); manual verification in a real native window is a
known, documented gap. The browser tab/native window
title (T7) comes from `host.web.theme.window_title`: `ui.run(...)`'s `title=`
supplies the global default (no target yet), and `run_page` separately calls
`ui.page_title(theme.window_title(session.target))` from inside the page body —
`@ui.page(title=...)` is bound at decoration/import time and can't see the
per-request session's target, so the call is made live instead, landing the
target's name in the very first HTML response. `index_page` (the picker) never
calls `ui.page_title()`, since no directory is "selected" until a run exists.

**Reload-safe design:** a page reload creates a new NiceGUI client, but `RunSession`
(looked up by run_id from the URL) persists independently of any one client, and a
pending approval/cost/ask request is an `asyncio.Future` parked on the session rather
than an awaited NiceGUI dialog — so a reload re-attaches to an in-flight approval
instead of orphaning it. This was validated in a pre-implementation spike (see
ROADMAP.md's "Break 1" note ahead of Phase 18), which found that a bare reload does
**not** kill the background run — it silently orphans it, and any UI element the
run's task then tries to touch afterward targets a dead client, which can
permanently deadlock an approval gate with no visible symptom. As of U4 — and,
for `ask_user`'s dialog, V12 — the approval/cost/ask dialogs themselves
(`host/web/dialogs.py`) close the same failure class's other door: they're
`.props("persistent")` (no backdrop-click or Esc
dismissal) and resolve through `session.resolve_pending(result,
request_id=pending.request_id)`, so a dismissed-without-resolving dialog can no
longer deadlock a run, and a stale dialog from another tab or a pre-reload client
can't resolve a pending request it was never actually shown.

**Journal toolbar affordance (U6):** `run_page` renders a "Journal (N)" button
(`.mark("btn-open-journal")`) above `starter_column`, so it's usable before a
run even starts — TUI parity with the `j` keybinding, which works from the
moment `OrganizerScreen` mounts. Clicking it opens `host.web.dialogs.build_journal_dialog(session)`.
Its label's count is read via `host.web.journal.load_entries` — synchronously,
not via `run.io_bound` (see `host/web/dialogs.py` above) — both on initial
render and again inside `_refresh()`'s `fs_revision`-changed branch, alongside
the existing sidebar-tree rebuild, so the count refreshes whenever the target
directory's contents change (including from an undo, which also bumps
`fs_revision`).

**Query button (U7, session reuse X11):** the run page's main view also renders a
"Query this corpus" button (`.mark("btn-query-corpus")`), hidden until
`session.done` — mirroring the TUI's `OrganizerScreen`'s `g` keybinding, which is
gated the same way. As of X11, clicking it first calls
`web_session.find_by_target(session.target, mode="query")` and only falls back to
`web_session.create(session.target, mode="query")` when no query session for that
target exists yet — the same reuse the nav header's own Query tab uses (see
`host/web/shell.py`/`host/web/session.py` above) — before navigating to
`/query/{run_id}`.

**Browse corpus button (V5):** beside it, a "Browse corpus" button
(`.mark("btn-browse-corpus")`), hidden until `session.done` the same way —
`.visible` is set both at build time and again on every `_refresh()` tick, the
same two-places-set pattern the query button already needed (a test caught the
second site being missed during development). Clicking it navigates to
`/corpus/{session.run_id}` — the *same* session/run_id, not a new one, since
`corpus_page` only ever reads `session.target`.

**Dialogs (U4, extended by V12):** `_show_pending_dialog`'s inline checkbox/button-building code is
gone too — it now just tracks which `pending.request_id` has already been shown
(`_RenderState.shown_request_id`) and, on a new one, calls
`host.web.dialogs.build_approval_dialog`/`build_cost_dialog`/`build_ask_user_dialog`
(V12) and `.open()`s the result; the dialog's own buttons (Approve/Refine/Reject,
Proceed/Cancel, or — V12 — Submit/Skip) resolve `session.pending` directly (see
`dialogs.py` above).

`_pick_port()` binds an ephemeral `127.0.0.1` port.

Note: the ROADMAP text for S5 also names `_load_profile_options` (now
`host.configflow.profile_options`), journal reads, and `server.tools.undo_last` as
blocking calls to move off the event loop. As of U2, `profile_options()`'s one call
site — `host/web/wizard.py`'s `build_setup_wizard` — goes through
`await run.io_bound(configflow.profile_options)`, closing that gap. As of U6,
journal reads (`host.web.journal.load_entries`) and `undo_last`
(`host.web.journal.do_undo`) get their first real call sites — the Journal
toolbar button and its dialog above — but deliberately **do not** go through
`run.io_bound`/`asyncio.to_thread`: under NiceGUI's headless test harness, an
executor-callback continuation invoked from inside a click handler on a dialog
opened from *another* dialog's own click handler never resumes (confirmed by
direct experiment; documented as gotcha #6 in `tests/test_web_ui.py`'s module
docstring). Both operations are fast (a single small JSONL file) and rare (an
explicit, deliberate user click, never the poll timer), so a brief synchronous
stall is imperceptible — unlike this section's original motivating cases (a
full directory walk, a Windows keyring round-trip that can take seconds). See
`build_journal_dialog`'s docstring in `host/web/dialogs.py` for the same
rationale in place.
