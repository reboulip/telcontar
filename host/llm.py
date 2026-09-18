"""OpenAI-compatible client factory for any endpoint via base_url override.

Also branches to Azure OpenAI's own client class when the configured endpoint
looks like Azure — a plain AsyncOpenAI sends `Authorization: Bearer <key>` and
whatever path the SDK builds from `base_url`, neither of which Azure accepts
outside its `/openai/v1` compatibility surface (see issue #61 for a real
misconfiguration walkthrough: a full operation URL with an embedded dated
`api-version` broke both request signing and the deployment route).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from openai import AsyncAzureOpenAI, AsyncOpenAI

from config.settings import Settings
from host import llmlog
from host.configflow import AZURE_API_VERSION


@dataclass(frozen=True)
class AzureTarget:
    endpoint: str  # resource root, no path — what AsyncAzureOpenAI expects
    deployment: str
    api_version: str


def resolve_azure_target(base_url: str, model: str, api_version: str) -> AzureTarget | None:
    """Return an AzureTarget when ``base_url`` looks like Azure OpenAI, else
    None (meaning: use the plain AsyncOpenAI path).

    Azure is detected by an explicit ``api_version`` OR an ``*.azure.com``
    host — EXCEPT when the path ends in ``/openai/v1``, Azure's newer,
    genuinely OpenAI-compatible surface: that combination (a dated
    api-version alongside `/openai/v1`) is exactly what produced "API version
    not supported" for a real user (issue #61) — `/openai/v1` must never get
    an api-version query injected, so it always takes the plain client path.
    """
    if not base_url:
        return None

    parts = urlsplit(base_url)
    host = parts.hostname or ""
    path = parts.path.rstrip("/")

    if path.endswith("/openai/v1"):
        return None

    is_azure = bool(api_version) or host.endswith(".azure.com")
    if not is_azure:
        return None

    deployment = model
    if "/deployments/" in path:
        # .../openai/deployments/<name> -> resource root + captured <name>.
        # The URL's own deployment segment wins over LLM_MODEL when they
        # disagree — every working Azure install today has it embedded there
        # exactly as the setup wizard instructs.
        prefix, _, rest = path.partition("/deployments/")
        deployment = rest.split("/")[0] or model
        path = prefix[: -len("/openai")] if prefix.endswith("/openai") else prefix
    elif path.endswith("/openai"):
        path = path[: -len("/openai")]

    endpoint = (
        urlunsplit((parts.scheme, parts.netloc, path, "", "")) or f"{parts.scheme}://{parts.netloc}"
    )
    resolved_version = api_version or AZURE_API_VERSION
    return AzureTarget(endpoint=endpoint, deployment=deployment, api_version=resolved_version)


def make_client(settings: Settings) -> AsyncOpenAI:
    http_client = llmlog.build_http_client(settings.llm_debug_log_path)
    target = resolve_azure_target(
        settings.llm_base_url, settings.llm_model, settings.llm_api_version
    )

    if target is not None:
        client: AsyncOpenAI = AsyncAzureOpenAI(
            api_key=settings.llm_api_key,
            azure_endpoint=target.endpoint,
            azure_deployment=target.deployment,
            api_version=target.api_version,
            http_client=http_client,
            max_retries=_MAX_RETRIES,
        )
        llmlog.log_client(
            settings.llm_debug_log_path,
            client_class="AsyncAzureOpenAI",
            endpoint=target.endpoint,
            api_version=target.api_version,
            deployment=target.deployment,
            model=settings.llm_model,
            auth_header="api-key",
        )
    else:
        client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            http_client=http_client,
            max_retries=_MAX_RETRIES,
        )
        llmlog.log_client(
            settings.llm_debug_log_path,
            client_class="AsyncOpenAI",
            endpoint=settings.llm_base_url,
            api_version="",
            deployment="",
            model=settings.llm_model,
            auth_header="Authorization",
        )

    return client


# ── Mid-session config reload (Z2, #67) ─────────────────────────────────────
#
# A bad LLM config (wrong model name, wrong key, transient 429s) shouldn't
# require a full telcontar restart to recover from. ReloadingClient wraps the
# client returned by make_client and, on every call, checks whether the
# on-disk config has changed since it was built (via config.settings's
# process-global revision counter) — and if so, reloads Settings, rebuilds
# the client, and reports what changed. Only the LLM endpoint fields hot-swap
# (_RELOADABLE_FIELDS below); approval_mode and every path field stay pinned
# for the session's lifetime, so a live run's already-consented-to approval
# gate can never be silently relaxed by an unrelated settings save.

_MAX_RETRIES = 4

_RELOADABLE_FIELDS = ("llm_base_url", "llm_api_key", "llm_model", "llm_api_version")


class ReloadingClient:
    """Duck-types as an ``AsyncOpenAI`` for the one surface ``host.agent``
    actually calls — ``.chat.completions.create(**kwargs)`` — delegating to
    whichever underlying client is current, rebuilding it transparently when
    the config has changed since the last call.

    Mutates the SAME ``Settings`` instance passed in, in place, rather than
    swapping to a new object: the agent loop holds a reference to that same
    object, so a reload is invisible to every other reader of it without
    needing to plumb a new settings object through `run_agent_loop`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        on_reload: Callable[[str, str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._on_reload = on_reload
        self._client = make_client(settings)
        from config.settings import config_revision

        self._revision = config_revision()
        self._lock = asyncio.Lock()
        self.chat = _ChatProxy(self)

    async def aclose(self) -> None:
        await self._client.close()

    async def ensure_current(self) -> None:
        """No-op if the config revision hasn't changed since the current
        client was built (the overwhelmingly common path — one integer
        compare per call). On a changed revision: reload settings off the
        event loop (``load()`` can hit the OS keyring, which can block),
        hot-swap only the reloadable fields, rebuild the client, close the
        superseded one, and report via ``on_reload``. Never raises into the
        run — a failed reload keeps the existing client."""
        from config.settings import config_revision

        if config_revision() == self._revision:
            return
        async with self._lock:
            if config_revision() == self._revision:
                return  # another call already reloaded while we waited
            self._revision = config_revision()

            from config.settings import load as load_settings

            try:
                fresh = await asyncio.to_thread(load_settings)
            except Exception:
                # Keep the existing client; don't retry this same broken
                # config on every subsequent call until it changes again.
                return

            changed = {
                field: getattr(fresh, field)
                for field in _RELOADABLE_FIELDS
                if getattr(fresh, field) != getattr(self._settings, field)
            }
            if not changed:
                return
            for field, value in changed.items():
                setattr(self._settings, field, value)

            old_client = self._client
            self._client = make_client(self._settings)
            with contextlib.suppress(Exception):
                await old_client.close()

            if self._on_reload is not None:
                host = urlsplit(self._settings.llm_base_url).hostname or self._settings.llm_base_url
                self._on_reload(
                    f"Settings changed — reconnected using model "
                    f"{self._settings.llm_model!r} at {host}.",
                    self._settings.llm_model,
                )


class _ChatProxy:
    def __init__(self, handle: ReloadingClient) -> None:
        self.completions = _CompletionsProxy(handle)


class _CompletionsProxy:
    def __init__(self, handle: ReloadingClient) -> None:
        self._handle = handle

    async def create(self, **kwargs: object) -> object:
        await self._handle.ensure_current()
        return await self._handle._client.chat.completions.create(**kwargs)  # type: ignore[call-overload]


def make_reloading_client(
    settings: Settings, *, on_reload: Callable[[str, str], None] | None = None
) -> AsyncOpenAI:
    """Build a self-reloading LLM client (Z2). Returns a ``ReloadingClient``
    cast to ``AsyncOpenAI`` — the cast is the one deliberate lie in this
    module: it lets every `host/agent.py` call site keep its existing
    ``llm: AsyncOpenAI`` annotation unchanged, since the only member it ever
    touches (``llm.chat.completions.create``) is exactly what this wrapper
    implements."""
    import typing

    handle = ReloadingClient(settings, on_reload=on_reload)
    return typing.cast(AsyncOpenAI, handle)
