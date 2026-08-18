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

from typing import Any

import httpx

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
    }


def check(tool_name: str, client_id: str | None) -> bool:
    return bool(client_id) and client_id in _identity_acl().get(tool_name, set())


async def evaluate_judge_budget(
    settings: Settings,
    *,
    subject_token: str | None,
    actor_cache: dict[str, Any],
) -> tuple[int | None, str | None]:
    """Read the evaluator-optimizer retry budget from Authorize."""
    if not settings.authorize_decision_endpoint or not subject_token:
        return None, "authorize_not_configured"
    with with_span("authorize.evaluator_optimizer", {}) as span:
        try:
            if "authorize_token_endpoint" not in actor_cache:
                actor_cache["authorize_token_endpoint"] = await get_token_endpoint(settings.oidc_discovery_url or "")
            if "authorize_worker_token" not in actor_cache:
                worker = await client_credentials_grant(
                    actor_cache["authorize_token_endpoint"],
                    client_id=settings.authorize_client_id,
                    client_secret=settings.authorize_client_secret,
                    scope=settings.authorize_scope,
                    auth_method=settings.authorize_client_auth_method,
                )
                actor_cache["authorize_worker_token"] = worker["access_token"]
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    settings.authorize_decision_endpoint,
                    headers={"Authorization": f"Bearer {actor_cache['authorize_worker_token']}"},
                    json={"parameters": {settings.evaluator_optimizer_policy_parameter: "true", "AccessToken": subject_token}},
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("decision") != "PERMIT":
                span.set_attribute("policy.result", "deny")
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
            return budget, None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            span.set_attribute("policy.result", "error")
            span.set_attribute("policy.failure_reason", type(exc).__name__)
            return None, "evaluator_optimizer_request_failed"


async def check_with_authorize(
    settings: Settings,
    *,
    tool_name: str,
    client_id: str | None,
    subject_token: str | None,
    actor_cache: dict[str, Any],
) -> tuple[bool, str, str | None]:
    """Run the local ACL and then ask PingOne Authorize about group access.

    The decision endpoint introspects ``subject_token`` and evaluates the
    configured group policy. Only an explicit ``PERMIT`` is accepted;
    missing configuration, malformed responses, and all transport/upstream
    failures deny by default. The worker token is cached only for this task,
    while the Authorize decision itself is deliberately fresh per call.
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

    with with_span("authorize.decision", {"policy.tool": tool_name}) as span:
        try:
            if "authorize_token_endpoint" not in actor_cache:
                actor_cache["authorize_token_endpoint"] = await get_token_endpoint(settings.oidc_discovery_url or "")
            token_endpoint = actor_cache["authorize_token_endpoint"]

            if "authorize_worker_token" not in actor_cache:
                worker = await client_credentials_grant(
                    token_endpoint,
                    client_id=settings.authorize_client_id,
                    client_secret=settings.authorize_client_secret,
                    scope=settings.authorize_scope,
                    auth_method=settings.authorize_client_auth_method,
                )
                actor_cache["authorize_worker_token"] = worker["access_token"]

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {actor_cache['authorize_worker_token']}"},
                    json={
                        "parameters": {
                            settings.authorize_group_policy_parameter: settings.authorize_group_policy_value,
                            "AccessToken": subject_token,
                        }
                    },
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            span.set_attribute("policy.result", "error")
            span.set_attribute("policy.failure_reason", type(exc).__name__)
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
            return False, "group_policy_denied", statement_payload
        span.set_attribute("policy.result", "permit")
        return True, "permit", None
