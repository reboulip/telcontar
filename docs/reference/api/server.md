# server — API Reference

Python API for the MCP server package. The server is normally launched as a subprocess by the host, but all business logic is importable for testing and scripting.

---

## server.tools

Tool implementations — pure functions called by the MCP server handlers in `server/main.py`. Each function maps directly to an MCP tool of the same name.

::: server.tools

---

## server.plan

Plan data model and disk persistence.

::: server.plan

---

## server.registry

Document registry — the engine's persistent, content-addressed memory.

::: server.registry

---

## server.profile

Domain profile loader — reads a `.toml` file and exposes typed accessors.

::: server.profile

---

## server.guards

Collision, overwrite, and quarantine guardrails.

::: server.guards

---

## server.journal

Append-only undo journal (JSONL).

::: server.journal

---

## server.extract

Text extraction from binary formats via markitdown.

::: server.extract
