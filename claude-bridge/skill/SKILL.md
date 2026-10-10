---
name: task-agent-todos
description: Manage the user's todo list — view/list, add, or complete items — via the task-agent-bridge MCP connector. Use whenever the user mentions todos, tasks, a to-do list, or checking something off, in any phrasing (e.g. "what's on my list", "add X", "mark Y done", "did I finish Z"), even if they don't say the word "todo" explicitly.
---

# Todos, via the Task Agent Bridge

The user's todo list lives behind the `task-agent-bridge` MCP connector,
not in your own knowledge or memory. Any request about the user's
todos/tasks — reading, adding, or completing — needs one of that
connector's two tools. Don't answer from assumption or ask the user to
repeat themselves in a different way first; if the request is at all
about their todo list, call the tool.

## The two tools

Both take one parameter, `request: str` — a plain-language description of
what to do — and return a plain-language `str` answer. There is no
structured request/response shape to construct; write `request` the way
you'd say it to a person.

- **`ask_task_agent_read(request)`** — anything read-only: viewing,
  listing, searching, or asking about the state of todos. Use this for
  "what's on my list", "do I have anything about X", "is Y done yet".
- **`ask_task_agent_write(request)`** — anything that changes the list:
  adding a new item, or marking one complete. Use this for "add X to my
  list", "mark X done", "I finished X".

Never use `ask_task_agent_write` for a question that isn't asking for a
change — a misrouted read as a write risks a real (if idempotent) write
call landing where none was wanted.

### You don't need an id first

To complete a todo, just describe it in plain language in the `request`
string (e.g. `"mark 'buy milk' as complete"`) — you do not need to call
`ask_task_agent_read` first to look up its id. The Task Agent on the
other end looks the item up by its own text match and resolves the id
itself, in the same call.

### What a todo actually has — don't invent fields

A todo has exactly: **text** and a **done**/not-done state (plus who
created it and when, which you'd only see if asked). There is **no** due
date, priority, tag, category, or list/project field. Delete is supported
only when the user explicitly asks for it and the target is unambiguous.
If the user asks for something that implies an unsupported field (e.g.
"add X due tomorrow" or "show me high-priority todos"), do the supported
part and explain the limitation plainly — don't quietly drop it or pretend
it worked.

## Why this exists — worth narrating, briefly

Every call through these tools is a real OAuth delegation, not a mocked
integration: this connector proves its own PingOne identity, exchanges
that plus your session for a scoped credential addressed to the Task
Agent, and the Task Agent independently re-verifies it and mints its own
further-scoped credential before touching the actual todos service. When
you use these tools, a one-line mention that you're "delegating to the
Task Agent" (rather than silently just returning an answer) is accurate
and — for this project specifically — the point: the person you're
talking to is very likely demoing exactly this delegation chain.

## Troubleshooting

If a call fails outright rather than returning a normal answer, the
error text usually explains why (e.g. PingOne rejected the delegation
exchange, or the Task Agent isn't running) — relay that explanation
rather than a generic "something went wrong."
