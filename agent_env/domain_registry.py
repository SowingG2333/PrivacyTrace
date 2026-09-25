"""Load and validate domain-to-MCP-server policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from agent_env.models import DomainSpec


def load_domain_registry(path: Path) -> dict[str, DomainSpec]:
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Domain configuration must be a non-empty JSON object")

    registry: dict[str, DomainSpec] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"Configuration for {name} must be an object")
        core = value.get("core_servers")
        if not isinstance(core, list) or not core or not all(isinstance(x, str) and x for x in core):
            raise ValueError(f"{name}.core_servers must be a non-empty string list")
        optional = value.get("optional_servers", [])
        if not isinstance(optional, list) or not all(
            isinstance(x, str) and x for x in optional
        ):
            raise ValueError(f"{name}.optional_servers must be a string list")
        overlap = set(core) & set(optional)
        if overlap:
            raise ValueError(
                f"{name} servers cannot be both core and optional: {sorted(overlap)}"
            )
        raw_allowlists = value.get("tool_allowlists", {})
        if not isinstance(raw_allowlists, dict):
            raise ValueError(f"{name}.tool_allowlists must be an object")
        selected_servers = {*core, *optional}
        unknown_allowlist_servers = set(raw_allowlists) - selected_servers
        if unknown_allowlist_servers:
            raise ValueError(
                f"{name}.tool_allowlists contains unselected servers: "
                f"{sorted(unknown_allowlist_servers)}"
            )
        tool_allowlists: dict[str, tuple[str, ...]] = {}
        for server, tools in raw_allowlists.items():
            if (
                not isinstance(server, str)
                or not server
                or not isinstance(tools, list)
                or not tools
                or not all(isinstance(tool, str) and tool for tool in tools)
            ):
                raise ValueError(
                    f"{name}.tool_allowlists entries must be non-empty string lists"
                )
            if len(set(tools)) != len(tools):
                raise ValueError(
                    f"{name}.tool_allowlists.{server} contains duplicate tools"
                )
            tool_allowlists[server] = tuple(tools)
        registry[name] = DomainSpec(
            name=name,
            description=str(value.get("description", "")),
            core_servers=tuple(core),
            optional_servers=tuple(optional),
            tool_allowlists=tool_allowlists,
            coverage_note=str(value.get("coverage_note", "")),
        )
    return registry


def load_available_server_names(commands_path: Path) -> set[str]:
    with commands_path.open("r", encoding="utf-8") as handle:
        commands = json.load(handle)
    if not isinstance(commands, dict):
        raise ValueError("commands.json must contain a JSON object")
    return set(commands)


def validate_domain_servers(
    registry: Mapping[str, DomainSpec], available_servers: Iterable[str]
) -> None:
    available = set(available_servers)
    configured = {
        server
        for spec in registry.values()
        for server in spec.select_servers()
    }
    unknown = configured - available
    if unknown:
        raise ValueError(f"Unknown MCP servers in domain config: {sorted(unknown)}")


def select_servers(
    domain: str,
    registry: Mapping[str, DomainSpec],
) -> list[str]:
    try:
        return registry[domain].select_servers()
    except KeyError as exc:
        raise ValueError(f"Unsupported scenario domain: {domain}") from exc
