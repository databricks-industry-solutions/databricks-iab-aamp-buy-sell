"""CrewAI LLM wired to Databricks Model Serving (OpenAI-compatible).

Databricks serving endpoints speak the OpenAI API, so CrewAI's OpenAI provider talks
to them with no adapter: point it at `<workspace host>/serving-endpoints` with a
Databricks bearer token and pass the endpoint name as the model.

The token is minted fresh from the Databricks SDK (no secrets in the repo) — locally
via AAMP_DATABRICKS_PROFILE, in a deployed app via the app's service principal. Tokens
are short-lived, so build the LLM at crew-run time (a fresh token per run). The model
name (e.g. "databricks-claude-sonnet-4-5") is the serving-endpoint name, sent verbatim
as the OpenAI `model`.
"""

from __future__ import annotations

import os
from typing import Optional

from .config import Settings


def _workspace_client(settings: Settings):
    from databricks.sdk import WorkspaceClient

    return (
        WorkspaceClient(profile=settings.databricks_profile)
        if settings.databricks_profile
        else WorkspaceClient()
    )


def resolve_base_url(settings: Settings) -> str:
    """The OpenAI-compatible Model Serving base URL (explicit, or derived from SDK host)."""
    if settings.openai_base_url:
        return settings.openai_base_url.rstrip("/")
    host = _workspace_client(settings).config.host.rstrip("/")
    return f"{host}/serving-endpoints"


def mint_token(settings: Settings) -> str:
    """Fresh Databricks bearer token for Model Serving (profile locally; SP in-app)."""
    headers = _workspace_client(settings).config.authenticate()  # {'Authorization': 'Bearer <token>'}
    auth = headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise RuntimeError("could not mint a Databricks bearer token for Model Serving")
    return auth.split(" ", 1)[1]


def build_llm(settings: Settings, *, model: Optional[str] = None, temperature: float = 0.2, **kwargs):
    """Construct a CrewAI LLM bound to a Databricks serving endpoint.

    `model` defaults to settings.llm_model; pass settings.manager_llm_model for the
    hierarchical L1 manager. OPENAI_* is also exported so any crewai/litellm internal
    that reads the env resolves to the same endpoint + token.
    """
    from crewai import LLM

    base_url = resolve_base_url(settings)
    token = mint_token(settings)
    endpoint = model or settings.llm_model

    os.environ["OPENAI_API_KEY"] = token
    os.environ["OPENAI_BASE_URL"] = base_url

    # "openai/<endpoint>" forces the OpenAI-compatible path: the provider prefix is
    # stripped and "<endpoint>" is sent as the model (the Databricks serving endpoint).
    return LLM(
        model=f"openai/{endpoint}",
        base_url=base_url,
        api_key=token,
        temperature=temperature,
        **kwargs,
    )


def smoke(settings: Optional[Settings] = None) -> str:
    """One-line connectivity check against Model Serving (no agent). Returns the reply."""
    from .config import get_settings

    settings = settings or get_settings()
    llm = build_llm(settings)
    return llm.call("Reply with exactly the word: OK")
