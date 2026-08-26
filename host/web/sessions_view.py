"""Sessions list + read-only transcript view (Y2) — lists every known
session (metadata from the home-directory index, merged with whichever are
still live in host/web/session.py's in-memory registry), grouped by target,
and renders a read-only transcript for a dead session with a Resume action.

Every transcript/activity value rendered here is LLM-derived output from
attacker-controllable documents — `ui.label` only, never
`ui.markdown`/`ui.html`/`ui.code`. Y7's chat-message markdown exception
(host/web/chat.py) is scoped to the live conversation view only; this
read-only replay does not extend it — a persisted snapshot is exactly the
kind of at-rest artifact that shouldn't grow new interpreted-markup
surfaces.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import run, ui

from host.web import session as web_session
from host.web import sessions as sessions_store


def _target_label(target_str: str) -> str:
    return Path(target_str).name or target_str


async def build_sessions_view() -> None:
    entries = await run.io_bound(sessions_store.list_index) or []
    live_run_ids = {s.run_id for s in web_session.all_sessions()}

    ui.label("Sessions").classes("text-h5")

    if not entries:
        ui.label("No sessions yet.").classes("text-caption").mark("sessions-empty")
        return

    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(entry.get("target", ""), []).append(entry)

    for target_str, group in groups.items():
        with ui.column().classes("w-full gap-1 q-mb-md").mark("sessions-group"):
            ui.label(_target_label(target_str)).classes("text-subtitle1")
            for entry in group:
                run_id = entry.get("run_id", "")
                is_live = run_id in live_run_ids
                status = "live" if is_live else entry.get("status", "unknown")
                with ui.row().classes("w-full items-center gap-2").mark("session-row"):
                    ui.label(
                        f"{entry.get('mode', 'organize')} · {status} · "
                        f"{entry.get('last_active_at', '')}"
                    ).classes("text-caption")
                    if is_live:
                        href = (
                            f"/query/{run_id}" if entry.get("mode") == "query" else f"/run/{run_id}"
                        )
                        ui.button("Open", on_click=lambda h=href: ui.navigate.to(h)).props(
                            "flat dense"
                        ).mark("btn-session-open")
                    else:
                        ui.button(
                            "View",
                            on_click=lambda rid=run_id: ui.navigate.to(f"/sessions/{rid}"),
                        ).props("flat dense").mark("btn-session-view")


async def build_session_detail_view(run_id: str) -> None:
    if not sessions_store.is_valid_run_id(run_id):
        ui.label("Invalid session id.").classes("text-negative").mark("sessions-invalid")
        return

    live = web_session.get(run_id)
    if live is not None:
        ui.label("This session is still live.").classes("text-caption")
        href = f"/query/{run_id}" if live.mode == "query" else f"/run/{run_id}"
        ui.button("Open", on_click=lambda: ui.navigate.to(href)).mark("btn-session-open-live")
        return

    entries = await run.io_bound(sessions_store.list_index) or []
    entry = next((e for e in entries if e.get("run_id") == run_id), None)
    if entry is None:
        ui.label("Session not found.").classes("text-negative").mark("sessions-not-found")
        return

    target = Path(entry["target"])
    data = await run.io_bound(sessions_store.load_snapshot, run_id, target)
    if data is None:
        ui.label("Session data could not be read.").classes("text-negative").mark(
            "sessions-unreadable"
        )
        return

    ui.label(f"Session — {_target_label(entry['target'])}").classes("text-h5")
    ui.label(f"{entry.get('mode', 'organize')} · {entry.get('status', 'unknown')}").classes(
        "text-caption"
    )

    with ui.column().classes("w-full gap-1").mark("sessions-transcript"):
        transcript = data.get("transcript") or []
        activity_log = data.get("activity_log") or []
        merged = sorted([*transcript, *activity_log], key=lambda item: item.get("seq", 0))
        if not merged:
            ui.label("No conversation recorded.").classes("text-caption")
        for item in merged:
            if "speaker" in item:
                ui.label(f"{item['speaker']}: {item['text']}").mark("sessions-turn")
            else:
                ui.label(item.get("text", "")).classes("text-caption").mark("sessions-activity")

    def _resume() -> None:
        restored = sessions_store.restore_session(data)
        web_session.register(restored)
        from host.web.bridge import AgentBridge, QueryBridge

        if restored.mode == "query":
            QueryBridge(restored).start()
            ui.navigate.to(f"/query/{run_id}")
        else:
            AgentBridge(restored).start_resumed()
            ui.navigate.to(f"/run/{run_id}")

    ui.button("Resume", on_click=_resume, color="primary").mark("btn-session-resume")
