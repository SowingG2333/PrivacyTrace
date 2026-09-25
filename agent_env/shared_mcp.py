"""Launch one reusable local HTTP proxy for each selected MCP server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, TextIO
from urllib.parse import urlparse


SHARED_MCP_ENDPOINT = "/mcp"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "server"


def normalize_shared_mcp_urls(
    value: Any,
    *,
    selected_servers: Iterable[str] | None = None,
) -> dict[str, str]:
    """Validate a server-name to streamable-HTTP endpoint mapping."""
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("shared MCP endpoints must be an object")
    selected = set(selected_servers or ())
    endpoints: dict[str, str] = {}
    for server_name, raw_url in value.items():
        if not isinstance(server_name, str) or not server_name.strip():
            raise ValueError("shared MCP endpoint names must be non-empty strings")
        if selected and server_name not in selected:
            raise ValueError(
                f"Shared MCP endpoint references an unselected server: {server_name}"
            )
        if not isinstance(raw_url, str):
            raise ValueError(f"Shared MCP endpoint for {server_name} must be a URL")
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid shared MCP endpoint for {server_name}: {raw_url}")
        endpoints[server_name] = raw_url.rstrip("/")
    return endpoints


def load_shared_mcp_urls(
    path: Path,
    *,
    selected_servers: Iterable[str] | None = None,
) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, Mapping) and "servers" in value:
        value = value["servers"]
    endpoints = normalize_shared_mcp_urls(value)
    if selected_servers is None:
        return endpoints
    selected = set(selected_servers)
    return {
        server_name: url
        for server_name, url in endpoints.items()
        if server_name in selected
    }


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


class SharedMCPPool:
    """Own batch-scoped proxy processes and their upstream MCP processes."""

    def __init__(
        self,
        *,
        project_dir: Path,
        commands_path: Path,
        server_names: Iterable[str],
        runtime_dir: Path,
        startup_timeout: float = 180.0,
    ) -> None:
        self.project_dir = project_dir.resolve()
        self.commands_path = commands_path.resolve()
        self.server_names = tuple(dict.fromkeys(server_names))
        self.runtime_dir = runtime_dir.resolve()
        self.startup_timeout = startup_timeout
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        self.output_handles: dict[str, TextIO] = {}
        self.endpoints: dict[str, str] = {}
        self.endpoint_config_path = self.runtime_dir / "endpoints.json"

    def _stop_process(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait()

    def _upstream_process_groups(self, server_name: str) -> list[int]:
        if os.name != "posix":
            return []
        ready_path = self.runtime_dir / "ready" / f"{_slug(server_name)}.json"
        try:
            value = json.loads(ready_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        groups = value.get("upstream_process_groups", [])
        if not isinstance(groups, list):
            return []
        return [
            group for group in groups
            if isinstance(group, int) and group > 1 and group != os.getpgrp()
        ]

    @staticmethod
    def _stop_process_group(process_group: int) -> None:
        if os.name != "posix" or process_group <= 1:
            return
        try:
            os.killpg(process_group, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)
        try:
            os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def stop(self) -> None:
        upstream_groups = {
            server_name: self._upstream_process_groups(server_name)
            for server_name in self.processes
        }
        for process in self.processes.values():
            self._stop_process(process)
        for groups in upstream_groups.values():
            for process_group in groups:
                self._stop_process_group(process_group)
        self.processes.clear()
        for handle in self.output_handles.values():
            handle.close()
        self.output_handles.clear()

    def _failure_detail(self, server_name: str) -> str:
        log_path = self.runtime_dir / "logs" / f"{_slug(server_name)}.log"
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-4000:]

    def start(self) -> Path:
        if not self.server_names:
            raise ValueError("Shared MCP pool requires at least one server")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        log_dir = self.runtime_dir / "logs"
        ready_dir = self.runtime_dir / "ready"
        log_dir.mkdir(parents=True, exist_ok=True)
        ready_dir.mkdir(parents=True, exist_ok=True)

        try:
            for server_name in self.server_names:
                slug = _slug(server_name)
                ready_path = ready_dir / f"{slug}.json"
                ready_path.unlink(missing_ok=True)
                port = _reserve_loopback_port()
                endpoint = f"http://127.0.0.1:{port}{SHARED_MCP_ENDPOINT}"
                log_path = log_dir / f"{slug}.log"
                output = log_path.open("w", encoding="utf-8")
                command = [
                    sys.executable,
                    str(self.project_dir / "scripts/serve_shared_mcp.py"),
                    "--server-name",
                    server_name,
                    "--commands",
                    str(self.commands_path),
                    "--port",
                    str(port),
                    "--ready-file",
                    str(ready_path),
                    "--upstream-log",
                    str(log_dir / f"{slug}.upstream.log"),
                ]
                process = subprocess.Popen(
                    command,
                    cwd=self.project_dir,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name == "posix",
                )
                self.processes[server_name] = process
                self.output_handles[server_name] = output
                self.endpoints[server_name] = endpoint

            deadline = time.monotonic() + self.startup_timeout
            pending = set(self.server_names)
            while pending and time.monotonic() < deadline:
                for server_name in list(pending):
                    process = self.processes[server_name]
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"Shared MCP server {server_name!r} exited during startup. "
                            f"{self._failure_detail(server_name)}"
                        )
                    ready_path = ready_dir / f"{_slug(server_name)}.json"
                    if not ready_path.is_file():
                        continue
                    parsed = urlparse(self.endpoints[server_name])
                    try:
                        with socket.create_connection(
                            (parsed.hostname or "127.0.0.1", parsed.port or 80),
                            timeout=0.2,
                        ):
                            pass
                    except OSError:
                        continue
                    pending.remove(server_name)
                if pending:
                    time.sleep(0.1)
            if pending:
                details = {
                    name: self._failure_detail(name) for name in sorted(pending)
                }
                raise RuntimeError(
                    f"Shared MCP startup timed out for {sorted(pending)}: {details}"
                )

            payload = {
                "transport": "shared_streamable_http",
                "servers": self.endpoints,
            }
            temporary = self.endpoint_config_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.endpoint_config_path)
            return self.endpoint_config_path
        except BaseException:
            self.stop()
            raise
