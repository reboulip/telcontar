"""Tool implementations — called by the MCP server handlers in main.py."""
from __future__ import annotations


def list_dir(path: str) -> dict:
    raise NotImplementedError


def read_file(path: str, max_chars: int) -> str:
    raise NotImplementedError


def extract_text(path: str, max_chars: int) -> str:
    raise NotImplementedError


def propose_rename(path: str, new_name: str) -> str:
    raise NotImplementedError


def propose_move(path: str, dest_dir: str) -> str:
    raise NotImplementedError


def propose_quarantine(path: str) -> str:
    raise NotImplementedError


def execute_plan(plan_id: str) -> dict:
    raise NotImplementedError


def write_index(path: str) -> str:
    raise NotImplementedError


def write_summary(path: str) -> str:
    raise NotImplementedError


def undo_last() -> dict:
    raise NotImplementedError
