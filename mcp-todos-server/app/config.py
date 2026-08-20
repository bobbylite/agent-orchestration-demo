from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Two independent identity surfaces, matching the rest of this repo:

    1. OIDC (PingOne) sign-in for a *human* opening this service's own web
       UI directly — its own PingOne app, own session cookie. Runs in a
       degraded (sign-in hidden) mode without it configured, same as
       backend/.
    2. Inbound auth for a *delegated* bearer token an agent (task-agent/)
       forwards on MCP tool calls — verified fresh here too, independently
       of task-agent's own gate. Must match task-agent/.env's
       OIDC_DISCOVERY_URL / AGENT_EXPECTED_AUDIENCE / ALLOWED_AGENT_CLIENT_ID
       / TODOS_READ_SCOPE / TODOS_WRITE_SCOPE exactly — see CLAUDE.md's
       "must match exactly" gotcha, now with a third copy to keep in sync.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OIDC (PingOne) — human sign-in to this service's own UI
    oidc_discovery_url: str | None = Field(default=None)
    oidc_client_id: str | None = Field(default=None)
    oidc_client_secret: str | None = Field(default=None)
    oidc_post_logout_redirect_uri: str | None = Field(default=None)
    oidc_scopes: str = Field(default="openid profile email")

    session_secret: str | None = Field(default=None)

    # Inbound auth for delegated tokens forwarded by task-agent
    agent_expected_audience: str | None = Field(default=None)
    allowed_agent_client_id: str | None = Field(default=None)
    todos_read_scope: str = Field(default="todos:read")
    todos_write_scope: str = Field(default="todos:write")
    todos_delete_scope: str = Field(default="todos:delete")

    # Friendly label for the audit log ("Agent Task Agent used ..." instead
    # of a raw client_id) — purely cosmetic, defaults to the client_id.
    agent_display_name: str = Field(default="Task Agent")

    # Networking
    app_base_url: str = Field(default="http://localhost:9000")
    frontend_origin: str = Field(default="http://localhost:5174")

    @property
    def oidc_configured(self) -> bool:
        return bool(
            self.oidc_discovery_url and self.oidc_client_id and self.oidc_client_secret and self.session_secret
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
