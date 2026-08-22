import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration is optional and independent, matching the reference
    console: the app runs in a degraded (buttons hidden) mode without OIDC
    or agent credentials configured."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OIDC (PingOne) — user sign-in
    oidc_discovery_url: str | None = Field(default=None)
    oidc_client_id: str | None = Field(default=None)
    oidc_client_secret: str | None = Field(default=None)
    oidc_post_logout_redirect_uri: str | None = Field(default=None)
    oidc_scopes: str = Field(default="openid profile email")

    # Agent identity — Client Credentials + RFC 8693 Token Exchange. This
    # service knows nothing about todos:read/todos:write or any other
    # downstream action-specific scope — that's entirely the Task Agent's
    # concern once it holds a delegation credential and performs its own
    # exchange. This service's only job is: prove its own identity, then
    # combine that with the user's session into a delegation credential
    # addressed to whichever agent it's decided to talk to.

    # Step 1 (Client Credentials): the Chat Agent authenticates as itself —
    # "I am the orchestration agent." Which PingOne resource owns this
    # scope determines the resulting token's `aud`, which should be this
    # service's own URL (app_base_url) — same "audience comes from the
    # scope's resource" pattern as every other scope in this app.
    agent_client_id: str | None = Field(default=None)
    agent_client_secret: str | None = Field(default=None)
    agent_own_scope: str = Field(default="agent:orchestration")

    # Step 2 (Token Exchange): combines the user's session token with an
    # actor token into one delegation credential, always scoped
    # generically "I'm delegating a task" — never the specific action
    # (todos:read, todos:write, or anything else), since this service
    # doesn't decide that. A DIFFERENT PingOne worker app from the one
    # above — the app authorized to actually perform Token Exchange isn't
    # necessarily the same app that proves the agent's own identity in
    # step 1. Which resource owns this scope determines the resulting
    # token's `aud`, which should be the target agent's own URL
    # (task_agent_url below).
    agent_delegation_client_id: str | None = Field(default=None)
    agent_delegation_client_secret: str | None = Field(default=None)
    agent_delegation_scope: str = Field(default="agent:delegation")

    # Expected `aud` claim on a token /api/invoke verifies as addressed TO
    # this service itself (not the delegation token above, which is
    # addressed to the Task Agent and verified against task_agent_url
    # instead — see app/routes/invoke.py). Falls back to agent_client_id.
    agent_expected_audience: str | None = Field(default=None)

    @property
    def resolved_expected_audience(self) -> str | None:
        return self.agent_expected_audience or self.agent_client_id

    # CIBA step-up approval. The dedicated client is separate from both
    # agent/delegation workers. CIBA tokens are stored only in HttpOnly JWE
    # cookies and are never accepted from the browser request body.
    ciba_authorization_endpoint: str | None = Field(default=None)
    ciba_token_endpoint: str | None = Field(default=None)
    ciba_client_id: str | None = Field(default=None)
    ciba_client_secret: str | None = Field(default=None)
    ciba_client_auth_method: str = Field(default="client_secret_basic")
    ciba_authorization_auth_method: str = Field(default="client_secret_basic")
    ciba_token_auth_method: str = Field(default="client_secret_basic")
    ciba_token_max_age: int = Field(default=3600)
    ciba_scope: str = Field(default="openid ciba")
    ciba_binding_message_length: int = Field(default=8)
    ciba_requested_expiry: int = Field(default=300)
    ciba_http_timeout: float = Field(default=10.0)
    ciba_poll_timeout: float = Field(default=300.0)
    ciba_min_poll_interval: float = Field(default=2.0)
    ciba_max_poll_interval: float = Field(default=10.0)
    ciba_slow_down_increment: float = Field(default=2.0)

    # Session cookie encryption (JWE, A256GCM needs a 32-byte key)
    session_secret: str | None = Field(default=None)

    # LangGraph agent — which LLM provider the `assistant` node itself
    # runs on (nothing to do with agent *identity*/delegation above, which
    # is always PingOne regardless of this setting). "anthropic" (default),
    # "openai", or "groq" — each provider gets its OWN model-name field
    # (agent_model / model_id / groq_model), deliberately not one shared
    # field: MODEL_ID and GROQ_MODEL can both stay set in .env at once
    # (e.g. "gpt-4.1" and "llama-3.3-70b-versatile") without one becoming a
    # landmine for the other when MODEL_PROVIDER is switched — a single
    # shared field would silently send an OpenAI model name to Groq (or
    # vice versa) the moment you flipped providers without also editing it.
    anthropic_api_key: str | None = Field(default=None)
    agent_model: str = Field(default="claude-sonnet-5")
    model_provider: str = Field(default="anthropic")
    openai_api_key: str | None = Field(default=None)
    model_id: str | None = Field(default=None)  # used when model_provider="openai"
    # Only needed when model_provider="groq". Free API key from
    # console.groq.com — no billing required for the free tier.
    groq_api_key: str | None = Field(default=None)
    groq_model: str | None = Field(default=None)  # used when model_provider="groq"

    # LangSmith tracing — purely additive to this service's own OpenTelemetry
    # spans (see CLAUDE.md "OpenTelemetry is the product"), off by default.
    # Read directly by langchain-core's own tracer via os.environ (see
    # get_settings() below) — no LangSmith code is imported or called directly.
    langsmith_tracing: bool = Field(default=False)
    langsmith_endpoint: str = Field(default="https://api.smith.langchain.com")
    langsmith_api_key: str | None = Field(default=None)
    langsmith_project: str = Field(default="Todos")

    # A2A: the Task Agent this Chat Agent may delegate to via ask_task_agent.
    # This is the network URL used for the A2A call. It may differ from the
    # PingOne audience carried by the delegation token in Kubernetes.
    task_agent_url: str = Field(default="http://localhost:9010")
    task_agent_expected_audience: str | None = Field(default=None)

    # Networking
    app_base_url: str = Field(default="http://localhost:8000")
    frontend_origin: str = Field(default="http://localhost:5173")

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_discovery_url and self.oidc_client_id and self.oidc_client_secret and self.session_secret)

    @property
    def agent_configured(self) -> bool:
        return bool(
            self.agent_client_id
            and self.agent_client_secret
            and self.agent_delegation_client_id
            and self.agent_delegation_client_secret
            and self.session_secret
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.langsmith_tracing:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        if settings.langsmith_api_key:
            os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    return settings
