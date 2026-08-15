from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """This agent verifies the *same* delegated token the Chat Agent already
    holds (see CLAUDE.md "Identity propagation") — so OIDC_DISCOVERY_URL and
    AGENT_EXPECTED_AUDIENCE here must match backend/.env's values exactly.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    oidc_discovery_url: str | None = Field(default=None)
    agent_expected_audience: str | None = Field(default=None)

    # The Chat Agent's own client_id (backend/.env's AGENT_CLIENT_ID) — what
    # shows up as the verified caller's identity. Used by policy.py's ACL.
    allowed_agent_client_id: str | None = Field(default=None)

    # Must match backend/.env's TODOS_READ_SCOPE / TODOS_WRITE_SCOPE exactly —
    # policy.py checks the verified token's own `scope` claim against these,
    # not just the caller's identity. See CLAUDE.md "Per-action scoped delegation".
    todos_read_scope: str = Field(default="todos:read")
    todos_write_scope: str = Field(default="todos:write")

    anthropic_api_key: str | None = Field(default=None)
    agent_model: str = Field(default="claude-sonnet-5")

    mcp_todos_url: str = Field(default="http://localhost:9000/mcp")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=9010)
    public_url: str = Field(default="http://localhost:9010")


@lru_cache
def get_settings() -> Settings:
    return Settings()
