from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """This agent verifies the delegation credential the Chat Agent
    forwards — audience = this service's own URL, scope = a fixed generic
    delegation scope (NOT todos:read/write, which this service decides for
    itself, per tool call, via its own further RFC 8693 Token Exchange).
    See CLAUDE.md "Identity propagation across the A2A hop" (2026-08-16).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    oidc_discovery_url: str | None = Field(default=None)

    # Inbound: this service's own audience (its own URL, e.g.
    # http://localhost:9010) — the delegation token must be addressed here.
    agent_expected_audience: str | None = Field(default=None)
    # The delegation token's own scope must contain exactly this — proves
    # it's a genuine delegation credential, not some other token that just
    # happens to have the right audience.
    expected_delegation_scope: str = Field(default="agent:delegation")

    # The client_id claim on that inbound token — whichever PingOne worker
    # app actually PERFORMED the Token Exchange that produced it
    # (backend/.env's AGENT_DELEGATION_CLIENT_ID, not AGENT_CLIENT_ID —
    # the two are deliberately different apps now). Used by policy.py's ACL.
    allowed_agent_client_id: str | None = Field(default=None)

    # This service's OWN identity — Client Credentials, scoped
    # agent_task_scope, audience = itself. The resulting actor token is
    # what proves "the Task Agent" (not just some anonymous caller) when
    # it goes on to request an MCP-scoped token below. A THIRD PingOne
    # worker app, distinct from the Chat Agent's two.
    task_agent_client_id: str | None = Field(default=None)
    task_agent_client_secret: str | None = Field(default=None)
    agent_task_scope: str = Field(default="agent:task")

    # A FOURTH, distinct PingOne worker app — authorized to perform Token
    # Exchange against the MCP server's own resource. Combines the
    # already-delegated token (received from the Chat Agent, preserving
    # the human's identity through the chain — RFC 8693 exchange chains
    # keep the original subject's `sub`, which is what keeps OBO
    # attribution intact all the way to mcp-todos-server's audit log) as
    # subject with this service's own actor token, scoped to exactly the
    # todos capability a given tool call needs — requested fresh, per
    # call, not cached broadly ("step-up" scoping: read now, write later
    # if a different tool needs it, never assumed from an earlier call).
    todos_mcp_client_id: str | None = Field(default=None)
    todos_mcp_client_secret: str | None = Field(default=None)
    mcp_todos_audience: str = Field(default="http://localhost:9000/mcp")

    # What to actually request from the exchange above, per tool —
    # must match backend/.env's copies... no it doesn't anymore: these are
    # now THIS service's own decision, not inherited from an upstream
    # scope. Must match mcp-todos-server/.env's TODOS_READ_SCOPE /
    # TODOS_WRITE_SCOPE exactly — that's what independently checks the
    # granted scope on every MCP tool call.
    todos_read_scope: str = Field(default="todos:read")
    todos_write_scope: str = Field(default="todos:write")

    anthropic_api_key: str | None = Field(default=None)
    agent_model: str = Field(default="claude-sonnet-5")

    # Judge node (app/graph.py) — evaluates task_assistant's proposed
    # answer against the request this service was actually delegated
    # (not the human's literal message, which this service never sees;
    # see CLAUDE.md). Needs no identity of its own — it never touches a
    # protected resource, just evaluates text already produced.
    judge_enabled: bool = Field(default=True)
    # "anthropic" (default) or "groq" — the judge is a pure text-in/
    # structured-verdict-out evaluation with no tool use of its own, so
    # it's a natural place to run a cheaper/free provider instead of
    # spending Anthropic tokens on it. Groq's free tier is genuinely free
    # (rate-limited, not credit-metered) and its hosted Llama models
    # support tool-calling, which is what `.with_structured_output()`
    # needs under the hood.
    judge_provider: str = Field(default="anthropic")
    # Falls back to agent_model (if provider="anthropic") or a Groq default
    # model (if provider="groq") when unset — see app/graph.py's
    # _build_judge_node. Lets a cheaper/different model judge without
    # forcing it.
    judge_model: str | None = Field(default=None)
    # Original attempt + this many retries before giving up and returning
    # the last answer anyway, rather than looping forever.
    judge_max_attempts: int = Field(default=2)

    # Only needed when judge_provider="groq". Free API key from
    # console.groq.com — no billing required for the free tier.
    groq_api_key: str | None = Field(default=None)

    mcp_todos_url: str = Field(default="http://localhost:9000/mcp")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=9010)
    public_url: str = Field(default="http://localhost:9010")


@lru_cache
def get_settings() -> Settings:
    return Settings()
