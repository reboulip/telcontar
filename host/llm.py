"""OpenAI-compatible client factory (Azure / Mammouth via base_url override)."""
from __future__ import annotations

from openai import AsyncOpenAI

from config.settings import Settings


def make_client(settings: Settings) -> AsyncOpenAI:
    kwargs: dict = {
        "api_key": settings.llm_api_key,
        "base_url": settings.llm_base_url,
    }
    if settings.llm_api_version:
        # Azure requires the api-version query parameter on every request.
        kwargs["default_query"] = {"api-version": settings.llm_api_version}
    return AsyncOpenAI(**kwargs)
