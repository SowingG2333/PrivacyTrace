"""Protocol and HTTP adapter for the evaluated PydanticAI MCP agent."""

from __future__ import annotations

from typing import Any, Protocol

from agent_env.execution_limits import ExecutionLimits
from agent_env.models import AgentTurnResult


class AgentAdapter(Protocol):
    session_id: str | None
    connections: dict[str, Any] | None

    async def initialize(self) -> None: ...
    async def respond(self, message: str, timeout_seconds: int) -> AgentTurnResult: ...
    async def close(self) -> None: ...


class RemoteAgentAdapter:
    def __init__(
        self,
        api_url: str,
        model_settings: dict[str, str],
        servers: list[str],
        execution_limits: ExecutionLimits,
        tool_allowlists: dict[str, list[str]] | None = None,
        shared_mcp_urls: dict[str, str] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.model_settings = model_settings
        self.servers = servers
        self.execution_limits = execution_limits
        self.tool_allowlists = {
            server: list(tools)
            for server, tools in (tool_allowlists or {}).items()
        }
        self.shared_mcp_urls = dict(shared_mcp_urls or {})
        self.session_id: str | None = None
        self.connections: dict[str, Any] | None = None
        self._http: Any = None

    async def initialize(self) -> None:
        import aiohttp

        self._http = aiohttp.ClientSession()
        payload = {
            "model": f"agent-{self.model_settings['agent_model']}",
            "base_url": self.model_settings["agent_base_url"],
            "api_key": self.model_settings["agent_api_key"],
            "model_name": self.model_settings["agent_model"],
            "servers": self.servers,
            "tool_allowlists": self.tool_allowlists,
            "shared_mcp_urls": self.shared_mcp_urls,
            "execution_limits": self.execution_limits.to_dict(),
        }
        try:
            async with self._http.post(f"{self.api_url}/sessions", json=payload) as response:
                if response.status >= 400:
                    raise RuntimeError(
                        f"PrivacyTrace Agent API session creation failed ({response.status}): "
                        f"{await response.text()}"
                    )
                value = await response.json()
            self.session_id = value["session_id"]
            async with self._http.get(
                f"{self.api_url}/sessions/{self.session_id}/connections"
            ) as response:
                if response.status == 200:
                    self.connections = await response.json()
        except Exception:
            await self.close()
            raise

    async def respond(self, message: str, timeout_seconds: int) -> AgentTurnResult:
        if not self._http or not self.session_id:
            raise RuntimeError("API agent is not initialized")
        async with self._http.post(
            f"{self.api_url}/sessions/{self.session_id}/chat",
            json={"message": message, "timeout": timeout_seconds},
        ) as response:
            if response.status >= 400:
                raise RuntimeError(
                    f"PrivacyTrace Agent API chat failed ({response.status}): {await response.text()}"
                )
            value = await response.json()
        async with self._http.get(f"{self.api_url}/sessions/{self.session_id}") as response:
            if response.status == 200:
                session = await response.json()
                assistants = [
                    turn for turn in session.get("history", [])
                    if turn.get("role") == "assistant"
                ]
                if assistants:
                    value.update(assistants[-1].get("metadata", {}))
        return AgentTurnResult.from_mapping(value)

    async def close(self) -> None:
        if self._http is None:
            return
        try:
            if self.session_id:
                async with self._http.delete(
                    f"{self.api_url}/sessions/{self.session_id}"
                ) as response:
                    await response.read()
        finally:
            await self._http.close()
            self._http = None
