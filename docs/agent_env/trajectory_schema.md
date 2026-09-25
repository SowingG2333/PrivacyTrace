# Trajectory schema

Every completed agent env run emits one JSON object with a machine-readable
`schema_version` field.

During parallel generation, objects are written as individual `Sxxxx.json`
files under `artifacts/trajectory_runs/`. After a reviewed batch is complete,
`scripts/package_trajectories.py` validates and streams them into the canonical
`artifacts/trajectories/trajectories.jsonl`. The canonical directory also contains
only `_batch_summary.json` and `combined_audit.json`; logs and recovery state are
run-time artifacts rather than dataset records.

## Top-level fields

| Field | Meaning |
|---|---|
| `task` | Immutable task identity, structured `user_task`, domain and evaluation criteria |
| `environment` | Selected servers, connection status and all run limits |
| `run` | Agent/simulator model names, direct/API transport, and Agent runtime |
| `turns` | Ordered user and assistant events |
| `status` | `satisfied`, `cannot_continue`, `max_user_turns`, `agent_error`, `simulator_error`, or `environment_error` |
| `termination_reason` | Machine-readable reason for stopping |
| `token_usage` | Separate agent/simulator usage plus their known total |
| `simulator_error` | Parse attempts, errors, raw excerpts, and usage when simulator formatting exhausts retries |
| `evaluation` | Deterministic checks and metrics |
| `metrics` | Convenience alias for `evaluation.metrics` |

The previous runner's `scenario_id`, `profile_id`, `domain`, `servers`,
`tools_info`, `total_tool_calls`, and `total_tokens` fields remain as flat aliases.
For backward compatibility, `total_tokens` still means evaluated-agent tokens;
`total_known_tokens` includes both agent and simulator calls whose usage was returned.

The same structured `user_task` is retained both at the top level and under
`task`; no runtime date anchor is added. Scenario-generation tool planning is
not copied into `task.metadata` and never controls runtime visibility. The
default domain policy exposes every tool discovered from its registered MCP
servers. `environment.connections` records discovered and Agent-visible tool
counts plus `exposed_tools_by_server`, so this boundary remains auditable.
`shared_mcp_servers` records which configured servers used batch-scoped shared
endpoints; it changes process lifecycle, not tool visibility.

## Simulated user turn fields

User turns include `simulator_status`, `simulator_remaining_requirement`, `simulator_usage`,
`simulator_format_attempts`, and any invalid `simulator_format_failures` that were
recovered before producing the valid turn. `simulator_llm_outputs` contains one
audit record per provider attempt, including the original `content`, response ID,
finish reason, token usage, and reasoning-content length. This preserves empty
responses that were recovered by the provider's internal retry loop. The simulator
uses native multi-turn chat messages, requests structured JSON, and retries
malformed outputs up to the configured limit. DeepSeek simulator requests
explicitly disable thinking mode.

For `continue`, `simulator_remaining_requirement` is an exact `constraint_N` or
`expected_result` ID from the original task catalog. For `cannot_continue`, it
identifies the catalog entry blocked by unavailable profile information. It is
null for `satisfied`. This control field is stored for audit and is never shown
to the Agent.

`simulator_llm_outputs[].content` is intentionally unredacted model output for
auditing and should be treated with the same access controls as the full trajectory.

## Assistant tool call fields

Each assistant turn may contain `execution_results`. Important fields are:

| Field | Meaning |
|---|---|
| `call_id`, `call_order` | Stable identity and order within an MCP session |
| `provider_call_id` | PydanticAI/model-provider tool-call identity when available |
| `server_name`, `tool_name` | Tool origin |
| `call_statement` | Tool input |
| `returned_result` | Raw returned text when retained |
| `latency_ms` | Client-observed latency |
| `transport_success` | Whether MCP transport completed normally |
| `content_success` | Whether returned content is usable evidence |
| `outcome` | Normalized outcome category |
| `tool_call_signature` | Canonical server + tool + arguments identity used for loop protection |
| `mcp_executed`, `cache_hit` | Whether MCP was contacted or a prior identical result was reused |
| `duplicate_call_count` | Occurrence number for the signature within the current Agent turn |

Legacy `tool`, `server`, `parameters`, `result`, `error`, and `success` fields are
retained for existing notebooks. `success` now means usable content, not merely a
non-throwing transport call.

Within one Agent turn, the first signature is executed normally. Its second
occurrence reuses the prior result without contacting MCP. A third occurrence
records a failed `stalled_tool_loop` call and ends the turn with top-level
`status: agent_error` and `termination_reason: stalled_tool_loop`; it is retained
as a genuine failed trajectory. Concurrent copies already queued for that
signature stop with the turn and do not create fourth-or-later records.

Across user turns, raw tool results remain in `turns[].execution_results` and
the flat `tools_info` alias. The Agent's model history retains prior user text
and final natural-language answers, but replaces old tool protocol messages
with a compact list of server, tool, arguments, success status, outcome, and a
short result summary.

## Evaluation semantics

The deterministic evaluator reports operational diagnostics:

- usable/empty/application-error/transport-error call counts;
- repeated identical calls and reuse of the same tool with different parameters;
- separate agent, simulator, and known-total token counts;
- whether the simulator closed as satisfied and no runtime component failed.

`evaluation.passed` is a pipeline-quality gate, not a semantic reward. A research
benchmark should add explicit criteria and judge outputs under a separate key,
leaving the raw trajectory immutable.
