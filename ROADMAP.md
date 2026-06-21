# Roadmap

## v0.1.0 — Skeleton

- [ ] A1 · Project layout — create `host/`, `server/`, `config/` package stubs with `__init__.py`
- [ ] A2 · Config layer — implement `config/settings.py` with pydantic-settings and `.env` loading; validate all required env vars at startup
- [ ] A3 · MCP server skeleton — `server/main.py` entrypoint (stdio), empty tool stubs matching the CLAUDE.md tool list
- [ ] A4 · MCP host skeleton — `host/main.py` agent loop, `host/llm.py` openai SDK wrapper (Azure/Mammouth via `base_url`)
