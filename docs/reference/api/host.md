# host — API Reference

Python API for the MCP host package. The host orchestrates the agent loop and presents the web UI.

---

## host.agent

Async agent loop — MCP client, LLM tool-calling loop, and approval callback protocol.

The module is fully decoupled from any UI framework so it can be tested with plain `pytest-asyncio` tests. Callers supply async callbacks for events and approval.

::: host.agent

---

## host.llm

OpenAI-compatible client factory supporting any endpoint via `base_url` override.

::: host.llm

---

## host.llmlog

Always-on, redacted debug log of outbound LLM HTTP calls (request/response metadata only, no message content or credentials).

::: host.llmlog
