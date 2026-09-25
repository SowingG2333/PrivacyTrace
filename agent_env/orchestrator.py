"""Tau2-style simulator-agent-env event loop."""

from __future__ import annotations

from itertools import count
import time
from typing import Any

from agent_env.evaluation import evaluate_trajectory
from agent_env.models import RunLimits, TaskCase
from agent_env.user_simulator import SimulatorOutputError, add_usage


class AgentEnv:
    """Own the conversation loop while keeping agent and user implementations swappable."""

    def __init__(
        self,
        task: TaskCase,
        simulator: Any,
        agent: Any,
        limits: RunLimits,
        servers: list[str],
        server_selection: str,
        run_metadata: dict[str, Any] | None = None,
        required_servers: list[str] | None = None,
        optional_servers: list[str] | None = None,
        tool_allowlists: dict[str, list[str]] | None = None,
    ) -> None:
        self.task = task
        self.simulator = simulator
        self.agent = agent
        self.limits = limits
        self.servers = servers
        self.server_selection = server_selection
        self.run_metadata = dict(run_metadata or {})
        self.required_servers = list(
            dict.fromkeys(required_servers if required_servers is not None else servers)
        )
        self.optional_servers = list(dict.fromkeys(optional_servers or []))
        self.tool_allowlists = {
            server: list(dict.fromkeys(tools))
            for server, tools in (tool_allowlists or {}).items()
        }

    async def run(self) -> dict[str, Any]:
        visible_history: list[dict[str, str]] = []
        simulator_usage: dict[str, int] = {}
        trajectory: dict[str, Any] = {
            "schema_version": "2.0",
            "scenario_id": self.task.scenario_id,
            "profile_id": self.task.profile_id,
            "domain": self.task.domain,
            "user_task": dict(self.task.user_task),
            "task": {
                "scenario_id": self.task.scenario_id,
                "profile_id": self.task.profile_id,
                "domain": self.task.domain,
                "user_task": dict(self.task.user_task),
                "evaluation_criteria": list(self.task.evaluation_criteria),
                "metadata": dict(self.task.metadata),
            },
            "environment": {
                "servers": self.servers,
                "server_selection": self.server_selection,
                "server_policy": {
                    "required_servers": self.required_servers,
                    "optional_servers": self.optional_servers,
                    **(
                        {"tool_allowlists": self.tool_allowlists}
                        if self.tool_allowlists
                        else {}
                    ),
                },
                "limits": self.limits.to_dict(),
            },
            "run": self.run_metadata,
            # Flat aliases keep older analysis notebooks working.
            "servers": self.servers,
            "server_selection": self.server_selection,
            "started_at": time.time(),
            "turns": [],
            "status": "running",
            "termination_reason": None,
        }

        try:
            await self.agent.initialize()
            trajectory["mcp_session_id"] = self.agent.session_id
            connections = dict(self.agent.connections or {})
            failed_servers = set(connections.get("failed_servers", []))
            connections["failed_required_servers"] = [
                name for name in self.required_servers if name in failed_servers
            ]
            connections["failed_optional_servers"] = [
                name for name in self.optional_servers if name in failed_servers
            ]
            trajectory["server_connections"] = connections
            trajectory["environment"]["connections"] = connections

            user_turns = (
                count()
                if self.limits.max_user_turns is None
                else range(self.limits.max_user_turns)
            )
            for _ in user_turns:
                try:
                    simulated = await self.simulator.next_turn(visible_history)
                except SimulatorOutputError as exc:
                    add_usage(simulator_usage, exc.usage)
                    trajectory["status"] = "simulator_error"
                    trajectory["termination_reason"] = "simulator_output_error"
                    trajectory["error"] = str(exc)
                    trajectory["simulator_error"] = exc.to_dict()
                    break
                add_usage(simulator_usage, simulated.usage)
                trajectory["turns"].append(
                    {
                        "role": "user",
                        "content": simulated.message,
                        "simulator_status": simulated.status,
                        "simulator_remaining_requirement": (
                            simulated.remaining_requirement
                        ),
                        "simulator_usage": simulated.usage,
                        "simulator_format_attempts": simulated.format_attempts,
                        "simulator_format_failures": list(simulated.format_failures),
                        "simulator_llm_outputs": [
                            dict(item) for item in simulated.llm_outputs
                        ],
                    }
                )
                visible_history.append({"role": "user", "content": simulated.message})

                if simulated.status != "continue":
                    trajectory["status"] = simulated.status
                    trajectory["termination_reason"] = f"simulator_{simulated.status}"
                    break

                result = await self.agent.respond(
                    simulated.message, self.limits.turn_timeout_seconds
                )
                trajectory["turns"].append(result.to_turn_record())
                visible_history.append({"role": "assistant", "content": result.response})
                if result.error:
                    trajectory["status"] = "agent_error"
                    trajectory["termination_reason"] = result.termination_reason or result.error
                    break
            else:
                trajectory["status"] = "max_user_turns"
                trajectory["termination_reason"] = "max_user_turns"
        except Exception as exc:
            trajectory["status"] = "environment_error"
            trajectory["termination_reason"] = type(exc).__name__
            trajectory["error"] = str(exc)
        finally:
            try:
                await self.agent.close()
            except Exception as exc:
                trajectory["close_error"] = str(exc)
                if trajectory["status"] == "running":
                    trajectory["status"] = "environment_error"
                    trajectory["termination_reason"] = "agent_close_error"
            finally:
                trajectory["completed_at"] = time.time()

        trajectory["tools_info"] = [
            call
            for turn in trajectory["turns"]
            if turn.get("role") == "assistant"
            for call in turn.get("execution_results", [])
        ]
        trajectory["total_tool_calls"] = len(trajectory["tools_info"])
        agent_prompt_tokens = sum(
            int(turn.get("prompt_tokens", 0) or 0)
            for turn in trajectory["turns"]
            if turn.get("role") == "assistant"
        )
        agent_completion_tokens = sum(
            int(turn.get("completion_tokens", 0) or 0)
            for turn in trajectory["turns"]
            if turn.get("role") == "assistant"
        )
        agent_total_tokens = sum(
            int(turn.get("total_tokens", 0) or 0)
            for turn in trajectory["turns"]
            if turn.get("role") == "assistant"
        )
        simulator_total_tokens = int(simulator_usage.get("total_tokens", 0) or 0)
        trajectory["token_usage"] = {
            "agent": {
                "prompt_tokens": agent_prompt_tokens,
                "completion_tokens": agent_completion_tokens,
                "total_tokens": agent_total_tokens,
            },
            "simulator": dict(simulator_usage),
            "known_total_tokens": agent_total_tokens + simulator_total_tokens,
        }
        # Backward-compatible alias: total_tokens continues to mean evaluated
        # agent tokens. New analysis should use token_usage.known_total_tokens.
        trajectory["total_tokens"] = agent_total_tokens
        trajectory["total_known_tokens"] = agent_total_tokens + simulator_total_tokens
        trajectory["evaluation"] = evaluate_trajectory(trajectory)
        trajectory["metrics"] = trajectory["evaluation"]["metrics"]
        return trajectory
