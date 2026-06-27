# host — API Reference

Python API for the MCP host package. The host orchestrates the GPT-5 agent loop and presents the Textual TUI.

---

## host.agent

Async agent loop — MCP client, GPT-5 tool-calling loop, and approval callback protocol.

The module is fully decoupled from Textual so it can be tested with plain `pytest-asyncio` tests. Callers supply async callbacks for events and approval.

::: host.agent

---

## host.app

Textual TUI screens and widgets.

::: host.app

---

## host.llm

OpenAI-compatible client factory supporting Azure and Mammouth via `base_url` override.

::: host.llm
