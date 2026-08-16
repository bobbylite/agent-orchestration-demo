"""Local stdio MCP server — Claude Desktop spawns this directly (see
claude_desktop_config.json in this directory's README section of
CLAUDE.md). It exposes ask_task_agent_read/ask_task_agent_write, the same
shape backend/app/agent/tools.py's LangGraph tools have, so Claude
Desktop's own model becomes the orchestrator deciding when to delegate —
while presenting the EXACT SAME PingOne identity (apps #2/#3) the Chat
Agent already has.

Everything downstream of that identity — the Task Agent's inbound auth,
its own further RFC 8693 exchange, mcp-todos-server's policy ACL and OBO
audit log — is completely unaware anything changed at the front door. See
CLAUDE.md "Claude Desktop as the orchestrator".

Logging (not OpenTelemetry): this runs as a short-lived local subprocess
with no HTTP port of its own for the frontend's Telemetry panel to poll —
same reasoning task-agent's judge node used before it got real spans (see
CLAUDE.md), except here there's no server process to eventually add spans
to. stderr is where Claude Desktop surfaces MCP server logs to the user.
"""

from __future__ import annotations

import logging
import sys

from fastmcp import FastMCP

from app import delegate
from app.config import get_settings
from app.credentials import CredentialManager

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("Task Agent Bridge")
_settings = get_settings()
_credentials = CredentialManager(_settings)


async def _call_task_agent(request: str) -> str:
    if not _settings.configured:
        return (
            "This bridge isn't configured yet — claude-bridge/.env is missing PingOne "
            "credentials. See claude-bridge/.env.example."
        )
    try:
        token = await _credentials.get_delegation_token()
    except Exception as exc:  # noqa: BLE001 — surfaced to the model as a normal tool result, not a crash
        logger.exception("Failed to obtain a delegation credential")
        return f"Could not authenticate with PingOne: {exc}"
    return await delegate.ask_task_agent(request, delegation_token=token, task_agent_url=_settings.task_agent_url)


@mcp.tool
async def ask_task_agent_read(request: str) -> str:
    """Delegate a READ-ONLY request to the Task Agent — use this for viewing
    or listing the user's todos. Never use this for adding, completing, or
    otherwise changing anything; use ask_task_agent_write for that."""
    return await _call_task_agent(request)


@mcp.tool
async def ask_task_agent_write(request: str) -> str:
    """Delegate a WRITE request to the Task Agent — use this for adding a
    new todo or marking one complete, or any other change to the user's
    todo list. Describe what to do in plain language (e.g. "mark 'buy milk'
    as complete") — you do not need a todo's internal id first; the Task
    Agent looks it up itself."""
    return await _call_task_agent(request)


def main() -> None:
    # show_banner=False — stderr is what Claude Desktop's MCP log viewer
    # shows the user; keep it to this bridge's own sign-in/delegation
    # narration (the logging calls throughout this package), not FastMCP's
    # ASCII banner.
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
