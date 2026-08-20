"""Tool-access policy for delegated (agent) calls — identity only now.

Scope is no longer checked here: the inbound delegation token always
carries the same generic scope (Settings.expected_delegation_scope,
"agent:delegation") regardless of which tool is ultimately called — this
service decides which specific todos:read/todos:write capability it needs
itself, per tool call, via its own RFC 8693 Token Exchange
(app/graph.py's _scoped_tool_call). Whether that exchange actually
succeeds — and mcp-todos-server's own independent verification of the
resulting token — are the real enforcement points for *which* action is
allowed, not a local comparison here. This check only answers "is this
caller even allowed to reach this tool at all."
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app import authorize_audit
from app.config import Settings, get_settings
from app.telemetry import with_span
from app.token_grants import client_credentials_grant, get_token_endpoint


def _identity_acl() -> dict[str, set[str]]:
    settings = get_settings()
    allowed = {settings.allowed_agent_client_id} if settings.allowed_agent_client_id else set()
    return {
        "list_todos": allowed,
        "add_todo": allowed,
        "complete_todo": allowed,
        "reopen_todo": allowed,
        "delete_todo": allowed,
    }


def check(tool_name: str, client_id: str | None) -> bool:
    return bool(client_id) and client_id in _identity_acl().get(tool_name, set())


# Process-wide cache for this service's OWN Authorize worker credential —
# safe to reuse across every task (it's this service's own identity, proven
# once via Client Credentials, not scoped to any particular request or
# subject). The three functions below used to mint a fresh one per task via
# a task-scoped actor_cache, which meant every single delegated request
# re-authenticated to PingOne from scratch — real, unnecessary load that
# contributed to hitting PingOne's rate limit under rapid demo use. The
# Authorize *decision* itself is still requested fresh every single call
# (never cached) — only the credential used to ask stays warm.
_worker_cache: dict[str, Any] = {}
_TOKEN_REFRESH_MARGIN_SECONDS = 30.0
_DECISION_MAX_ATTEMPTS = 3


async def _get_worker_token(settings: Settings) -> str:
    now = time.monotonic()
    cached_token = _worker_cache.get("access_token")
    expires_at = _worker_cache.get("expires_at", 0.0)
    if cached_token and now < expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
        return cached_token
    if "token_endpoint" not in _worker_cache:
        _worker_cache["token_endpoint"] = await get_token_endpoint(settings.oidc_discovery_url or "")
    worker = await client_credentials_grant(
        _worker_cache["token_endpoint"],
        client_id=settings.authorize_client_id,
        client_secret=settings.authorize_client_secret,
        scope=settings.authorize_scope,
        auth_method=settings.authorize_client_auth_method,
    )
    _worker_cache["access_token"] = worker["access_token"]
    _worker_cache["expires_at"] = now + float(worker.get("expires_in", 300))
    return worker["access_token"]


async def _post_decision(endpoint: str, worker_token: str, parameters: dict[str, str]) -> dict[str, Any]:
    """POST to the Decision Endpoint, retrying on 429 with backoff (honoring
    a Retry-After header when PingOne sends one). The decision itself is
    still evaluated fresh every call — this only smooths over a transient
    rate limit so a real action doesn't hard-fail when a short wait would
    have succeeded. A 429 that persists past the retry budget still raises,
    same as any other upstream failure — this app fails closed."""
    response: httpx.Response | None = None
    for attempt in range(_DECISION_MAX_ATTEMPTS):
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {worker_token}"},
                json={"parameters": parameters},
            )
        if response.status_code != 429:
            response.raise_for_status()
            return response.json()
        if attempt < _DECISION_MAX_ATTEMPTS - 1:
            retry_after = response.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2**attempt
            await asyncio.sleep(delay)
    assert response is not None
    response.raise_for_status()
    return response.json()


async def evaluate_judge_budget(
    settings: Settings,
    *,
    subject_token: str | None,
    client_id: str | None = None,
) -> tuple[int | None, str | None]:
    """Read the evaluator-optimizer retry budget from Authorize."""
    if not settings.authorize_decision_endpoint or not subject_token:
        return None, "authorize_not_configured"
    with with_span("authorize.evaluator_optimizer", {}) as span:
        try:
            worker_token = await _get_worker_token(settings)
            body = await _post_decision(
                settings.authorize_decision_endpoint,
                worker_token,
                {settings.evaluator_optimizer_policy_parameter: "true", "AccessToken": subject_token},
            )
            if not isinstance(body, dict) or body.get("decision") != "PERMIT":
                span.set_attribute("policy.result", "deny")
                authorize_audit.record(
                    policy="evaluator_optimizer", decision="deny",
                    agent_client_id=client_id, reason="evaluator_optimizer_denied",
                )
                return None, "evaluator_optimizer_denied"
            statements = body.get("statements", [])
            if not isinstance(statements, list):
                return None, "evaluator_optimizer_invalid_payload"
            payload = next(
                (statement.get("payload") for statement in statements
                 if isinstance(statement, dict) and statement.get("code") == "policy-information"),
                None,
            )
            if not isinstance(payload, str) or not payload.strip().isdigit():
                return None, "evaluator_optimizer_invalid_budget"
            budget = int(payload.strip())
            if budget < 1:
                return None, "evaluator_optimizer_invalid_budget"
            span.set_attribute("policy.result", "permit")
            span.set_attribute("policy.budget", budget)
            authorize_audit.record(policy="evaluator_optimizer", decision="permit", agent_client_id=client_id)
            return budget, None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            span.set_attribute("policy.result", "error")
            span.set_attribute("policy.failure_reason", type(exc).__name__)
            authorize_audit.record(
                policy="evaluator_optimizer", decision="error",
                agent_client_id=client_id, reason="evaluator_optimizer_request_failed",
            )
            return None, "evaluator_optimizer_request_failed"


async def check_task_policy(
    settings: Settings,
    *,
    tool_name: str,
    exchanged_token: str | None,
    client_id: str | None = None,
) -> tuple[bool, str]:
    """Authorize the freshly exchanged MCP token before calling MCP."""
    if not settings.authorize_decision_endpoint or not exchanged_token:
        return False, "task_policy_not_configured"
    with with_span("authorize.task_policy", {"policy.tool": tool_name}) as span:
        try:
            worker_token = await _get_worker_token(settings)
            body = await _post_decision(
                settings.authorize_decision_endpoint,
                worker_token,
                {
                    settings.authorize_task_policy_parameter: settings.authorize_task_policy_value,
                    "AccessToken": exchanged_token,
                },
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            span.set_attribute("policy.result", "error")
            span.set_attribute("policy.failure_reason", type(exc).__name__)
            authorize_audit.record(
                policy="task_policy", decision="error", tool=tool_name,
                agent_client_id=client_id, reason="task_policy_request_failed",
            )
            return False, "task_policy_request_failed"
        if not isinstance(body, dict) or body.get("decision") != "PERMIT":
            span.set_attribute("policy.result", str(body.get("decision", "invalid")).lower() if isinstance(body, dict) else "invalid")
            authorize_audit.record(
                policy="task_policy", decision="deny", tool=tool_name,
                agent_client_id=client_id, reason="task_policy_denied",
            )
            return False, "task_policy_denied"
        span.set_attribute("policy.result", "permit")
        authorize_audit.record(policy="task_policy", decision="permit", tool=tool_name, agent_client_id=client_id)
        return True, "permit"


async def check_with_authorize(
    settings: Settings,
    *,
    tool_name: str,
    client_id: str | None,
    subject_token: str | None,
) -> tuple[bool, str, str | None]:
    """Run the local ACL and then ask PingOne Authorize about group access.

    The decision endpoint introspects ``subject_token`` and evaluates the
    configured group policy. Only an explicit ``PERMIT`` is accepted;
    missing configuration, malformed responses, and all transport/upstream
    failures deny by default. The worker token is cached process-wide (see
    _get_worker_token above), while the Authorize decision itself is
    deliberately fresh per call.
    """
    if not check(tool_name, client_id):
        return False, "agent_acl_denied", None

    endpoint = settings.authorize_decision_endpoint
    if not endpoint:
        return False, "authorize_not_configured", None
    if not subject_token:
        return False, "missing_subject_token", None
    if not settings.authorize_client_id or not settings.authorize_client_secret:
        return False, "authorize_credentials_not_configured", None

    with with_span("authorize.delegation_policy", {"policy.tool": tool_name}) as span:
        try:
            worker_token = await _get_worker_token(settings)
            body = await _post_decision(
                endpoint,
                worker_token,
                {
                    settings.authorize_delegate_tasks_policy_parameter: settings.authorize_delegate_tasks_policy_value,
                    "AccessToken": subject_token,
                },
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            span.set_attribute("policy.result", "error")
            span.set_attribute("policy.failure_reason", type(exc).__name__)
            authorize_audit.record(
                policy="delegation_policy", decision="error", tool=tool_name,
                agent_client_id=client_id, reason="authorize_request_failed",
            )
            return False, "authorize_request_failed", None

        if not isinstance(body, dict) or body.get("decision") != "PERMIT":
            decision = body.get("decision", "invalid") if isinstance(body, dict) else "invalid"
            statements = body.get("statements", [])
            statement_payload = None
            if isinstance(statements, list):
                statement_payload = next(
                    (
                        statement.get("payload")
                        for statement in statements
                        if isinstance(statement, dict) and statement.get("payload")
                    ),
                    None,
                )
            span.set_attribute("policy.result", str(decision).lower())
            authorize_audit.record(
                policy="delegation_policy", decision="deny", tool=tool_name,
                agent_client_id=client_id, reason=statement_payload or "group_policy_denied",
            )
            return False, "group_policy_denied", statement_payload
        span.set_attribute("policy.result", "permit")
        authorize_audit.record(policy="delegation_policy", decision="permit", tool=tool_name, agent_client_id=client_id)
        return True, "permit", None
