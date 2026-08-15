---
name: extend-agent-graph
description: Conventions and the agreed (but shelved) plan for adding nodes, tools, or a second agent to the LangGraph graph under backend/app/agent/. Read this before adding any node, tool-calling loop, or A2A integration — don't re-derive the design from scratch.
---

# Extending the agent graph

## The one rule that overrides everything else here

**Security and authorization logic never lives inside a LangGraph node.**
It is always a deterministic gate that runs before the graph is touched —
a FastAPI dependency or a plain function called at the top of a route
handler, never something reachable via the graph's own control flow.

Why this is non-negotiable: a graph node's execution is something the
model's own output can influence (which node runs next, whether a tool
gets called, with what arguments). Anything that decides *whether the
caller is allowed to be here at all* must not be inside that blast radius.

The existing example to copy: `backend/app/auth/inbound.py`
(`verify_inbound_token`) runs in `backend/app/routes/invoke.py` *before*
`graph.astream_events(...)` is ever called — not as a node.

If a future "policy agent" or ACL check is added, it follows the exact same
shape: a plain function, even if it's given an agent-like name in telemetry
or UI copy for demo/storytelling purposes. Do not give it its own LLM call
unless there's a genuine need for model judgment (unlikely for ACL lookups).

## Current state

`backend/app/agent/graph.py`: one node, `assistant` (calls
`ChatAnthropic`), `MemorySaver`-checkpointed per `thread_id`.
`backend/app/routes/invoke.py` calls `graph.astream_events(..., version="v2")`
and re-emits `on_chat_model_stream` deltas as SSE.

Honest framing if asked "why LangChain for this": at one node, it provides
almost nothing a raw Anthropic SDK call + a dict of message history
wouldn't. It's here because of an explicit ask to build toward multi-step
orchestration and multi-agent (A2A) work — see below. Don't over-justify it
if there's still only one node; say so plainly and point at the plan below
as the reason it's staying.

## Shelved plan: multi-agent + A2A demonstration

Agreed in conversation on 2026-08-15. **Do not start building this without
confirming with the user first** — it was explicitly shelved, not
abandoned, and the user may want to revisit the shape before implementation
starts.

### Why not just a deterministic router + ACL check in one graph?

That was the user's original idea, and the reason it's insufficient for
*demonstrating A2A specifically*: A2A's defining characteristics are (a)
each agent has its own independent, model-backed reasoning — not just
control flow — and (b) agents communicate as separate services over the
actual A2A protocol (Agent Cards for capability discovery, task-based
JSON-RPC/HTTP, streamed artifacts), not via function calls inside one
process. A single graph with an if/else router and a lookup table doesn't
exercise either of those, even though it superficially looks like "multiple
agents."

### Agreed shape

**Chat Agent graph** (this backend — what `/api/invoke` calls today) —
2 nodes:
- `assistant` — the existing model call, now with a tool bound (e.g.
  `ask_task_agent`). It decides itself, via real tool-calling, whether to
  delegate — not a keyword/intent classifier.
- `a2a_delegate` — **not an LLM call.** An async node that acts purely as
  an A2A *client*: builds a Task from the tool call's arguments, sends it
  to the Task Agent's A2A server (use the official `a2a-sdk` Python
  package for protocol compliance, don't hand-roll JSON-RPC), awaits the
  artifact/result.
- Edges: `assistant` →(tool call present?)→ `a2a_delegate` → back to
  `assistant` (so the model turns the artifact into a natural-language
  answer — the "pretty answer" from the original ask) → `END`. This is the
  same loop shape as LangGraph's prebuilt ReAct agent; the only difference
  is the "tool" is a cross-process A2A call instead of a local function.

**Task/Specialist Agent graph** (NEW — a separate running service/process,
exposing its own A2A server; does not live in `backend/app/agent/`) —
2 nodes:
- `task_assistant` — its own model call. Reasons about *how* to fulfill
  the delegated task — which mocked tool to invoke, in what order. This
  independent reasoning is what makes it a real second "agent" rather than
  a stand-in for a lookup table.
- `execute_tool` — calls the mocked tool (first candidate: `list_todos`),
  loops back to `task_assistant` to package the result as an A2A artifact.

**Policy/ACL check** — still not a graph node in either graph. It's a
FastAPI-level gate in front of the Task Agent's A2A endpoint, structurally
identical to `inbound_auth.verify`: checks the calling agent's identity
(RFC 8693 `act` claim — already decoded into `InboundIdentity.actor_sub` in
`backend/app/auth/inbound.py`, no new plumbing needed there) plus the
delegated user's identity (`InboundIdentity.sub`) against an ACL for the
requested tool, before the Task Agent's own graph is touched at all.

**Total: 4 LangGraph nodes across 2 separate graphs/processes.**

### Tools

Not yet built. Planned: a hardcoded config (format TBD — plain Python dict
or a small YAML/JSON file) listing mocked tools available to the Task
Agent, `list_todos` as the first entry, with the ACL keyed by
`(user_sub, agent_client_id, tool_name)`.

### What "is LangChain orchestrating A2A" actually means here

Precisely: LangGraph orchestrates each agent's own internal reasoning loop
(the `assistant`/`task_assistant` ↔ tool-node cycles). It does not
implement the A2A protocol itself — that's the `a2a-sdk`'s job, invoked
from inside the `a2a_delegate` node. The relationship is the same as
LangChain + MCP: `langchain-mcp-adapters` exposes MCP tools *as* LangChain
tools; A2A becomes another tool source the same way, not something
LangChain has native protocol support for.
