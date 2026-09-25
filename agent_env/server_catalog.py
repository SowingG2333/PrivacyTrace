"""Load the MCP server catalog and build least-privilege process settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import shlex
import sys
from typing import Any, Mapping


SAFE_SERVER_ENV_NAMES = (
    "BIOMCP_CACHE_DIR",
    "DUFFEL_API_VERSION",
    "DUFFEL_BASE_URL",
    "DUFFEL_MAX_INFLIGHT",
    "DUFFEL_MAX_RETRIES",
    "DUFFEL_SUPPLIER_TIMEOUT_MS",
    "DUFFEL_TIMEOUT_SECONDS",
    "FRUITYVICE_TIMEOUT_SECONDS",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
    "UV_CACHE_DIR",
    "UV_PYTHON",
    "UV_PYTHON_INSTALL_DIR",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@dataclass(frozen=True)
class ServerProcess:
    command: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path


def load_server_commands(path: Path) -> dict[str, dict[str, Any]]:
    """Load and minimally validate ``mcp_servers/commands.json``."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"MCP server catalog must be an object: {path}")
    commands: dict[str, dict[str, Any]] = {}
    for name, config in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"MCP server catalog has an invalid name: {name!r}")
        if not isinstance(config, dict):
            raise ValueError(f"MCP server {name!r} config must be an object")
        if not str(config.get("cmd", "")).strip():
            raise ValueError(f"MCP server {name!r} has an empty command")
        commands[name] = config
    return commands


def build_server_process(
    *,
    project_dir: Path,
    server_name: str,
    config: Mapping[str, Any],
) -> ServerProcess:
    """Resolve one catalog entry into a safe subprocess specification."""
    command = shlex.split(str(config.get("cmd", "")))
    if not command:
        raise ValueError(f"MCP server {server_name!r} has an empty command")
    if command[0] in {"python", "python3"}:
        command[0] = sys.executable

    cwd_value = str(config.get("cwd", "")).strip()
    if cwd_value.startswith("../"):
        cwd = project_dir / "mcp_servers" / cwd_value[3:]
    elif cwd_value:
        cwd = project_dir / cwd_value
    else:
        cwd = project_dir
    cwd = cwd.resolve()
    if not cwd.is_dir():
        raise ValueError(
            f"MCP server {server_name!r} working directory does not exist: {cwd}"
        )

    env = {
        name: os.environ[name]
        for name in SAFE_SERVER_ENV_NAMES
        if name in os.environ
    }
    for name in config.get("env", []):
        if name in os.environ:
            env[name] = os.environ[name]

    return ServerProcess(
        command=tuple(command),
        env=env,
        cwd=cwd,
    )
