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

    # Agent identity — Client Credentials + RFC 8693 Token Exchange
    agent_client_id: str | None = Field(default=None)
    agent_client_secret: str | None = Field(default=None)
    # Scope requested for the agent's own actor token (step 1, Client
    # Credentials) — what the agent can do *as itself*.
    agent_scopes: str | None = Field(default=None)
    # Scope requested for the resulting delegated token (step 2, Token
    # Exchange) — what the agent can do *on behalf of the user*, which is
    # legitimately a different scope set than its own actor-token scope
    # (e.g. an actor-only "agent:identify" vs a delegated "agentcore:invoke").
    # Falls back to agent_scopes if unset.
    agent_token_exchange_scope: str | None = Field(default=None)

    # Expected `aud` claim on the delegated token /api/invoke will accept —
    # this is what makes inbound auth actually mean something: a token
    # that's valid for some *other* purpose (like the user's raw session
    # token) must be rejected because its audience isn't the agent. Falls
    # back to agent_client_id, which is what PingOne populates `aud` with
    # for a token minted against a custom resource owned by the agent app.
    agent_expected_audience: str | None = Field(default=None)

    @property
    def resolved_token_exchange_scope(self) -> str | None:
        return self.agent_token_exchange_scope or self.agent_scopes

    @property
    def resolved_expected_audience(self) -> str | None:
        return self.agent_expected_audience or self.agent_client_id

    # Session cookie encryption (JWE, A256GCM needs a 32-byte key)
    session_secret: str | None = Field(default=None)

    # LangGraph agent
    anthropic_api_key: str | None = Field(default=None)
    agent_model: str = Field(default="claude-sonnet-5")

    # Networking
    app_base_url: str = Field(default="http://localhost:8000")
    frontend_origin: str = Field(default="http://localhost:5173")

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_discovery_url and self.oidc_client_id and self.oidc_client_secret and self.session_secret)

    @property
    def agent_configured(self) -> bool:
        return bool(self.agent_client_id and self.agent_client_secret and self.session_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
