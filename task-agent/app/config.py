import os
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
    # must match mcp-todos-server/.env's TODOS_READ_SCOPE /
    # TODOS_WRITE_SCOPE exactly — that's what independently checks the
    # granted scope on every MCP tool call.
    todos_read_scope: str = Field(default="todos:read")
    todos_write_scope: str = Field(default="todos:write")
    todos_delete_scope: str = Field(default="todos:delete")

    # PingOne Authorize decision endpoint. The worker app is used only to
    # authenticate the decision request; the delegated token is supplied to
    # Authorize as AccessToken so it can resolve the human subject and the
    # delegating agent itself. This decision runs before the MCP token
    # exchange, once per protected tool call.
    authorize_decision_endpoint: str | None = Field(default=None)
    authorize_client_id: str | None = Field(default=None)
    authorize_client_secret: str | None = Field(default=None)
    authorize_scope: str = Field(default="")
    authorize_client_auth_method: str = Field(default="client_secret_post")
    authorize_delegate_tasks_policy_parameter: str = Field(default="evaluateDelegateTasksPolicy")
    authorize_delegate_tasks_policy_value: str = Field(default="true")
    authorize_task_policy_parameter: str = Field(default="evaluateTaskPolicy")
    authorize_task_policy_value: str = Field(default="true")

    anthropic_api_key: str | None = Field(default=None)
    agent_model: str = Field(default="claude-sonnet-5")

    # Which LLM provider task_assistant itself reasons with — same
    # meaning/shape as backend/app/config.py's identical fields (nothing
    # to do with this service's own PingOne identity/delegation chain
    # above, which is unaffected either way). "anthropic" (default),
    # "openai", or "groq" — each provider gets its own model-name field
    # (agent_model / model_id / groq_model), not one shared field, so
    # MODEL_ID and GROQ_MODEL can both stay set in .env without one
    # becoming a landmine for the other when MODEL_PROVIDER is switched.
    # groq_api_key below (also used by the judge, see below) is shared
    # across both uses — same underlying Groq account either way.
    model_provider: str = Field(default="anthropic")
    openai_api_key: str | None = Field(default=None)
    model_id: str | None = Field(default=None)  # used when model_provider="openai"

    # Judge node (app/graph.py) — evaluates task_assistant's proposed
    # answer against the request this service was actually delegated
    # (not the human's literal message, which this service never sees;
    # see CLAUDE.md). Needs no identity of its own — it never touches a
    # protected resource, just evaluates text already produced.
    judge_enabled: bool = Field(default=True)
    # "anthropic" (default), "groq", or "openai" — the judge is a pure
    # text-in/structured-verdict-out evaluation with no tool use of its
    # own, so it's a natural place to run a cheaper/free provider instead
    # of spending Anthropic tokens on it. Groq's free tier is genuinely
    # free (rate-limited, not credit-metered) and its hosted Llama models
    # support tool-calling, which is what `.with_structured_output()`
    # needs under the hood; "openai" reuses OPENAI_API_KEY/MODEL_ID above.
    judge_provider: str = Field(default="anthropic")
    # Falls back to agent_model (if provider="anthropic"), a Groq default
    # model (if provider="groq"), or model_id/a default (if
    # provider="openai") when unset — see app/graph.py's _build_judge_llm.
    # Lets a cheaper/different model judge without forcing it.
    judge_model: str | None = Field(default=None)
    # Original attempt + this many retries before giving up and returning
    # the last answer anyway, rather than looping forever.
    judge_max_attempts: int = Field(default=2)
    # PingOne Authorize evaluator-optimizer policy. The policy-information
    # statement supplies the retry budget for this task's judge.
    evaluator_optimizer_policy_parameter: str = Field(default="evaluateEvaluatorOptimizerPolicy")

    # Only needed when model_provider="groq" or judge_provider="groq". Free
    # API key from console.groq.com — no billing required for the free tier.
    groq_api_key: str | None = Field(default=None)
    # Model name for task_assistant when model_provider="groq" (the judge
    # uses judge_model instead, with its own groq default — see above).
    groq_model: str | None = Field(default=None)

    # LangSmith tracing — purely additive to this service's own OpenTelemetry
    # spans (see CLAUDE.md "OpenTelemetry is the product"), off by default.
    # Read directly by langchain-core's own tracer via os.environ (see
    # get_settings() below) — no LangSmith code is imported or called directly.
    langsmith_tracing: bool = Field(default=False)
    langsmith_endpoint: str = Field(default="https://api.smith.langchain.com")
    langsmith_api_key: str | None = Field(default=None)
    langsmith_project: str = Field(default="Todos")

    mcp_todos_url: str = Field(default="http://localhost:9000/mcp")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=9010)
    public_url: str = Field(default="http://localhost:9010")


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
