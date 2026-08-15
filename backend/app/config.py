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
