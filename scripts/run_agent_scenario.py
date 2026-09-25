#!/usr/bin/env python3
"""Run one profile-backed task with the PydanticAI MCP agent."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_PROFILES = (
    PROJECT_DIR
    / "artifacts/profile_pool/profiles.jsonl"
)
DEFAULT_SCENARIOS = (
    PROJECT_DIR
    / "artifacts/scenario/cases.jsonl"
)
sys.path.insert(0, str(PROJECT_DIR))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one tau2-style user/agent trajectory over MCP servers."
    )
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument(
        "--profiles", type=Path,
        default=DEFAULT_PROFILES,
    )
    parser.add_argument(
        "--scenarios", type=Path,
        default=DEFAULT_SCENARIOS,
    )
    parser.add_argument(
        "--user-policy", type=Path,
        default=PROJECT_DIR / "config/user_simulator_policy.txt",
    )
    parser.add_argument(
        "--domains-config", type=Path,
        default=PROJECT_DIR / "config/domains.json",
    )
    parser.add_argument(
        "--env-config", type=Path,
        default=PROJECT_DIR / "config/agent_env.json",
    )
    parser.add_argument("--servers", nargs="+")
    parser.add_argument("--max-user-turns", type=int)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--sim-max-tokens", type=int)
    parser.add_argument("--sim-temperature", type=float)
    parser.add_argument("--sim-format-attempts", type=int)
    parser.add_argument("--max-agent-rounds", type=int)
    parser.add_argument("--max-tool-calls", type=int)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--sim-model")
    parser.add_argument("--sim-base-url")
    parser.add_argument("--sim-api-key")
    parser.add_argument("--agent-model")
    parser.add_argument("--agent-base-url")
    parser.add_argument("--agent-api-key")
    parser.add_argument(
        "--agent-api-url",
        help="URL of a running agent API service; direct mode is the default.",
    )
    parser.add_argument(
        "--shared-mcp-config",
        type=Path,
        help="Batch-owned server-to-HTTP endpoint mapping.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Trajectory path; defaults to "
            "artifacts/trajectory_runs/latest/<scenario-id>.json."
        ),
    )
    return parser


def apply_limit_overrides(limits: Any, args: argparse.Namespace) -> Any:
    from agent_env.execution_limits import ExecutionLimits

    agent = replace(
        limits.agent,
        **{
            field: value
            for field, value in {
                "max_rounds": args.max_agent_rounds,
                "max_tool_calls": args.max_tool_calls,
                "max_total_tokens": args.max_total_tokens,
            }.items()
            if value is not None
        },
    )
    # Force dataclass validation here instead of allowing a misleading runtime run.
    agent = ExecutionLimits.from_mapping(agent.to_dict())
    return replace(
        limits,
        agent=agent,
        **{
            field: value
            for field, value in {
                "max_user_turns": args.max_user_turns,
                "turn_timeout_seconds": args.timeout,
                "simulator_max_tokens": args.sim_max_tokens,
                "simulator_temperature": args.sim_temperature,
                "simulator_format_attempts": args.sim_format_attempts,
            }.items()
            if value is not None
        },
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    # Server commands contain project-relative cwd values.
    os.chdir(PROJECT_DIR)

    from agent_env.agent_adapter import RemoteAgentAdapter
    from agent_env.case_loader import load_task_case
    from agent_env.domain_registry import (
        load_available_server_names,
        load_domain_registry,
        select_servers,
        validate_domain_servers,
    )
    from agent_env.orchestrator import AgentEnv
    from agent_env.settings import load_run_limits, resolve_model_settings
    from agent_env.user_simulator import UserSimulator
    from llm.factory import LLMFactory, ModelConfig
    from llm.direct_pool import direct_pool_audit

    task = load_task_case(
        args.profiles.resolve(), args.scenarios.resolve(), args.scenario_id
    )
    limits = apply_limit_overrides(
        load_run_limits(args.env_config.resolve()), args
    )
    settings = resolve_model_settings(args)
    direct_api = direct_pool_audit()
    if direct_api is None:
        raise ValueError(
            "Trajectory generation requires direct multi-key API transport"
        )
    registry = load_domain_registry(args.domains_config.resolve())
    available = load_available_server_names(PROJECT_DIR / "mcp_servers/commands.json")
    validate_domain_servers(registry, available)

    if args.servers:
        servers = list(dict.fromkeys(args.servers))
        unknown = set(servers) - available
        if unknown:
            raise ValueError(f"Unknown MCP servers: {sorted(unknown)}")
        required_servers = servers
        optional_servers: list[str] = []
        tool_allowlists: dict[str, list[str]] = {}
        server_selection = "cli_override"
    else:
        domain_spec = registry[task.domain]
        servers = select_servers(task.domain, registry)
        required_servers = list(dict.fromkeys(domain_spec.core_servers))
        optional_servers = list(dict.fromkeys(domain_spec.optional_servers))
        # Scenarios are natural-language simulator inputs. Tool choices made
        # while synthesizing a scenario must never restrict the evaluated
        # agent. Every tool discovered from the domain's servers is visible.
        tool_allowlists = {}
        server_selection = "domain_full_tool_catalog"

    shared_mcp_urls: dict[str, str] = {}
    if args.shared_mcp_config is not None:
        from agent_env.shared_mcp import load_shared_mcp_urls

        shared_mcp_urls = load_shared_mcp_urls(
            args.shared_mcp_config.resolve(),
            selected_servers=servers,
        )

    simulator_provider = await LLMFactory.create_llm_provider(
        ModelConfig(
            name="user-simulator",
            provider_type="openai_compatible",
            api_key=settings["sim_api_key"],
            base_url=settings["sim_base_url"],
            model_name=settings["sim_model"],
        )
    )
    simulator = UserSimulator(
        provider=simulator_provider,
        profile=task.profile,
        user_task=task.user_task,
        behavior_guidelines=args.user_policy.resolve().read_text(encoding="utf-8").strip(),
        max_tokens=limits.simulator_max_tokens,
        temperature=limits.simulator_temperature,
        max_format_attempts=limits.simulator_format_attempts,
    )

    api_url = (args.agent_api_url or os.environ.get("MCP_BENCH_API_URL") or "").strip()
    if api_url:
        agent = RemoteAgentAdapter(
            api_url=api_url,
            model_settings=settings,
            servers=servers,
            execution_limits=limits.agent,
            tool_allowlists=tool_allowlists,
            shared_mcp_urls=shared_mcp_urls,
        )
        transport = "api"
        agent_runtime = "external-api"
    else:
        from agent_env.pydantic_agent import PydanticAIAgentAdapter

        agent = PydanticAIAgentAdapter(
            base_url=settings["agent_base_url"],
            api_key=settings["agent_api_key"],
            model_name=settings["agent_model"],
            server_names=servers,
            execution_limits=limits.agent,
            tool_allowlists=tool_allowlists,
            shared_mcp_urls=shared_mcp_urls,
            commands_path=PROJECT_DIR / "mcp_servers/commands.json",
            project_dir=PROJECT_DIR,
        )
        transport = "direct"
        agent_runtime = "pydantic-ai"

    env = AgentEnv(
        task=task,
        simulator=simulator,
        agent=agent,
        limits=limits,
        servers=servers,
        server_selection=server_selection,
        required_servers=required_servers,
        optional_servers=optional_servers,
        tool_allowlists=tool_allowlists,
        run_metadata={
            "agent_model": settings["agent_model"],
            "simulator_model": settings["sim_model"],
            "agent_transport": transport,
            "agent_runtime": agent_runtime,
            "agent_api_url": api_url or None,
            "direct_api": direct_api,
            "shared_mcp_servers": sorted(shared_mcp_urls),
        },
    )
    trajectory = await env.run()
    # Flat aliases used by the previous profile-scenario runner.
    trajectory.update(
        {
            "agent_model": settings["agent_model"],
            "simulator_model": settings["sim_model"],
            "agent_transport": transport,
            "agent_runtime": agent_runtime,
            "agent_api_url": api_url or None,
        }
    )
    return trajectory


def main() -> int:
    from agent_env.settings import load_project_env
    from agent_env.trajectory import write_trajectory

    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    output = (
        args.output
        or PROJECT_DIR
        / "artifacts/trajectory_runs/latest"
        / f"{args.scenario_id}.json"
    ).resolve()
    try:
        trajectory = asyncio.run(run(args))
        write_trajectory(output, trajectory)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        from agent_env.recovery import is_fatal_provider_error

        return 42 if is_fatal_provider_error(exc) else 1
    print(
        f"Trajectory saved to {output} "
        f"(status={trajectory['status']}, tools={trajectory['total_tool_calls']})"
    )
    return 1 if trajectory["status"] == "environment_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
