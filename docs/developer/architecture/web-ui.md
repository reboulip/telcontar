# Architecture — Web UI

This page covers the NiceGUI web UI's own foundational design decisions — routing/session model, the persistent nav header, dialogs, sidebar tree, reload-safety, and theming. It is the `host/web/`-specific continuation of the [Architecture](core.md) overview, which covers the MCP host/server components, the core design decisions, and data flow. For the per-module reference, see [Module Reference — Web UI](../modules/web-ui.md).

---

## NiceGUI web UI foundations (S4-S6, extended by T2/T3/T5/T6/T7/T8, U1/U2/U3/U4/U6/U7/U10, V1/V5/V7/V11/V12/V13a/V13c/V15)

`host/web/` is a package — originally the first piece of a planned Textual→NiceGUI
web UI migration (ROADMAP Phase 18). As of S6, `telcontar --web` (`host/main.py`)
launched it in place of the then-default Textual TUI; as of U10, the relationship
inverted — bare `telcontar` (no flags) launched the web UI by default, with `--tui`
as an escape hatch back to the Textual TUI. As of Phase 22 (W1), the Textual TUI
(`host/app.py`) and the `--tui` flag were deleted outright — telcontar now always
launches the web UI, with no flag to opt out. The web UI's
`from host.web.main import run_web` import in `host/main.py` is still deferred
until after the startup `print()`, so the user sees "Loading telcontar…" before
paying the cost of its heavier imports (`nicegui`, `mcp`, `openai`, …). A
`--target PATH` flag skips the landing page's directory picker and starts a run
for that directory immediately. As of V1, the web UI also opens in a native
`pywebview` window by default (Windows only — falls back to the system browser,
with a stderr warning, if `pywebview` isn't installed or the platform isn't
Windows) rather than a browser tab; a `--browser` flag forces the browser instead
— see `run_web()` below. As of U1, the landing page itself offers direct
Organize/Query/Settings entry points, at parity with the TUI's `StartupScreen`:
Settings was already reachable via U3's persistent sidebar button and the
folder picker via Phase 19's T3 sidebar tree, so U1's remaining work was adding
a Query button next to Organize's — it validates the sidebar selection and
resolves it to the nearest `.organizer` ancestor via
`host.paths.find_organizer_root` (TUI parity with `StartupScreen._query`)
before starting a query-mode session, and both buttons now surface a real
error message instead of silently no-op'ing on an invalid or missing
selection. As of U2, it does have its own
first-run setup wizard at `/setup`, at parity with the TUI's, so it no longer
requires an already-configured install to be usable. As of U3, it also has a
settings view at `/settings`, reachable from every screen via a persistent sidebar
button — the same parity goal applied to the TUI's `ConfigScreen`. As of U6, it
also has a journal view + undo, with a visible toolbar affordance (TUI parity with
the keyboard-only `j` then `u`) — see `host/web/journal.py` and
`host/web/dialogs.py`'s `build_journal_dialog` below. As of U7, it also has a
read-only query view at `/query/{run_id}`, reached via a "Query this corpus" button
on the organize view (`/run/{run_id}`) once a run finishes — TUI parity with the
`g` keybinding — see `host/web/query_view.py` and `host/web/bridge.py`'s
`QueryBridge` below. As of V5, it also has a corpus browser at `/corpus/{run_id}`
— a sortable, filterable table over the document registry with a per-document
detail pane, reached via a "Browse corpus" button beside "Query this corpus" —
merging what used to be the separate V4 document-preview idea into one screen,
and reachable without any LLM call or agent turn at all (unlike query mode). No
TUI equivalent exists; see `host/web/corpus.py` and `host/web/corpus_view.py`
below. As of Y1, it also has a knowledge-graph view at `/graph/{run_id}` — a
ranked-actors table plus an optional force-directed graph panel over
`server/graph.py`'s already-built graph, previously invisible behind no UI at
all (a carryover from a deferred Phase 21 item) — reached via a "Knowledge
graph" button beside "Browse corpus", again with no LLM call or agent turn
involved. No TUI equivalent exists here either; see `host/web/graph.py` and
`host/web/graph_view.py` below. As of Y2, it also has a session list/resume
view at `/sessions` and `/sessions/{run_id}` — every session ever started
(live or dead, across process restarts), grouped by target, with a "Resume"
action that restarts a dead organize/query session from its last checkpoint —
reached via an always-enabled "Sessions" nav tab (unlike Query/Graph, not
target-scoped, since a session can belong to any target). See "Sessions"
below and `host/web/sessions.py`/`host/web/sessions_view.py`.

- `host/web/session.py` — `RunSession`, framework-agnostic per-run state. As of
  T5/T6, the transcript is turns-only: `RunSession.transcript: list[TranscriptItem]`
  (`seq`/`speaker`/`text`) holds genuine user↔telcontar exchanges (chat, ask_user,
  approval/cost outcomes, done/error) via `add_turn(speaker, text)`. Tool activity no
  longer interleaves there as a "steps"-kind item; instead `RunSession.steps:
  list[StepRecord]` (`seq`/`tool`/`summary`/`args`/`detail`/`status:
  "running"|"ok"|"error"`) holds it, and `RunSession.activity: str` holds a single
  mutable "what's happening right now" narration line. As of V16, a third list —
  `RunSession.activity_log: list[ActivityEntry]` (`seq`/`text`) — persists one
  entry per macro-phase change, appended via `add_activity(text)`; `activity`
  (the scalar) is unchanged and still what earlier tests assert against,
  `activity_log` is purely additive, giving the web UI a reviewable history of
  phase changes instead of a single line that's overwritten and lost.
  Deliberately not folded into `transcript`: `activity_log`, like `steps`, is
  telcontar's own narration, not a genuine user↔telcontar exchange — that
  data-model separation is unchanged by `thread()` (X3, below), which only
  merges the two into one chronologically-sorted *view* for rendering.
  `open_step(tool, summary,
  args)` starts a step "running"; `close_step(result, ok=...)` pairs it with its
  result — `{"args": ..., "result": result}`, pretty-printed JSON, capped at
  `_MAX_STEP_DETAIL_CHARS = 20_000` with a "(truncated)" suffix — and marks it "ok"
  or "error". A step that never closes (the run errored out mid-call) stays
  "running" forever by design — it shows exactly where things stopped, not a bug.
  `transcript`, `steps`, and `activity_log` share the same `_seq` counter for a
  stable relative
  ordering. `thread() -> list[TranscriptItem | ActivityEntry]` (X3) returns
  `transcript` and `activity_log` merged and sorted by that shared `seq` — a
  plain `sorted()`, no timestamps needed — deliberately excluding `steps`,
  which stays in its own log-strip drawer; consumed by `run_page`'s merged
  conversation rendering below. Also holds status, tokens, progress, a `pending` approval/cost/ask
  request (`kind: "approval"|"cost"|"ask"`, `"ask"` added V12) keyed to an
  `asyncio.Future`, a chat `messages` queue, conversation `history`, and
  (U7) a `mode: Literal["organize", "query"] = "organize"` field — one `RunSession`
  type/registry serves both kinds of run rather than a parallel `QuerySession` type,
  since query mode needs the exact same `add_turn`/`open_step`/`close_step`/
  `status`/`tokens` primitives (`pending`/`progress` simply stay unused for query
  sessions) — plus a module-level registry (`create(target, *, mode="organize")`/
  `get`/`close`/`all_sessions`/`find_by_target(target, mode="query")`) keyed by a
  `secrets.token_urlsafe(16)` run id. `find_by_target` (X11) returns an existing
  session matching both `target` (resolved) and `mode`, letting the nav header's
  Query tab and the "Query this corpus" button reuse one query session per target
  instead of minting a new one (and its own MCP subprocess) per click. A
  module-level `set_active(run_id)`/`get_active() -> RunSession | None` pair (X11)
  tracks which organize-mode session the persistent nav header's
  Conversation/Corpus tabs point at — set automatically by `create(...,
  mode="organize")` and by `app_shell()` whenever it's given a `session=`, and read
  by `app_shell()` as the fallback for routes with no session of their own in
  scope (e.g. `/settings`).
  Deliberately has no `nicegui` import, so it is unit-testable in plain pytest. `get_sidebar_width()`/`set_sidebar_width(width)`
  (T4) manage one in-memory sidebar-width preference (240-1000px, default 440 —
  the max raised from 720 in V13b, since the step-detail section now shares this
  same drawer and wants more room; the default itself raised again 380 → 440 in
  Y9, GH #58, once the document-preview pane joined the same stack visible by
  default) for
  the process's lifetime, rather than a `RunSession` field, since it also applies on
  the picker route where no `RunSession` exists yet; `set_sidebar_width` clamps and
  returns the stored value. As of U4, `RunSession` also carries `fs_revision: int`,
  a counter `bump_fs_revision()` increments whenever the target directory's
  contents change on disk — consumed by the sidebar-tree refresh below and, as of
  U6, the journal toolbar button's count refresh too (an undo also calls
  `bump_fs_revision()`, from the journal dialog's confirm handler). Also as of
  U4, `close_step(result, ok=...)` returns the closed `StepRecord` (`None` if none
  was open) instead of nothing, so a caller can inspect which tool just closed, and
  `resolve_pending(result, *, request_id=None)` takes an optional `request_id` that
  must match the *current* pending request's id or the call is silently ignored —
  stops a stale dialog (another tab, or one left over after a reload) from
  resolving a different, newer pending request than the one it was shown. As of U6,
  `has_open_step() -> bool` reports whether a tool call is still running (the
  `_open_step` field is non-`None`) — the journal dialog uses it to block undo
  while a tree-mutating step is in flight, since `server.journal.pop_last`
  rewrites the whole journal file while the MCP server subprocess may be
  appending to it.
