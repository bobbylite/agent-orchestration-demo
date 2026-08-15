from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from app.config import Settings


def build_agent_card(settings: Settings) -> AgentCard:
    skill = AgentSkill(
        id="manage_todos",
        name="Manage Todos",
        description="Lists, adds, and (subject to policy) completes items on a todo list, via a real MCP server.",
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=["a2a", "todos", "mcp"],
        examples=["what's on my todo list?", "add 'buy milk' to my todos"],
    )
    return AgentCard(
        name="Task Agent",
        description="Specialist agent that reasons about which mocked MCP tool to invoke on the caller's behalf.",
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=settings.public_url,
                protocol_version="1.0",
            )
        ],
        skills=[skill],
    )
