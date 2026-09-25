"""Configuration loading shared by single and batch env runners."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from agent_env.models import RunLimits


_ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _load_env_file_without_dependency(path: Path) -> None:
    """Load simple ``.env`` assignments when python-dotenv is unavailable.

    This deliberately keeps exported environment values authoritative and covers
    the repository's local key/value configuration without interpreting shell
    expansions or executing any content from the file.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_PATTERN.fullmatch(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_project_env(project_dir: Path) -> None:
    """Load local model/server secrets without overriding exported values."""
    os.environ.setdefault("UV_CACHE_DIR", str(project_dir / ".uv-cache"))
    # MCP servers may request a Python version that is not installed globally
    # (for example, medcalc requires Python 3.12).  Keep uv-managed runtimes in
    # the project so trajectory workers do not depend on a writable user-level
    # uv installation directory.
    os.environ.setdefault(
        "UV_PYTHON_INSTALL_DIR", str(project_dir / ".uv-python")
    )
    os.environ.setdefault(
        "BIOMCP_CACHE_DIR", str(project_dir / ".cache" / "biomcp")
    )
    # Several vendored MCP servers resolve transient uv environments. Python
    # 3.13 is installed on the host and has compatible wheels for the catalog.
    # Pin it so server processes use one compatible project-local runtime.
    os.environ.setdefault("UV_PYTHON", "3.13")
    try:
        from dotenv import load_dotenv
    except ImportError:
        _load_env_file_without_dependency(project_dir / ".env")
        _load_env_file_without_dependency(project_dir / ".env.mcp")
    else:
        load_dotenv(project_dir / ".env", override=False)
        load_dotenv(project_dir / ".env.mcp", override=False)

def load_run_limits(path: Path) -> RunLimits:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Agent env config must be a JSON object")
    return RunLimits.from_mapping(value.get("limits", value))


def resolve_model_settings(args: Any) -> dict[str, str]:
    def choose(explicit: str | None, primary: str, fallback: str) -> str | None:
        return explicit or os.environ.get(primary) or os.environ.get(fallback)

    settings = {
        "sim_model": choose(args.sim_model, "SIMULATOR_MODEL_NAME", "MY_MODEL_NAME"),
        "sim_base_url": choose(args.sim_base_url, "SIMULATOR_MODEL_BASE_URL", "MY_MODEL_BASE_URL"),
        "sim_api_key": choose(args.sim_api_key, "SIMULATOR_MODEL_API_KEY", "MY_MODEL_API_KEY"),
        "agent_model": choose(args.agent_model, "AGENT_MODEL_NAME", "MY_MODEL_NAME"),
        "agent_base_url": choose(args.agent_base_url, "AGENT_MODEL_BASE_URL", "MY_MODEL_BASE_URL"),
        "agent_api_key": choose(args.agent_api_key, "AGENT_MODEL_API_KEY", "MY_MODEL_API_KEY"),
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise ValueError(f"Missing model configuration: {', '.join(missing)}")
    return settings  # type: ignore[return-value]
