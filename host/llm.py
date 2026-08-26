"""OpenAI-compatible client factory for any endpoint via base_url override.

Also branches to Azure OpenAI's own client class when the configured endpoint
looks like Azure — a plain AsyncOpenAI sends `Authorization: Bearer <key>` and
whatever path the SDK builds from `base_url`, neither of which Azure accepts
outside its `/openai/v1` compatibility surface (see issue #61 for a real
misconfiguration walkthrough: a full operation URL with an embedded dated
`api-version` broke both request signing and the deployment route).
"""

from __future__ import annotations

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
