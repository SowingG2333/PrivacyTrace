"""Lifecycle helper for MCP servers that expose streamable HTTP."""

from __future__ import annotations

import asyncio
import logging
import random
import subprocess
import tempfile
from typing import Mapping, Sequence, TextIO

import aiohttp


logger = logging.getLogger(__name__)

PORT_SEARCH_ATTEMPTS = 100
RANDOM_PORT_MIN = 10_000
RANDOM_PORT_MAX = 50_000
HEALTH_CHECK_TIMEOUT_SECONDS = 2
PROCESS_WAIT_TIMEOUT_SECONDS = 5


class HTTPServerLauncher:
    """Start one local HTTP MCP process and release it deterministically."""

    def __init__(
        self,
        *,
        server_name: str,
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: str,
        port: int,
        endpoint: str = "/mcp",
    ) -> None:
        self.server_name = server_name
        self.command = list(command)
        self.original_command = list(command)
        self.env = dict(env)
        self.cwd = cwd
        self.port = port
        self.endpoint = endpoint
        self.process: subprocess.Popen[str] | None = None
        self.output: TextIO | None = None

    def _command_for_port(self, original_port: int, new_port: int) -> list[str]:
        if original_port == new_port:
            return list(self.original_command)
        updated: list[str] = []
        index = 0
        while index < len(self.original_command):
            argument = self.original_command[index]
            if f"--port {original_port}" in argument:
                updated.append(
                    argument.replace(
                        f"--port {original_port}",
                        f"--port {new_port}",
                    )
                )
            elif f"--port={original_port}" in argument:
                updated.append(
                    argument.replace(
                        f"--port={original_port}",
                        f"--port={new_port}",
                    )
                )
            elif argument == "--port":
                updated.append(argument)
                index += 1
                if index < len(self.original_command):
                    updated.append(
                        str(new_port)
                        if self.original_command[index] == str(original_port)
                        else self.original_command[index]
                    )
            else:
                updated.append(argument)
            index += 1
        return updated

    async def start(self) -> bool:
        original_port = self.port
        for attempt in range(PORT_SEARCH_ATTEMPTS):
            current_port = random.randint(RANDOM_PORT_MIN, RANDOM_PORT_MAX)
            self.port = current_port
            self.command = self._command_for_port(original_port, current_port)
            child_env = dict(self.env)
            child_env["MCP_SERVER_PORT"] = str(current_port)

            if self.output is not None:
                self.output.close()
            self.output = tempfile.TemporaryFile(mode="w+t")
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=child_env,
                stdout=self.output,
                stderr=subprocess.STDOUT,
                text=True,
            )
            await asyncio.sleep(3)

            if self.process.poll() is None:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"http://127.0.0.1:{self.port}{self.endpoint}",
                            timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
                        ):
                            pass
                except Exception:
                    # Some MCP HTTP endpoints reject GET but are ready for POST.
                    pass
                logger.info(
                    "Started HTTP MCP server %s on port %d",
                    self.server_name,
                    self.port,
                )
                return True

            self.output.flush()
            self.output.seek(0)
            server_output = self.output.read()
            if (
                "EADDRINUSE" in server_output
                or "address already in use" in server_output.lower()
            ):
                continue
            logger.error(
                "HTTP MCP server %s failed: %s",
                self.server_name,
                server_output[-4000:],
            )
            return False
        return False

    async def stop(self) -> None:
        process = self.process
        self.process = None
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(
                        process.wait,
                        timeout=PROCESS_WAIT_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)
        finally:
            if self.output is not None:
                self.output.close()
                self.output = None
