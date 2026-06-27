# config — API Reference

Environment-based configuration for the entire telcontar stack. Both the server and the host load settings through this module.

---

## config.settings

Pydantic Settings class that reads from `.env` (or real environment variables). Call `config.settings.load()` to get a validated `Settings` instance.

::: config.settings
