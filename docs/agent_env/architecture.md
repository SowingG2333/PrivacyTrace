# Agent env architecture

## Design goals

The environment uses PydanticAI for real MCP discovery and execution while
adopting the explicit boundaries used by τ²-style evaluations:

1. A task is immutable input, not an implicit mix of CLI arguments.
2. The simulated user and evaluated agent have separate private state.
3. The environment owns turn ordering and termination.
4. A trajectory is a first-class artifact and can be evaluated again later.
5. Direct and HTTP transports share one Agent result contract.

## Boundaries

### Task and domain

`TaskCase` joins one structured `user_task` to its profile and optional
evaluation criteria. The task contains `goal`, `context`, `constraints`, and
`expected_result`; the simulator and saved trajectory consume this structure
without re-realizing it as a separate scenario string.
`DomainSpec` describes the MCP servers available to a domain through one unified
`core_servers` list. The domain registry is validated against
`mcp_servers/commands.json` before a run. Scenario-synthesis traces may record
which tools were used to construct a grounded task, but that provenance is not
runtime policy. The simulator receives the natural-language `user_task`, and the
evaluated agent sees every tool discovered from every server registered for the
task's domain.

### User simulator

`UserSimulator` receives the private profile, structured user task, policy and visible
conversation. Its control status is not exposed to the agent. The complete
canonical profile, including direct identifiers, is available only in the
simulator's private system context. Disclosure is controlled by the behavior
policy: attributes are revealed only when relevant, and direct identifiers or
sensitive attributes are not volunteered. The visible conversation is
reconstructed as native chat messages from the simulator model's perspective:
simulator-authored user turns use the API `assistant` role and evaluated-agent
replies use the API `user` role. Historical simulator turns retain only their
natural-language message; status and `remaining_requirement` remain control
metadata. Each non-satisfied turn selects an exact `constraint_N` or
`expected_result` ID from an immutable catalog rendered from the original task;
satisfied turns set it to null. The compact system prompt has one
behavior-policy source and a minimal JSON output contract. It
repairs minor JSON syntax and retries invalid formatting. Missing task-relevant
personal facts may not be invented. Exhausted attempts are recorded as
`simulator_error` with usage and bounded raw excerpts.

### Agent adapter

The Agent contract exposes three async operations: `initialize`, `respond`, and
`close`. The direct implementation is `PydanticAIAgentAdapter`; the HTTP
adapter talks to a service backed by the same implementation. PydanticAI owns
OpenAI-compatible model calls, MCP toolsets, tool dispatch, and usage limits.
The adapter keeps full tool results only within the active Agent turn. Before
the next user turn, it carries forward prior user messages and final Agent text
plus a compact tool-activity summary, rather than replaying raw tool protocol
messages. Both
direct and HTTP paths return `AgentTurnResult`, so the orchestrator does not
depend on transport.

### Agent env

`AgentEnv` owns the event loop:

```text
initialize agent
repeat until max_user_turns:
  simulator.next_turn(history) -> status + remaining original requirement
  if simulator closes: stop
  agent.respond(message, timeout)
  if agent errors: stop
close agent
evaluate and persist trajectory
```

The profile is never placed in the agent prompt. Only simulator-authored user
messages enter the visible history.

### Agent turn

PydanticAI runs the model/tool loop and maps `ExecutionLimits` to request,
tool-call, and token limits. A single-case run connects directly to the catalog's
stdio servers; `agent_env.http_server` handles locally hosted HTTP MCP servers
such as Geoapify. A batch run instead starts one upstream instance per selected
server and exposes it through a loopback Streamable HTTP proxy. Every trajectory
still owns a separate PydanticAI Agent and frontend MCP session, while the batch
reuses the upstream server process and tool catalog. This reduces process and
memory growth without narrowing the domain-visible tools. `--no-shared-mcp`
restores the per-trajectory server lifecycle for diagnosis.

A call hook emits the research trajectory fields and separates transport
completion from usable application content. It signs calls by server, tool, and
canonical arguments: the second occurrence reuses the cached first result, and
the third atomically marks the turn stalled, records `stalled_tool_loop`, and
ends the Agent turn without another MCP request. Sibling duplicate calls already
queued behind the signature lock stop without emitting fourth-or-later records.
Server names are prefixed in model-visible tool names to prevent collisions,
while saved traces retain original server/tool identities. SIGINT and SIGTERM
enter the same interruption path: the batch stops active scenario process
groups, closes shared MCP proxy/upstream groups, and marks its recovery manifest
`interrupted` before exiting.

### Evaluation

`evaluation.py` provides deterministic diagnostics that require no judge model.
It deliberately does not claim semantic task correctness. Domain checks or an
LLM judge can consume the saved trajectory without rerunning MCP servers.

## Dependency direction

```text
CLI / API
  → agent_env
      → Agent protocol
          → PydanticAI Agent
              → PydanticAI MCPToolset / FastMCP client
                  → direct catalog server (single case)
                  → batch-shared HTTP proxy → catalog server (batch)
```

There is no parallel custom executor or benchmark-specific server manager.
This keeps tool-loop behavior owned by the maintained Agent framework and
leaves only research-specific logic in this repository.
