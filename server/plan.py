"""Plan data model and disk persistence."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

PlanState = Literal["pending", "approved", "executing", "done", "failed", "stopped"]
OpType = Literal[
    "rename",
    "move",
    "quarantine",
    "create_file",
    "update_file",
    "create_dir",
    "archive_document",
    "compress_quarantine",
]
OpStatus = Literal["pending", "completed", "failed"]

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "stopped"},
    "approved": {"executing", "stopped"},
    "executing": {"done", "failed", "stopped"},
    "done": set(),
    "failed": set(),
    "stopped": set(),
}


@dataclass
class PlanOp:
    op_id: str
    op_type: OpType
    src: str
    dst: str  # new_name for rename; dest dir for move; full quarantine path for quarantine
    status: OpStatus = "pending"
    error: str | None = None
    retries: int = 0
    # Op-specific data that doesn't fit src/dst (e.g. {"content": ...} for
    # create_file/update_file, {"checksum": ..., "reason": ...} for
    # archive_document, {"delete_originals": ...} for compress_quarantine).
    params: dict[str, Any] | None = None

    @classmethod
    def new(
        cls, op_type: OpType, src: str, dst: str, params: dict[str, Any] | None = None
    ) -> "PlanOp":
        return cls(op_id=str(uuid.uuid4()), op_type=op_type, src=src, dst=dst, params=params)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanOp":
        return cls(
            op_id=d["op_id"],
            op_type=d["op_type"],
            src=d["src"],
            dst=d["dst"],
            status=d.get("status", "pending"),
            error=d.get("error"),
            retries=d.get("retries", 0),
            params=d.get("params"),
        )


@dataclass
class Plan:
    plan_id: str
    state: PlanState
    ops: list[PlanOp]
    created_at: str
    updated_at: str
    rationale: str = ""  # agent's plain-language explanation of the plan (F8)
    # Agent-supplied per-folder purpose notes for the approval-view target-layout
    # preview (L5): maps a target folder path to a short one-line purpose note.
    folder_notes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(cls) -> "Plan":
        now = _now()
        return cls(
            plan_id=str(uuid.uuid4()),
            state="pending",
            ops=[],
            created_at=now,
            updated_at=now,
        )

    def add_op(self, op: PlanOp) -> None:
        self.ops.append(op)
        self.updated_at = _now()

    def transition(self, new_state: PlanState) -> None:
        allowed = _VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            terminal = "none (terminal state)" if not allowed else str(sorted(allowed))
            raise ValueError(
                f"Invalid plan state transition: {self.state!r} → {new_state!r}. "
                f"Allowed from {self.state!r}: {terminal}"
            )
        self.state = new_state
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "state": self.state,
            "ops": [asdict(op) for op in self.ops],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "rationale": self.rationale,
            "folder_notes": self.folder_notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(
            plan_id=d["plan_id"],
            state=d["state"],
            ops=[PlanOp.from_dict(op) for op in d["ops"]],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            rationale=d.get("rationale", ""),
            folder_notes=d.get("folder_notes", {}),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(plan: Plan, plans_dir: Path) -> None:
    """Write plan to {plans_dir}/{plan_id}.json."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / f"{plan.plan_id}.json").write_text(
        json.dumps(plan.to_dict(), indent=2), encoding="utf-8"
    )


def load(plan_id: str, plans_dir: Path) -> Plan:
    """Load plan from disk; raises FileNotFoundError if not found."""
    path = plans_dir / f"{plan_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Plan not found: {plan_id}")
    return Plan.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_all(plans_dir: Path) -> list[Plan]:
    """Return all plans in plans_dir sorted by created_at; skips corrupted files."""
    if not plans_dir.is_dir():
        return []
    plans: list[Plan] = []
    for path in plans_dir.glob("*.json"):
        try:
            plans.append(Plan.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError):
            pass
    return sorted(plans, key=lambda p: p.created_at)