- `host/web/bridge.py` — `AgentBridge` wraps a `RunSession` and exposes
  `on_event`/`on_approval_needed`/`on_cost_approval_needed`/`on_ask_user_needed`, the
  same callback contract `host/agent.py`'s `run_agent_loop` already uses for the
  Textual TUI, plus `run()`/`start()`, which drive one full organize run (settings
  load → `mcp_session` → `run_agent_loop`, including the O7 continuation loop for
  follow-up chat messages) as a detached `asyncio.Task`. Also `nicegui`-free. As of
  V12, `on_ask_user_needed` creates an `"ask"`-kind `PendingRequest` and awaits its
  future — the same request-scoped pattern `on_approval_needed`/
  `on_cost_approval_needed` already use — resolved by the structured
  `build_ask_user_dialog` (`host/web/dialogs.py`, below) rather than the pre-V12
  design of rendering the question into the transcript and reading the reply off
  `session.messages`; that old path double-posted the reply (once via the queue,
  once explicitly), which this fixes too, since `session.messages` is no longer
  touched at all.
  Both `start()` and `run()` take an optional `instructions: str | None = None`,
  threaded into the *first* `run_agent_loop` call only — never into an O7
  continuation — mirroring the Textual TUI's `OrganizerScreen._agent_worker`.
  As of T5/T6, `on_event`'s `tool_call`/`tool_result` handling no longer appends a
  chat turn — that was the "telcontar talking to itself in bubbles" T5 fixes.
  `tool_call` narrates into `session.activity` (via `Narrator.narrate`) — and, as
  of V16, also into `session.activity_log` via `session.add_activity(phrase)`,
  appended right alongside the `activity` assignment and only when the Narrator
  actually returns a new phrase (a repeated phrase collapses to nothing before
  either is touched) — and calls
  `session.open_step(tool, event.text, args)`, reading `tool`/`args` off `event.data`
  (`host/agent.py`'s 5 `AgentEvent("tool_call", ...)` sites now carry `data={"tool":
  name, "args": args}`, previously just the tool name); `tool_result` calls
  `session.close_step(result, ok=...)`, inferring `ok` from whether `event.data`'s
  `result` dict contains an `"error"` key (the same 5 sites now carry
  `data={"result": result}`, previously no data at all — additive only, no
  `run_agent_loop`/`run_query_loop` signature change). As of U4, `on_event`'s
  `tool_result` case also calls `session.bump_fs_revision()` when the just-closed
  step's tool is one of the module-level `_TREE_MUTATING_TOOLS =
  {"execute_plan", "write_index", "write_summary", "write_folder_readme"}` and the
  result was ok — the only tools that change what's on disk under the target
  directory, and what drives the sidebar-tree refresh below. As of U7,
  `host/web/bridge.py` also exports `QueryBridge(session)` — the same shape
  (`on_event`/`start`/`run`) as `AgentBridge`, but driving `host.agent.run_query_loop`
  instead of `run_agent_loop`. It has no `on_approval_needed`/`on_cost_approval_needed`/
  `on_ask_user_needed` at all — their absence is itself the safety property, since
  query mode is read-only by construction (`host.agent.QUERY_ALLOWED_TOOLS`). Its
  `on_event` deliberately has no `"done"` case: `run_query_loop` both emits a
  `"done"` event and returns the answer text, and the TUI's own
  `QueryScreen.on_event` renders only from the return value — `QueryBridge.run()`
  mirrors that, calling `session.add_turn` with the returned answer after each
  `run_query_loop` call rather than reacting to the event. `done`/`error` here are
  per-question, not per-session: `QueryBridge` never sets `session.done`.
- `host/web/dialogs.py` (U4, extended by U5/U6/V12) — one builder per `PendingRequest` kind, replacing the
  dialog-building code that used to live inline in `run_page`.
  `build_approval_dialog(session, pending)` is a faithful port of the TUI's
  `ApprovalModal` for the rationale + disclaimer and the folder-notes disclaimer,
  but as of V3 replaces the old flat `render_target_layout` text block plus
  separate checkbox list with an actual before/after file tree
  (`host.format.plan_tree_diff`, pure — no filesystem I/O, derived entirely from
  the plan's ops): a read-only "Before" panel of current file locations beside an
  "After" panel of destinations (folder notes annotated on its folder lines),
  with per-op checkboxes living inline on each after-tree file node. As of X4,
  `plan_tree_diff` first chains a same-file rename/move/quarantine/
  archive_document sequence (e.g. a rename immediately followed by a move of
  the renamed file) into one tree line via the private `_chain_ops` helper —
  resolving each op's source through the chain first, mirroring
  `execute_plan`'s own path resolution (`server/tools.py`) — so the file
  appears once with its true start/end state instead of once per op with a
  wrong intermediate one; `PlanTreeLine.op_ids` carries every op_id chained
  onto that line, and (X4/X6) the dialog gives such a line exactly one
  checkbox spanning the whole chain, so unchecking it excludes every chained
  op together rather than leaving the file half-applied. As of X6, every
  checkbox also carries an explanatory tooltip ("Uncheck to skip this
  change", or "...skip all of this file's chained changes" for a chain), and
  both the after-tree and "Other operations" sections show a standing caption
  above their checkboxes ("Unchecking a box excludes that change from
  execution.", `.mark("after-checkbox-hint")`) — X4's chaining made a bare
  checkbox easy to misread. As of X7, each tree row's `depth`-based indent is
  rendered as real vertical guide lines — a new `_render_tree_guides(depth)`
  helper emits `depth` CSS-bordered spacer divs (`.tc-tree-guide`, styled in
  `theme.css()`, below) before the row's label — replacing a flat
  `margin-left` indent, and visually matching the sidebar tree's own
  connector lines (`host/web/shell.py`, below) now that both share one
  color/density system. Ops with no
  clean tree slot — `create_dir`, `compress_quarantine`, `update_file`, and any
  `quarantine`/`archive_document` with no destination — fall back to an "Other
  operations" list below the tree, each still with its own checkbox: every op in
  the plan gets exactly one checkbox somewhere (shared across its chain, if
  any), preserving the `removed_op_ids`
  safety contract. `render_target_layout` itself was unchanged by V3 — the
  now-deleted Textual `ApprovalModal` used to call it directly for its own
  flat-text preview; only this dialog's use of it was replaced. The card is now sized via an inline style
  (`width: 90vw; max-width: 1400px`) rather than a Tailwind `max-w-3xl` class —
  Quasar's own dialog CSS outranks a same-specificity Tailwind swap. Then the
  `ops_json_path` label — as of X5, paired with a "Reveal in file explorer"
  button that confinement-checks the path against `session.target` (same
  spirit as the tree's own `is_op_out_of_scope` check) before calling
  `host.paths.reveal_in_file_manager` (Explorer's `/select,` flag on Windows,
  `open -R` on macOS, `xdg-open` of the parent folder on Linux — fire-and-
  forget, never raises) — a free-text refine input, and Approve/Refine/Reject
  buttons — Refine only resolves on non-blank input and always takes priority
  over Approve, since they're mutually exclusive button clicks. `build_cost_dialog(session, pending)` is a faithful
  port of the TUI's `CostEstimateModal` (U5): title "Analyze this corpus?", a
  summary line composed from the engine-side `pending.payload["data"]` dict
  (`new`/`already_analyzed`/`estimated_tokens`/`batch_size`, falling back to
  `pending.payload["summary"]` only when `data` is empty), the same "rough
  estimate from file sizes" disclaimer as the TUI, and Proceed/Cancel buttons.
  `build_ask_user_dialog(session, pending)` (V12) replaces the old design — the
  question rendered as a plain transcript turn, answered by typing a normal chat
  message — with a structured modal: one radio-button group per question
  (`pending.payload["questions"]`, only when the agent supplied `options`) plus a
  free-text "Additional comment" field, and Submit/"Skip — you decide" buttons.
  Submit composes the reply in code — never a second LLM round-trip —
  `"<question text> → <selected option>"` per answered question, plus an
  `"Additional comment: <text>"` line when non-blank; Skip resolves
  `AskUserResult(reply="", provided=False)`, the same "proceed with your own best
  judgement" signal a degenerate/no-callback case already produced. All three
  dialogs are `.props("persistent")` (no backdrop-click or Esc dismissal) and
  resolve through a shared `_make_resolver(session, pending, dialog)` helper
  (V12) — request-scoped `session.resolve_pending(...)` then `dialog.close()` —
  factored out of `build_approval_dialog`/`build_cost_dialog`'s previously
  separately hand-rolled copies of the same shape (pure refactor, no behaviour
  change to either) and reused by `build_ask_user_dialog` too. This closes a live
  bug where the prior plain `ui.dialog()` could be dismissed without resolving
  its future, permanently deadlocking the run with no visible symptom: the same
  failure class as the reload-orphaning issue described below ("Reload-safe
  design"), just a different door into it — V12 applies the same treatment to
  `ask_user`'s new dialog too. `build_journal_dialog(session)` (U6) is different
  in kind — it isn't resolving a `PendingRequest`, so it's a plain, dismissible
  `ui.dialog()`.
  It lists journal entries (`host.web.journal.load_entries`, rendered via
  `host.format.fmt_journal_entry`) and an Undo button gated behind a sibling
  confirm dialog; confirming calls `host.web.journal.do_undo` and, on success,
  `session.bump_fs_revision()`. The Undo button is replaced with an explanatory
  label while `session.has_open_step()` is true. Both `load_entries`/`do_undo`
  are called synchronously (not via `run.io_bound`) — see `host/web/journal.py`
  below.
- `host/web/journal.py` (U6) — journal load/undo logic, `nicegui`-free like
  `session.py`/`bridge.py`/`tree.py`. `load_entries(target) -> list[dict]` wraps
  `server.journal.all_entries` (defensive `try`/`except`, returns `[]` on any
  error — mirrors `host.app.JournalScreen`'s existing handling) and
  `do_undo(target) -> dict` wraps `server.tools.undo_last`, both resolving
  `journal_path`/`plans_dir` via `host.paths.resolve_journal_path`/
  `resolve_plans_dir`. Both `server.journal`/`server.tools` imports are late
  (inside the functions), matching the TUI's own existing discipline. Called
  synchronously from `host/web/dialogs.py`'s `build_journal_dialog`, not via
  `run.io_bound`/`asyncio.to_thread` like every other blocking-I/O call site in
  `host/web/` — under NiceGUI's headless test harness, an executor-callback
  continuation invoked from a click handler on a dialog opened from *another*
  dialog's own click handler never resumes (confirmed by direct experiment,
  documented as gotcha #6 in `tests/test_web_ui.py`'s module docstring). Both
  operations are fast (one small JSONL file) and rare (an explicit, deliberate
  user click, never the poll timer), so a brief synchronous stall is
  imperceptible, unlike this section's motivating cases below (a full directory
  walk, a Windows keyring round-trip that can take seconds).
- `host/web/steplog.py` (U4) — the internal-step log-strip rendering
  (`fmt_step_line`, `render_step_row`, `prune_log`, `sync_steps`, `StepLogState`)
  lifted out of `run_page`'s closure, originally intended so later screens could
  reuse the same "one compact line per step, toggle opens full detail in the
  shell's drawer" idiom instead of re-deriving it per screen. U6's journal
  dialog didn't end up needing it — journal entries render as plain formatted
  lines (`host.format.fmt_journal_entry`), not as steps — so the reuse case is
  still open, pending U7's query view.
- `host/web/shell.py` (T2, extended by T3, T6, U3, V7, V13b, X11) — `app_shell(*,
  target=None, on_select=None, session=None, active=None, nav=True)`, a
  `@contextmanager` mounted by every `@ui.page` route, including
  the early-return branches (not-configured, run-not-found), so a left-sidebar frame
  is visible on every screen rather than being assembled per-page. As of U3 the
  drawer always renders a persistent "Settings" button navigating to `/settings`,
  reachable from every route — the web UI's counterpart to the TUI's app-level
  `ctrl+s` binding (`host/app.py`'s `action_open_settings`). As of V7, that same
  header row also carries a manual refresh icon button (`.mark("btn-tree-refresh")`)
  next to the "telcontar" label, calling `shell.reload_tree()`. It creates the
  `ui.left_drawer` as a direct child of the page body — NiceGUI's
  `require_top_level_layout` raises `RuntimeError` if a drawer is nested inside
  another container — mounts a `ui.tree` inside it from `host.web.tree.build_nodes`
  (`.props("dense selected-color=primary")` for a denser vertical
  rhythm — `selected-color`, X1, a pure Quasar prop, highlights whatever node
  `shell.tree.select(...)` marks selected, no new CSS — `.classes("w-full
  tc-tree")` (X7, dropped the previous `no-connectors` prop so Quasar's own
  built-in QTree connector lines render, styled/tightened by `theme.css()`'s
  new `.tc-tree` rules below) — plus — as of
  V13b — `max-height: 45vh; overflow-y: auto` so the tree scrolls internally
  rather than pushing what's stacked below it off-screen). As of V13b, the
  internal-step detail zone (T6) is stacked directly below the tree inside this
  *same* left drawer instead of the separate `ui.right_drawer` T6 originally
  created: a hidden-by-default `ui.column().mark("detail-section")` holding a
  title label (`.mark("detail-title")`), a close button
  (`.mark("btn-detail-close")`, calls `shell.hide_detail()`), and the detail body
  — `ui.codemirror("", language="JSON", theme=theme.CODEMIRROR_THEME).disable().mark(
  "detail-content")`, created once at build time. `theme.CODEMIRROR_THEME`
  (`host/web/theme.py`, `= "basicDark"`) is required explicitly because
  `ui.codemirror` otherwise defaults to a light theme regardless of the app's own
  dark palette. As of Y9 (GH #58), the same drawer also stacks a
  document-preview section directly below the step-detail one — a
  visible-by-default `ui.column().mark("doc-section")` wrapping the existing
  `host/web/docpane.py` widgets (`build_doc_pane()`, unchanged), in its own
  `max-height: 40vh; overflow-y: auto` scroll region so the drawer doesn't
  scroll as one long unit. `app_shell` yields a
  `Shell` dataclass (`drawer`, `tree`, `content`, `detail_section`, `detail_title`,
  `detail_content`, `target`,
  `selected`, — V7 — a private `_reloading: bool` guard, and — Y9 —
  `doc_section`/`doc_pane`) that the page body
  builds into via `with app_shell(...) as shell:` — no more standalone
  `detail_drawer` field.
  `Shell.show_detail(title, detail)` (rewritten V13b) now populates the existing
  widgets in place — `detail_title.set_text(title)`, `detail_content.set_value(
  detail)` — and reveals the section (`.visible = True`) instead of clearing and
  rebuilding a separate drawer each call; the new `Shell.hide_detail()` sets
  `.visible = False` back. As of Y9, `show_detail`/`hide_detail` also toggle
  `doc_section`'s visibility the other way (hide/restore respectively) — the
  step-detail and document-preview sections share this one drawer and are
  mutually exclusive, so `Shell` gained matching `show_document(record)` /
  `show_unanalyzed(path, meta_line)` / `clear_document()` methods that
  populate/clear `doc_pane` and hide `detail_section` in turn. Never `ui.code`/`ui.markdown`
  — both of those render through a markdown fenced-code path, and step detail can
  carry untrusted document content (e.g. a `read_file_batch`/`extract_text_batch`
  result) that must never be interpreted as markup. `ui.codemirror` takes the
  content as a plain value/prop instead, with `.disable()` making it read-only
  display, not an editor. `host/web/main.py`'s `run_page` is the only caller,
  reached via a per-step "code" icon button. The tree's `on_select`
  handler ignores clicks on lazy-load placeholder nodes and otherwise sets
  `shell.selected` and invokes the optional `on_select` callback; its `on_expand`
  handler is async and, the first time a real directory node is expanded, calls
  `host.web.tree.load_children` via `run.io_bound`, splices the result into
  `tree.props["nodes"]` in place, and calls `tree.update()` — S5's blocking-I/O
  rule means that listing must happen off the event loop. As of V7, the node is
  re-located a second time in the post-await `tree.props["nodes"]` before
  splicing children into it, rather than reusing the pre-await reference, which
  a concurrent `reload_tree()` poll (below) can otherwise detach from what's
  actually live mid-`await` — a latent race that only became reachable once a
  second writer of `nodes` existed. `Shell.refresh_tree(root)`
  (T3) re-roots the sidebar tree at `root`, used by the picker's "go up one level"
  button and (Windows-only) drive-root dropdown — both rendered only when
  `on_select` is wired in (i.e. only on the picker route, `/`; `/run/{run_id}`'s
  tree is for verification only, not re-rooting).

  **`Shell.reload_tree()`** (V7) is the one writer for `tree.props["nodes"]` from
  here on: rebuilds from disk via `host.web.tree.rebuild_nodes` (`run.io_bound`),
  preserving the expanded set, and skips the actual prop assignment (and
  `tree.update()`) when the rebuilt nodes match what's already there — a QTree
  `nodes` replacement re-renders the whole subtree and can reset scroll/
  selection, so a no-op poll must leave the prop untouched, not just be quick.
  `self._reloading` guards against the page's `REFRESH_INTERVAL` render-poll
  timer and this method's own new poll timer — two independent `ui.timer`s —
  calling in concurrently; a no-op when `self.target is None`. Called from the
  manual refresh button, a new `ui.timer(web_session.TREE_POLL_INTERVAL,
  shell.reload_tree)` (created only when `app_shell()` has a real `target`), and
  `host/web/main.py`'s post-`execute_plan` `fs_revision` refresh (U4), which now
  just awaits this instead of inlining the rebuild — `main.py` no longer imports
  `host.web.tree` directly as a result. `TREE_POLL_INTERVAL` (`host/web/session.py`,
  default `5.0`s, same test-seam pattern as `REFRESH_INTERVAL`) is deliberately
  coarser than the 0.5s render poll, since `rebuild_nodes` recurses over every
  expanded directory.

  **Landing-page picker root and controls (Y3, GH #62):** when `app_shell` gets no
  explicit `target` (the picker route, `/`), the sidebar tree now roots at
  `web_session.get_start_dir()` — the directory telcontar was launched from
  (`Path.cwd()`, falling back to `Path.home()` if the cwd can't be read) — instead
  of always defaulting to the home directory. This also fixed a latent dead-code
  bug: the picker's "go up one level" button (`.mark("btn-go-up")`) and
  Windows-only drive-root dropdown (`Shell.refresh_tree`, above) were already
  gated on `on_select` being passed to `app_shell`, but `host/web/main.py`'s
  `index_page` never actually passed one — so neither control ever rendered, and
  the picker was permanently stuck at whichever directory it happened to root at.
  `index_page` now passes a real `on_select` callback (clears the startup error
  label on selection, threaded through a mutable cell since the label doesn't
  exist yet at the point `app_shell` is entered), so both controls render and
  work, keeping every other directory reachable even though the default root
  changed from home to cwd.

  `app_shell`'s signature stayed frozen through Phase 20/21 (U1-U7, V7 all mounted
  through it unchanged) until Phase 23's X11 extended it with `session`/`active`/
  `nav` for a persistent nav header (below).
  `host/web/shell.py` now shares nicegui-importing duties with `host/web/main.py`.

  **Persistent nav header (X11, logo added X13):** when `nav=True` (the default), `app_shell` opens
  with a `ui.header()` mounted before the left-drawer sidebar — as of X13,
  `theme.LOGO_SVG` (the White Tree mark) beside the "telcontar" label —
  plus a `ui.tabs(value=active)` with one tab each for `conversation`/`corpus`/
  `query`/`graph` (Y1)/`sessions` (Y2)/`settings` — so every route (bar `/setup`, which passes `nav=False` since
  the first-run wizard has nowhere valid to navigate to yet) gets the same
  top-level tab strip. `session` is the *organize*-mode `RunSession` driving the
  current route (`/run/{run_id}`/`/corpus/{run_id}`/`/graph/{run_id}` pass their own; a query-mode
  session is never passed here) and, via `web_session.set_active`, becomes what a
  session-less route's tabs fall back to. The Conversation/Corpus tabs disable
  when no organize-mode session is available (the route's own `session` or the
  module-level active-run tracker, `session.py` above); the Query and Graph tabs
  disable under the exact same condition — the resolved target has no analyzed
  corpus above it (`host.paths.find_organizer_root`) — since a knowledge graph
  is equally meaningless without one. The Sessions tab (Y2) is the one
  exception to all of the above: it is never disabled, target-scoped or
  otherwise, since a session can belong to any target, not just whichever one
  the current route happens to be showing. Selecting a tab navigates to the
  corresponding route — Query reuses an existing query session for the target via
  `web_session.find_by_target` where possible, else creates one; Graph navigates
  straight to `/graph/{effective_session.run_id}` using the current
  organize-mode session (no separate graph-mode session type exists); Sessions
  (Y2) always navigates to the fixed `/sessions` route, independent of any
  session — additive
  to, not a replacement for, the left-drawer's own unconditional Settings button
  described above.
  The drawer's width (T4) is set from `web_session.get_sidebar_width()` via the
  Quasar `width` prop (never raw CSS, since Quasar also offsets
  `.q-page-container` from that prop), and a 6px drag handle on the drawer's
  right edge — wired by a small injected JS snippet (`_RESIZE_JS`) tracking
  pointerdown/pointermove/pointerup on `document` rather than just the handle
  (so the pointer can leave the 6px strip mid-drag without breaking the resize)
  — live-resizes the drawer in the DOM during the drag, clamped between
  `SIDEBAR_WIDTH_MIN`/`_MAX` in the JS itself (`Math.max(__MIN__, Math.min(
  __MAX__, ...))`, the two bounds interpolated into `_RESIZE_JS` at module-import
  time via `.replace()` as of V13b — previously hardcoded `240`/`720` literals
  that had already drifted out of sync the moment `SIDEBAR_WIDTH_MAX` became
  `1000` in this same change) — and, only on pointerup,
  emits a `tc_sidebar_resized` event that the Python side clamps, persists via
  `web_session.set_sidebar_width()`, and re-applies as the real `width` prop. As
  of V15, `_RESIZE_JS` must be a self-invoking IIFE, not a bare arrow-function
  expression: `ui.run_javascript` evaluates the string via `eval`, which
  constructs but never calls a bare function literal, so the handle's listeners
  were never actually bound in any browser (not an Edge-specific regression, as
  first suspected) until this fix. A `window.__tcSidebarResizeWired` guard makes
  the wiring idempotent if the snippet ever runs more than once. As of X2, a
  `setPointerCapture` call on pointerdown keeps the drag targeting the handle
  even once the cursor leaves the browser window, and the pointerup cleanup
  (clearing `cursor`/`userSelect`, emitting `tc_sidebar_resized`) is now a
  shared `endDrag()` bound to `pointerup`, `pointercancel`,
  `lostpointercapture`, and `window`'s `blur` instead of just `pointerup` —
  fixing a real bug where a drag released outside the window left
  `document.body.style.userSelect` stuck at `'none'` (the whole page
  unselectable) until reload. Originally the suspected cause of a "chat text
  not selectable" bug report, but it turned out to be secondary — see
  `run_web()`'s native-window `text_select` fix below for the actual primary
  cause.
- `host/web/tree.py` (T2, fleshed out by T3) — NiceGUI-free, mirroring
  `session.py`/`bridge.py`'s invariant so it stays testable in plain pytest.
  `build_nodes(root: Path) -> list[dict]` builds the top-level node `ui.tree`
  expects (`{"id": <absolute path str>, "label": <basename>, "children": [...]}`),
  loading the root's immediate children eagerly (one directory listing) while
  deeper levels stay lazy behind a placeholder-child scheme (a node id ending in
  `PLACEHOLDER_SUFFIX`, a null-byte + ellipsis sentinel that's never a real path) —
  so a page load never walks the whole tree. `load_children(path)` lists one
  directory's immediate entries, both files and folders (the sidebar's job is
  letting the user verify files actually moved/renamed, not just browsing folders),
  sorted folders-then-files then alphabetically; it hides dotfiles but deliberately
  *not* `_quarantine` (the only removal path — the user must be able to see what
  landed there), never follows symlinks/junctions (Windows profile directories like
  "Application Data" can loop), and never raises — a permission error or a vanished
  directory yields an empty list rather than blanking the tree. `find_node` and
  `needs_loading` support `shell.py`'s expand handler. `rebuild_nodes(root,
  expanded_ids)` (U4) is a non-destructive alternative to `build_nodes`: it eagerly
  loads real children, recursively, for every directory id in `expanded_ids`
  instead of leaving the lazy-load placeholder, so refreshing the sidebar after a
  tree-mutating tool call doesn't collapse whatever the user had expanded; a
  directory that no longer exists (moved/renamed away by the very op that
  triggered the refresh) is silently dropped. `list_drive_roots()` wraps
  `os.listdrives()` (3.12+, Windows-only) so the picker can reach outside the home
  directory, degrading to an empty list on any other platform or on error.
- `host/web/theme.py` (T7, extended by T8/V13c, favicon/logo redesign X13) — product-identity helpers, `nicegui`-free
  (mirrors `session.py`/`bridge.py`/`tree.py`'s plain-pytest-testable invariant).
  `window_title(target: Path | None = None) -> str` returns `"telcontar"` with no
  target, or `f"telcontar — {target.name}"` once one is selected, falling back to
  the full path string when `.name` is empty (a Windows drive root has none, so the
  title never ends in a dangling "— "). T8 adds telcontar's visual identity — a
  Númenórean/human-king motif, gold and silver on a dark base — through two pieces
  consumed by `run_web()`: `PALETTE: dict[str, str]`, exactly the 9 keyword names
  `nicegui.app.colors()` accepts, with gold `primary`/mithril-silver `secondary` on
  a dark `dark_page`/`dark` base, while `positive`/`negative` deliberately stay in
  their own desaturated green/red families rather than being re-hued gold/silver —
  the approval dialog's Approve/Reject buttons are the highest-trust screen in the
  product and must stay unmistakable; and `css(font_dir=None) -> str`, one CSS layer
  binding the vendored Cinzel display face (via `font_face_css()`, emitted only when
  the woff2 actually exists on disk — otherwise a fallback serif stack, never a 404)
  onto Quasar's own `.text-h1`...`.text-h6` classes, plus — as of V13c — a new
  `.tc-display` utility class (applied explicitly via `.classes(...)` at two call
  sites, the sidebar's brand label and the approval dialog's title) and Quasar's
  own `.q-message-name` (chat sender-name) slot, which — like the heading classes —
  picks up the display face automatically with no code changes at any
  `ui.chat_message(...)` call site, plus a mandatory
  `.q-btn.bg-primary` text-colour fix (Quasar's default white button label is
  ~2.2:1 contrast on the gold primary — unreadable), plus — as of X2 — a
  `.q-message-text`/`.q-message-text-content { user-select: text }` rule, the
  third of three defense-in-depth layers fixing a "chat text not
  selectable/copyable" bug report, alongside the native-window `text_select`
  fix and the `_RESIZE_JS` drag-cleanup fix (both described elsewhere in this
  file), plus — as of X7 — four tree-connector/density rules shared by both
  tree views: a `border-color` rule targeting the `.tc-tree` class's
  `q-tree__node:after`/`q-tree__node-header:before` pseudo-elements mutes
  Quasar's default connector color to a muted `PALETTE["secondary"]` silver on
  the sidebar tree (re-enabled by dropping `no-connectors`, `host/web/shell.py`,
  above) and two more rules tighten that same tree's row density;
  `.tc-tree-guide`/`.tc-tree-row` style the plan-approval dialog's hand-rolled
  before/after tree's CSS-only guide lines instead (`_render_tree_guides`,
  `host/web/dialogs.py`, above), since that tree has no real QTree widget to
  re-enable connectors on. `FAVICON_SVG` is an inline SVG
  passed to `ui.run(favicon=...)`, which NiceGUI inlines as a data URL with no
  file or network request — the browser-mode favicon, untouched by V1's
  native-window icon (see `run_web()` below). As of X13, both `FAVICON_SVG` and
  a new `LOGO_SVG` render a White Tree of Gondor mark (branching silver
  trunk/roots, a gold star arc) — replacing T8's original Elendil's-star design
  — with `LOGO_SVG` a transparent, more-detailed variant rendered via
  `ui.html(...)` beside the "telcontar" wordmark in the persistent nav header
  (`host/web/shell.py`, below); legal there only because it's an in-repo
  constant, never registry/document content.
  `CODEMIRROR_THEME: Final = "basicDark"` (V13b) exists because `ui.codemirror`
  defaults to a light theme regardless of `PALETTE`/`dark=True`; every read-only
  `ui.codemirror` in the app — `Shell.show_detail`'s step-detail body and
  `host/web/settings.py`'s three prompt-inspection panels — now passes it
  explicitly instead of falling back to that mismatched light default.
- `host/configflow.py` (U2, extended by U3) — framework-agnostic (no `nicegui`, no
  `textual`) configuration-flow logic factored out of the TUI's
  `SetupScreen`/`ConfigScreen` so both the web UI's setup wizard and settings view
  read from the same source of truth: `profile_options()`
  (moved here from `host/app.py`'s old `_load_profile_options`/`_PROFILE_LABELS`),
  `SERVICE_HINTS` (per-service URL/model hint and placeholder text),
  `validate_credentials(url, key, model, *, key_required)` (url → key → model,
  first-error-wins; `key_required=False`, U3, is the settings view's blank-key-
  preserves-existing case), `build_wizard_updates(...)`, `build_settings_updates(url,
  key, model, profile, approval_mode)` (U3 — omits `llm_api_key` entirely when `key`
  is blank, instead of the wizard's always-include-the-key behaviour),
  `APPROVAL_OPTIONS` (U3, moved out of `host/app.py`'s `ConfigScreen`), and
  `plaintext_warning(button_label, recovery_action="go back")` — the shared
  warning-message builder that fixes U8's copy bug (the TUI wizard used to say
  "Press \"Finish\" again" while its button read "Save & continue →"). `host/app.py`
  used to import from here instead of owning this logic itself; as of Phase 22
  (W1), `host/app.py` is deleted and the web UI's wizard/settings view are the
  module's only consumers. See the [core module reference](../modules/core.md) for the
  full breakdown of this module.
- `host/web/forms.py` (U2, extended by U3) — shared NiceGUI form fragments:
  `credential_inputs(...)` renders the URL/API-key/model input triple (each
  `.mark()`ed for NiceGUI's headless `user` test fixture); as of U3 it takes a
  `key_placeholder` parameter so the settings view can show "Paste a new key, or
  leave empty to keep the current one" in place of the wizard's generic
  placeholder. `save_with_plaintext_guard(build_updates, *,
  plaintext_confirmed, button_label, recovery_action="go back")` calls
  `config.settings.save_user_config` via `run.io_bound`, always from a *fresh* dict
  built by `build_updates()` — never a cached one, since `save_user_config` pops the
  API key out of its argument before raising `PlaintextKeyFallbackNeeded`, so reusing
  a dict across a retry would silently drop the key.
- `host/web/settings.py` (U3, extended by V11) — the settings view, a NiceGUI port of
  `host/app.py`'s `ConfigScreen` at parity with it, including the
  blank-key-preserves-existing rule. `build_settings_view(*, on_done)` fetches
  `configflow.profile_options()` and `config.settings.read_user_config()` via
  `run.io_bound`, then renders a single-page form (URL/API-key/model, document
  profile, approval mode, Save/Cancel) through one `@ui.refreshable` — unlike the
  wizard, there's no multi-step routing to do. Saves through the same
  `forms.save_with_plaintext_guard` as the wizard. Mounted at `@ui.page("/settings")`
  in `host/web/main.py`, reached from any screen via the sidebar's Settings button
  (`host/web/shell.py`, above). As of V11, it also renders a collapsed "What
  telcontar tells the model" `ui.expansion` (`.mark("expansion-prompts")`),
  deliberately placed OUTSIDE the `@ui.refreshable form()` region so a
  validation-driven `refresh()` never recomposes it — backed by
  `host.agent.composed_system_prompts(settings)` (the three read-only composed
  ORGANIZE/QUERY/ANALYZE system prompts) and `host.agent._resolved_profile_name(settings)`
  (which domain profile actually resolved, surfacing a load failure instead of
  silently falling back the way the prompt renderers themselves do), both built off
  a plain `config.settings.Settings()` instance rather than `load()` (which raises
  without credentials) so the panel works even before the setup wizard has run.
  Rendered via disabled `ui.codemirror(..., theme=theme.CODEMIRROR_THEME)` only
  (the explicit `theme=` a V13b addition — `ui.codemirror` otherwise defaults to a
  light theme regardless of the app's own dark palette), never `ui.markdown`/`ui.html`,
  matching `Shell.show_detail`'s precedent (T6) — a composed prompt can embed
  profile free-text and `NAMING.md` content. Deliberately read-only: an editable
  prompt sits next to M10's injection-resistance guardrails and needs its own
  security pass first. The panel also notes what it can't show — the corpus
  digest and the user's pre-analysis steering instructions, both composed at run
  time from a live target/registry this target-free view never has.
- `host/web/wizard.py` (U2) — `build_setup_wizard(*, on_finish)`, a 1:1 port of
  `host/app.py`'s `SetupScreen`: the same 5 steps (welcome, service choice, API
  details, document profile, done), same validation order/strings (via
  `host/configflow.py`), same plaintext-keyring warn-then-confirm flow (via
  `forms.save_with_plaintext_guard`). Routes between steps with a `ui.refreshable`
  function keyed on a page-closure `_WizardState.step` — real per-step routing, the
  NiceGUI-native equivalent of the TUI's mount-all-five-and-toggle-`.display`
  approach. State lives only in that closure, never `app.storage` or a URL param —
  the API key must never touch either. Lives outside `host/web/main.py` on purpose:
  `main.py`'s `@ui.page` decorators stay thin shells: `@ui.page("/setup")` just
  calls `build_setup_wizard(on_finish=lambda: ui.navigate.to("/"))` inside
  `app_shell()`.
- `host/web/query_view.py` (U7, extended by V13a) — the query page's UI: `build_query_view(shell,
  session)` renders a conversation column (`ui.chat_message`, the same idiom
  `run_page` uses for organize turns — including `run_page`'s V13a bubble
  alignment/colour fix, deliberately duplicated here rather than shared until
  Y7 unified it — see the chat-bubble-rendering note below) plus a step-log strip
  (`host.web.steplog.sync_steps`, the same T5/T6 idiom `run_page` already
  established) instead of the TUI `QueryScreen`'s side-by-side dual-`RichLog`
  split — Phase 20 is parity with a cleaner surface, not a redesign, and the log
  strip already is the web UI's "tool timeline". No approval/cost/ask dialog wiring
  at all — query mode is read-only by construction, so there is nothing to gate.
- `host/web/corpus.py` (V5) — registry load logic, `nicegui`-free, mirroring
  `host/web/journal.py`'s contract exactly: this module owns the
  filesystem-adjacent logic, `host/web/corpus_view.py` owns the rendering.
  `list_documents(target) -> list[dict]` loads the registry via
  `server.registry.load(host.paths.resolve_registry_path(target))` and returns
  `[rec.to_dict() for rec in registry.records()]`, or `[]` on any error (missing
  file, corrupt JSON) — never raises, the same defensive contract as
  `journal.load_entries`. `get_document(target, checksum) -> dict | None` returns
  one record by checksum, or `None` if missing/unreadable. `registry_mtime(target)
  -> tuple[float, int] | None` (X10) returns `(mtime, size)` of `registry.json` via
  a plain `.stat()`, `None` on any `OSError` — a cheap pre-check letting
  `corpus_view.py`'s poll skip re-parsing the whole registry on a tick where
  nothing changed. `find_by_path(target, path) -> dict | None` (X9) matches a
  record by exact normalized-path comparison against each record's `path` —
  no basename fallback, deliberately, since a fallback could surface the wrong
  document's summary — feeding the run page's new document-preview pane
  (below). `server.registry` is
  called directly, never through MCP — there is no agent-reachable reason for a
  read-only *browse* to exist — and its import is late (inside the functions),
  matching `journal.py`'s discipline.
- `host/web/corpus_view.py` (V5, row-click selection X12, live polling X10) —
  the corpus-browser page's UI. `_CorpusViewState` now holds all reloadable
  state, not just the selection: `selected_checksum`/`current_filter` (X10) let
  a reload restore what the user was looking at; `records`/
  `records_by_checksum`/`all_rows` (X10) are the reloadable data itself, lifted
  out of local variables; `reloading`/`last_mtime` (X10) back the same
  re-entrancy-guard/skip-unchanged disciplines `Shell.reload_tree` already
  established for the sidebar tree poll.
  `build_corpus_view(session)` loads records via `await
  run.io_bound(corpus.list_documents, session.target) or []` (the `or []`, X10,
  guards `run.io_bound`'s documented None-on-cancellation contract). As of X10,
  the page no longer early-returns on an empty registry — the search input,
  table, and detail pane build unconditionally, and only an empty-state label
  (`.mark("corpus-empty")`) toggles `.visible` — so a page opened moments into a
  fresh run still has a live table that fills itself in once analysis produces
  records, instead of being stuck on the empty state. A refresh button
  (`.mark("btn-corpus-refresh")`) and a `ui.timer(web_session.CORPUS_POLL_INTERVAL,
  _reload)` (X10, 5.0s default, mirroring `TREE_POLL_INTERVAL`'s test-seam
  pattern) both call the same `_reload()`. Otherwise: a
  search input (`.mark("corpus-search")`) filtering rows Python-side against
  each record's full, untruncated title/type/summary text (not the truncated
  preview shown in the table) beside a `ui.table` (`.mark("corpus-table")`,
  `row_key="checksum"`, `pagination=10`, `.classes("cursor-pointer")` as of
  X12) with sortable
  title/type/date/status/summary/entities columns — client-side Quasar sort, no
  Python sort logic. Row values are pre-flattened to plain strings: `ui.table`
  crashes the browser on list-valued cells, so the registry's `entities` list
  becomes a short joined preview (up to 3 names, `"+N"` for the rest) for the
  table row, while the full list still lives in the record dict for the detail
  pane. The table has no `selection=` prop or checkbox column at
  all — clicking anywhere on a row (`table.on("rowClick", _on_row_click)`,
  defensively unpacking Quasar's `[evt, row, index]` payload rather than using
  NiceGUI's typed `TableSelectionEventArguments`, the prior V5 mechanism)
  reveals a
  detail pane (`.mark("corpus-detail")`) beside the table with the full
  summary, provenance, a new **Location** line (X10, `.mark(
  "corpus-detail-location")`, the record's path relativized to `session.target`,
  falling back to the raw path on `ValueError`/`OSError`), and every entity as
  its own line (`name — role (kind)`,
  defensively `.get()`-read since older records may carry incomplete entity
  dicts). As of X10, a checksum no longer present in `state.records_by_checksum`
  after a reload (the document was quarantined/archived) collapses the pane back
  to its placeholder instead of showing stale content. Merges the former V4
  (document preview) concept into this one
  screen. `_reload()` (X10, async, wired to both the button and the timer)
  mirrors `Shell.reload_tree`'s two disciplines — a re-entrancy flag, and a
  `registry_mtime` pre-check that skips the real reload when unchanged — then
  rebuilds the row/detail data, re-applies the live search filter, and
  refreshes the open detail pane if one is showing. Every value rendered here is LLM-derived output from
  attacker-controllable documents — `ui.label`/`ui.table` row values only,
  never `ui.markdown`/`ui.html`/`ui.code` — the same rule V13b's step-detail
  view and V11's prompt inspection already follow; see
  [Security Model](../security-model.md).
- `host/web/docpane.py` (X9, new file) — the run screen's document-preview
  pane, mirroring `corpus_view.py`'s detail pane field-for-field but
  deliberately duplicated rather than shared this sprint. `build_doc_pane()`
  returns a `DocPane` (title/meta/summary/provenance/entities widget handles)
  with `.show(record)` (populate from a registry record), `.show_unanalyzed(
  path, meta_line)` (filename + filesystem metadata only, no extraction, for a
  file with no registry record yet), and `.clear()` (collapse to placeholder).
  Same untrusted-content rule as `corpus_view.py`: `ui.label` only.
- `host/web/graph.py` (Y1, new file) — knowledge-graph load/projection logic,
  `nicegui`-free, mirroring `host/web/corpus.py`'s contract exactly: this
  module owns the filesystem-adjacent logic, `host/web/graph_view.py` owns the
  rendering. `load_graph(target) -> dict | None` always builds the graph
  fresh, in-process, from the registry + event journal via `server.graph.build`
  — deliberately never reading the persisted `.organizer/graph.json`, which
  only exists once a run reaches its final write-outputs step and goes stale
  the moment a single document is (re-)recorded afterward; `server.graph.build`
  is a documented pure function of the same two stores this module already
  polls, so rebuilding fresh costs nothing extra. `graph_mtime(target) ->
  tuple[float, int, float, int] | None` is the poll pre-check — combined
  `(mtime, size)` of both `registry.json` and `events.jsonl` — mirroring
  `corpus.py`'s `registry_mtime`. `rank_actors_for(target, cap) -> list[dict]`
  wraps `server.graph.rank_actors` for the ranked-actors table.
  `project(graph, *, kinds, top_actors) -> tuple[list[dict], list[dict]]` is a
  pure, unit-tested filter/cap function for the optional force-directed panel:
  keeps only nodes whose `kind` is in `kinds`, caps entity nodes to the top
  `top_actors` by the same three-component centrality ordering
  `server.graph.rank_actors` uses (document count, then co-occurrence weight,
  then mention count), and keeps only edges whose both endpoints survived.
  `neighbors(graph, node_id) -> list[dict]` returns a node's immediate
  neighbors (either edge direction), feeding the detail pane's "referencing
  documents"/"mentioned entities" lists. Same defensive contract as
  `corpus.py`: never raises, `None`/`[]` on any error.
- `host/web/graph_view.py` (Y1, new file) — the knowledge-graph page's UI,
  mirroring `corpus_view.py`'s structure. `build_graph_view(session)` renders
  a sortable ranked-actors `ui.table` (`.mark("graph-table")`) as the primary
  surface — clicking a row opens the same kind of detail pane `corpus_view.py`
  uses, reusing `host/web/docpane.py`'s `build_doc_pane(marker_prefix="graph")`
  unmodified for document nodes, with new sibling panels
  (`.mark("graph-entity-detail")`/`.mark("graph-event-detail")`) for entity and
  event nodes. An optional force-directed `ui.echart` panel
  (`.mark("graph-echart")`) sits behind a "Show force-directed view" toggle,
  default off — confirmed to use NiceGUI's fully locally-vendored echarts
  bundle, no CDN fetch, consistent with telcontar's offline-first design — with
  its tooltip disabled entirely (`tooltip: {"show": False}`) rather than an
  HTML `tooltip.formatter` over untrusted node names/text, since every detail
  already surfaces through the Python-side pane. Three kind filters — document
  and entity are always on; a third, event, defaults **off**, since
  `server/graph.py`'s event↔entity matching is a known naive-substring
  approximation that produces false-positive edges for short entity names — and
  a "Top actors" `ui.select` (25/50/100, default 50) cap both the table and the
  panel. A `ui.timer(web_session.GRAPH_POLL_INTERVAL, _reload)` (Y1, 5.0s
  default, same test-seam pattern as `TREE_POLL_INTERVAL`/`CORPUS_POLL_INTERVAL`)
  mirrors `corpus_view.py::_reload`'s two disciplines — a re-entrancy flag and
  a `graph_mtime` pre-check that skips the real reload when unchanged. Every
  graph value rendered here is LLM-derived output from attacker-controllable
  documents (entity names, document titles, event sentences): `ui.label`/
  `ui.table` row values only, never `ui.markdown`/`ui.html`/`ui.code` — the
  same rule `corpus_view.py` follows, predating and unrelated to Y7's
  chat-message markdown exception (`host/web/chat.py`); see [Security
  Model](../security-model.md).

- **Sessions (Y2, GH #53):** a home-directory index plus per-target snapshots
  give every session — organize or query — a life beyond one browser tab and
  one process: it can be listed, re-opened while still live, or resumed from
  disk after telcontar itself was restarted. Two tiers, deliberately split by
  trust boundary: the **home-directory index**
  (`~/.telcontar/sessions.json`, `config.settings.user_sessions_index_path()`)
  is metadata only — `run_id`/`target`/`mode`/`created_at`/`last_active_at`/
  `status` — and lives *outside* every allowlist/egress boundary the rest of
  the security model reasons about (it's not under any target directory), so
  it must never carry corpus-derived text; the **per-target snapshot**
  (`<target>/.organizer/sessions/<run_id>.json`, `Settings.sessions_dir`)
  holds the actual transcript, activity log, and full LLM message history —
  all derived from the user's own documents — and stays inside the same
  boundary the registry/journal/graph already trust. This split is why
  `host/web/sessions.py`'s `record_started()` only ever touches the index,
  while `snapshot()` writes both: the index entry is cheap and always-current
  (so a session shows up on the list immediately), the snapshot is the actual
  restorable state. `AgentBridge`/`QueryBridge` (`host/web/bridge.py`) each
  grew a `_checkpoint(*, terminal: bool)` method called from their existing
  `on_event` handler — unconditional on a terminal `done`/`error` event,
  throttled to at most once every `_CHECKPOINT_INTERVAL_SECS = 10.0` seconds
  otherwise, since `on_event` fires synchronously on the event loop on every
  tool call/result, far more often than a snapshot needs to be current. The
  app's shutdown hook (`run_web`, `host/web/main.py`) also does one
  unconditional final `sessions_store.snapshot(session)` per session before
  cancelling its driving task, so a graceful quit never loses the tail end of
  activity a throttled write hasn't flushed yet. `/sessions` (`sessions_page`)
  lists every known session grouped by target — live ones (cross-checked
  against `host/web/session.py`'s in-memory `_SESSIONS` registry) link
  straight to `/run/{run_id}` or `/query/{run_id}`; dead ones link to
  `/sessions/{run_id}`, a read-only transcript replay with a "Resume" button;
  both routes pass `active="sessions"` to `app_shell`. Clicking "Resume"
  (`host/web/sessions_view.py`'s `build_session_detail_view`) loads the
  snapshot, rebuilds a `RunSession` via `sessions.restore_session()` —
  keeping the persisted `run_id` so existing links keep working — registers
  it into the live registry (`host/web/session.py`'s new `register()`,
  mirroring `create()`'s registration for a fresh session but preserving the
  restored id instead of minting one), and re-enters the appropriate bridge.
  For query mode, resume needed zero method changes: `QueryBridge.run()`'s
  loop was already shaped to wait on the message queue from the very start
  regardless of whether history was pre-populated. For organize mode, a new
  `AgentBridge.start_resumed()` / `run(..., resume_history=...)` parameter
  skips the fresh-run bootstrap (pre-pass/analysis/digest) entirely and seeds
  `session.history` directly, landing in the exact same history-continuation
  code path a live session already uses between chat turns — so a resumed
  conversation is indistinguishable in mechanism from a live one waiting for
  its next message. No special-cased approval/ask-user handling was needed:
  the restored session's callbacks are the same live callbacks any run uses,
  and `host/agent.py` already has a documented guard (`_seed_last_plan_id`,
  its own docstring labels it "O7", literally for "a resumed conversation")
  for history that ends with a plan never actually presented for approval.
  Live re-attachment (a still-running session, opened again after a page
  reload) needed no new work at all — `/run/{run_id}` already re-attached
  correctly to an in-process session before Y2. Unlike Query/Graph, the
  "Sessions" nav tab (`host/web/shell.py`) has no target-scoped disable
  condition — it's always enabled, since a session can belong to any target,
  not just whichever one the current route happens to be showing.
- `host/web/main.py` now mounts `app_shell(...)` at the top of both page bodies
  instead of assembling its own layout. The landing page (`/`) first checks
  `config.settings.is_configured()`: if telcontar hasn't been set up yet, it
  navigates to `/setup` (the wizard above) instead of showing any picker. `/settings`
  (the settings view above, U3) is registered the same thin-shell way, reachable
  from the sidebar's Settings button on every route. As of U7, `/query/{run_id}`
  is registered the same thin-shell way too — a page that looks up the query-mode
  `RunSession`, starts `QueryBridge(session)` on first mount if not already started
  (TUI parity: `QueryScreen.on_mount` also auto-starts its worker, no explicit
  "start" button), and delegates rendering to `build_query_view`. As of V5,
  `/corpus/{run_id}` is registered the same thin-shell way too: it looks up the
  `RunSession` (same not-found handling as the other two run-scoped routes) and
  delegates to `build_corpus_view(session)` — no bridge, no MCP session, no
  agent turn, since it reads the registry directly (`host/web/corpus.py`)
  rather than through the model. It reuses the *same* session/run_id the
  organize run already created rather than minting a new one, since the corpus
  page only ever reads `session.target`. As of Y1, `/graph/{run_id}` is
  registered the exact same thin-shell way, mirroring `/corpus/{run_id}`
  precisely: same not-found handling, no bridge/MCP session/agent turn (it
  reads the registry + event journal directly via `host/web/graph.py`), the
  same reused session/run_id, and `active="graph"` passed to `app_shell` for
  the nav-tab highlight. As of Y2, `/sessions` and `/sessions/{run_id}` are
  registered the same thin-shell way too, both passing `active="sessions"` —
  `/sessions` delegates to `build_sessions_view()` (no `run_id`, no session
  lookup at all: it lists across every target from the home-directory index);
  `/sessions/{run_id}` delegates to `build_session_detail_view(run_id)`,
  which does its own `run_id` validation and live/dead/not-found handling
  rather than the not-found pattern the other run-scoped routes share, since
  a session id in this view is deliberately allowed to resolve to a *dead*
  session (the whole point of the page) rather than always requiring one
  already in the live registry. Once configured, folder selection is the
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
  `/corpus/{session.run_id}`: the same session, not a new one. As of Y1, a
  "Knowledge graph" button (`.mark("btn-graph")`, icon `hub`) sits beside that,
  gated and set the same `session.done` way, navigating to
  `/graph/{session.run_id}` — again the same session, not a new one, since the
  graph page only ever reads `session.target`. As of X9, the
  main view gained a document-preview pane — polling `shell.selected`
  (the same attribute the sidebar tree click handler, X11, already populates)
  rather than being wired through the tree directly: `_refresh()` reacts only
  when the selection actually changes since the last tick, offloading the
  stat/registry lookup (`_load_preview`, new module-level helper) via
  `run.io_bound` and showing either the matching registry record
  (`host/web/corpus.py`'s `find_by_path`), a "not analyzed yet" placeholder
  with filesystem metadata, or clearing the pane, depending on what's found.
  Originally (X9) this pane lived beside the conversation area, in a `ui.row()`
  splitting it 2/3 conversation / 1/3 doc-preview (`.mark("doc-preview")`,
  built via `host/web/docpane.py`'s `build_doc_pane()`, above). As of Y9 (GH
  #58), that column is gone: the conversation area is full-width again, and
  the doc-preview pane instead lives in the shared left-drawer inspector
  (see `host/web/shell.py`'s `Shell`, below) — stacked below the internal-step
  detail section (V13b), in its own scroll region, mutually exclusive with it
  (`Shell.show_document`/`show_unanalyzed`/`clear_document` now hide step
  detail the same way `Shell.show_detail` hides the doc pane). `_refresh()`
  still does the same polling/offloading described above; it now calls those
  `Shell` methods instead of driving a locally-built `DocPane` handle
  directly. `host/web/docpane.py` itself — the `DocPane` dataclass and
  `build_doc_pane()` — is unchanged; only where it is mounted and who calls
  its `show`/`show_unanalyzed`/`clear` moved.
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
  telcontar), fixing low-contrast white-on-gold bubble text. As of Y7 (GH #56),
  the bubble-rendering call itself — previously `ui.chat_message(item.text,
  ...)`, plain HTML-escaped text — moved into a new shared module,
  `host/web/chat.py::render_turn_bubble(item)`, called identically from both
  `run_page`'s `_render_turn` and `query_view.py`'s `_render_turn` (which had
  duplicated this same rendering code since V13a — Y7 explicitly reverses that
  earlier "duplicate, don't share" call now that the code is
  security-relevant: a single unsanitized copy would defeat the point of
  unifying it). The bubble content itself now renders via a nested
  `ui.markdown(item.text, sanitize=True)` instead of the plain chat-message
  text, so prompts and LLM output display as formatted markdown (bold, links,
  lists, code blocks). This is the one deliberate exception to telcontar's
  "never render corpus-derived text as markup" rule described in [Security
  Model](../security-model.md) — `sanitize=True` runs the output through a
  client-side, vendored DOMPurify before it reaches the DOM, and the CSP
  header (`_AuthMiddleware`, `host/web/main.py`) gained `img-src 'self'
  data:` to close the one gap DOMPurify alone leaves open (a sanitize-surviving
  markdown image tag beaconing to a remote host). As of U4 this rendering is
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
  since the last tick (see `bridge.py`'s `_TREE_MUTATING_TOOLS` above), never on
  every 0.5s `REFRESH_INTERVAL` tick — but the tree no longer depends on that path
  alone to stay current: `app_shell`'s own `TREE_POLL_INTERVAL` timer and the
  sidebar's manual refresh button both call `reload_tree()` independently of
  `fs_revision` and of any run being active at all. `reload_tree()`'s own
  skip-if-unchanged check is what keeps all of this from colliding or thrashing
  the DOM, and it never collapses whatever the user had expanded.

  `_pick_port()` binds an ephemeral `127.0.0.1` port. As of V1, `run_web(target:
  Path | None = None, *, native: bool = True)` gained the keyword-only `native`
  parameter (default `True` — "one command, one window"); `host/main.py` passes
  `native=not args.browser`. Rather than trust the argument blindly, `run_web`
  re-derives `effective_native = native and sys.platform == "win32" and
  importlib.util.find_spec("webview") is not None` — `pywebview` is a Windows-only
  dependency (`pyproject.toml`'s `; sys_platform == 'win32'` marker) and may still
  be missing even on Windows. If native was requested but isn't actually usable, a
  warning is printed to stderr and the call falls back to the browser rather than
  hard-exiting — NiceGUI's own native-mode path calls `sys.exit(1)` on a missing
  `webview`, unacceptable now that this is the default entry point (U10). `run_web`
  calls `ui.run(host="127.0.0.1", port=port, show=False if effective_native else
  True, reload=False, dark=True, favicon=..., native=effective_native,
  window_size=(1280, 860) if effective_native else None)`. `favicon=` is
  `str(_ICON_PATH)` — the vendored `host/web/assets/telcontar.ico` — when
  `effective_native` and that file exists, else `theme.FAVICON_SVG` unchanged;
  NiceGUI's `favicon=` kwarg is dual-purpose, also applying a local file path as
  the native window/taskbar icon in native mode (there is no separate "icon"
  kwarg), while the browser-mode favicon is untouched. `reload=False` is required,
  not stylistic:
  `reload=True` forces uvicorn onto a `SelectorEventLoop` on Windows, where
  `asyncio.create_subprocess_exec` (the MCP server subprocess launch) raises
  `NotImplementedError`. `dark=True` is likewise load-bearing (T8): Quasar only
  honours the `dark`/`dark_page` palette tokens in dark mode. Before `ui.run()`,
  `run_web` applies the visual identity globally and exactly once: `app.colors(
  **theme.PALETTE)` (never a per-page `ui.colors()`, which would silently
  override this and fragment the identity across routes), `app.add_static_files(
  theme.FONT_URL_PATH, theme.FONT_DIR)` when the vendored-fonts directory exists,
  and `ui.add_css(theme.css(), shared=True)`. As of U7, `run_web`'s
  `@app.on_shutdown` hook also calls `session.task.cancel()` for every session,
  alongside its existing pending-future rejection — an organize or query session's
  MCP server subprocess previously had no lifecycle at all past shutdown, since
  nothing ever calls `web_session.close()`. A full lifecycle/reaper is still future
  work; this is minimal hardening only.

  In native mode, `run_web` points the native window at the running server via
  `app.native.window_args.update(_native_window_args(f"http://127.0.0.1:{port}/
  ?token={token}"))` — as of X2, no longer a direct `app.native.window_args["url"]
  = ...` dict-key assignment. `_native_window_args(url: str) -> dict` is a pure
  helper (`{"url": url, "text_select": True}`) split out so its content is
  unit-testable on Linux CI, where native mode never actually activates.
  `text_select=True` is the **primary fix** for a live bug report that chat text
  (and everything else in the native window) wasn't selectable/copyable:
  pywebview's own default is `text_select=False` for the whole native window,
  independent of any in-page CSS — the sidebar-resize drag bug fixed the same
  sprint (`_RESIZE_JS`, `host/web/shell.py`, above) was the originally-suspected
  cause but turned out to be secondary. No automated coverage exercises the real
  native window end to end (CI has no `webview`, and the headless test harness
  never executes JavaScript) — coverage is limited to a dict-content assertion on
  `_native_window_args`, string-content assertions on `_RESIZE_JS`, and a
  CSS-string assertion on `theme.css()`'s new rule (below); manual verification
  in a real native window is still required and is a known gap, not a silent
  omission.

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

---

For the per-file module reference of everything in this section, see [Module Reference — Web UI](../modules/web-ui.md). For the rest of the architecture (components, core design decisions, data flow), see [Architecture](core.md).
