"""Per-step token-usage profiling log (R2, GH #27).

Append-only JSONL at ``token_log_path`` (default ``.organizer/tokens.jsonl``);
each entry records one LLM call's token usage — phase, step, per-call and
running totals — so a run's actual token spend can be analyzed after the
fact. Distinct from the egress log (S8, which records document *content*
sent, not token counts) and the undo journal.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TokenLogEntry:
    """One LLM call's token usage."""

    ts: str
    run_id: str
    phase: str
    step: int
    call: int
    model: str
    docs: int | None
    in_: int
    cached_in: int
    out: int
    est_in: int | None
    total_in: int
    total_out: int
    duration_ms: int | None = None

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        phase: str,
        step: int,
        call: int,
        model: str,
        docs: int | None,
        in_: int,
        cached_in: int,
        out: int,
        est_in: int | None,
        total_in: int,
        total_out: int,
        duration_ms: int | None = None,
    ) -> "TokenLogEntry":
        return cls(
            ts=datetime.now(timezone.utc).isoformat(),
            run_id=run_id,
            phase=phase,
            step=step,
            call=call,
            model=model,
            docs=docs,
            in_=in_,
            cached_in=cached_in,
            out=out,
            est_in=est_in,
            total_in=total_in,
            total_out=total_out,
            duration_ms=duration_ms,
        )

    def to_dict(self) -> dict:
        # `in` is a Python keyword, so the field is `in_` internally and
        # renamed to `in` only at serialization time.
        d = asdict(self)
        d["in"] = d.pop("in_")
        return d


def append(token_log_path: Path, entry: TokenLogEntry) -> None:
    """Append a single token-log entry; creates parent dirs if needed."""
    token_log_path.parent.mkdir(parents=True, exist_ok=True)
    with token_log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")


def all_entries(token_log_path: Path) -> list[dict]:
    """Return all token-log entries in chronological order; empty if no file."""
    if not token_log_path.is_file():
        return []
    return [
        json.loads(ln)
        for ln in token_log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
