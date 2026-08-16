from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """This bridge reuses the Chat Agent's own PingOne identity — PingOne
    apps #1 (user sign-in), #2 (this-agent's-own-identity Client
    Credentials), and #3 (delegation Token Exchange) from README.md's
    "PingOne setup" — because it genuinely *is* the Chat Agent's identity,
    just running as a local process Claude Desktop spawns instead of as
    backend/'s FastAPI service. See CLAUDE.md "Claude Desktop as the
    orchestrator".

    App #1 needs a SECOND redirect URI registered alongside backend's own
    (http://localhost:8000/api/auth/callback):
    http://localhost:<local_callback_port>/callback — this process has no
    browser cookie jar to carry a session between requests, so it does its
    own one-time-per-process Authorization Code + PKCE flow with a
    loopback redirect (see app/local_login.py), the same pattern CLI tools
    like `gh auth login` use.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App #1 — user sign-in.
    oidc_discovery_url: str | None = Field(default=None)
    oidc_client_id: str | None = Field(default=None)
    oidc_client_secret: str | None = Field(default=None)
    oidc_scopes: str = Field(default="openid profile email")

    # App #2 — Client Credentials only. "I am the orchestration agent."
    agent_client_id: str | None = Field(default=None)
    agent_client_secret: str | None = Field(default=None)
    agent_own_scope: str = Field(default="agent:orchestration")

    # App #3 — Client Credentials + Token Exchange. Produces the delegation
    # credential addressed to the Task Agent.
    agent_delegation_client_id: str | None = Field(default=None)
    agent_delegation_client_secret: str | None = Field(default=None)
    agent_delegation_scope: str = Field(default="agent:delegation")

    # A2A: the Task Agent this bridge may delegate to.
    task_agent_url: str = Field(default="http://localhost:9010")

    # Loopback OAuth callback for the one-time browser sign-in — must match
    # a redirect URI registered on PingOne app #1.
    local_callback_port: int = Field(default=8765)

    @property
    def configured(self) -> bool:
        return bool(
            self.oidc_discovery_url
            and self.oidc_client_id
            and self.oidc_client_secret
            and self.agent_client_id
            and self.agent_client_secret
            and self.agent_delegation_client_id
            and self.agent_delegation_client_secret
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
