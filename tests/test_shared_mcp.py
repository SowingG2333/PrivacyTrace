import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from agent_env.execution_limits import ExecutionLimits
from agent_env.pydantic_agent import PydanticAIAgentAdapter
from agent_env.shared_mcp import (
    SharedMCPPool,
    load_shared_mcp_urls,
    normalize_shared_mcp_urls,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HAS_FASTMCP = importlib.util.find_spec("fastmcp") is not None


class SharedMCPTests(unittest.TestCase):
    def test_endpoint_config_validation_and_filtering(self):
        value = {
            "Wikipedia": "http://127.0.0.1:41001/mcp",
            "FruityVice": "http://127.0.0.1:41002/mcp/",
        }
        normalized = normalize_shared_mcp_urls(value)
        self.assertEqual(
            normalized["FruityVice"],
            "http://127.0.0.1:41002/mcp",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "endpoints.json"
            path.write_text(
                json.dumps({"transport": "shared_streamable_http", "servers": value}),
                encoding="utf-8",
            )
            selected = load_shared_mcp_urls(
                path,
                selected_servers=["FruityVice"],
            )
        self.assertEqual(
            selected,
            {"FruityVice": "http://127.0.0.1:41002/mcp"},
        )

    def test_rejects_non_http_endpoint(self):
        with self.assertRaisesRegex(ValueError, "Invalid shared MCP endpoint"):
            normalize_shared_mcp_urls({"FruityVice": "stdio://local"})

    @unittest.skipUnless(
        HAS_FASTMCP and (PROJECT_ROOT / "mcp_servers/fruityvice-mcp/server.py").is_file(),
        "FastMCP server support or FruityVice fixture is unavailable",
    )
    def test_two_clients_share_one_upstream_server(self):
        async def exercise(url: str):
            from fastmcp import Client

            async def call(index: int):
                async with Client(url, timeout=30, init_timeout=30) as client:
                    tools = await client.list_tools()
                    result = await client.call_tool(
                        "get_fruit_nutrition",
                        {"fruit_name": "apple"},
                    )
                    return index, [tool.name for tool in tools], result.is_error

            return await asyncio.gather(call(1), call(2))

        with tempfile.TemporaryDirectory() as directory:
            pool = SharedMCPPool(
                project_dir=PROJECT_ROOT,
                commands_path=PROJECT_ROOT / "mcp_servers/commands.json",
                server_names=["FruityVice"],
                runtime_dir=Path(directory),
                startup_timeout=30,
            )
            try:
                config_path = pool.start()
                endpoints = load_shared_mcp_urls(config_path)
                values = asyncio.run(exercise(endpoints["FruityVice"]))
            finally:
                pool.stop()

        self.assertEqual(len(values), 2)
        self.assertTrue(all("get_fruit_nutrition" in value[1] for value in values))
        self.assertTrue(all(value[2] is False for value in values))
        self.assertEqual(pool.processes, {})

    @unittest.skipUnless(
        HAS_FASTMCP
        and importlib.util.find_spec("pydantic_ai") is not None
        and (PROJECT_ROOT / "mcp_servers/fruityvice-mcp/server.py").is_file(),
        "FastMCP, PydanticAI, or the unlicensed FruityVice fixture is unavailable",
    )
    def test_agent_adapter_uses_shared_endpoint_with_full_tool_catalog(self):
        from pydantic_ai.models.test import TestModel

        async def exercise(endpoint: str):
            adapter = PydanticAIAgentAdapter(
                base_url="http://unused.test/v1",
                api_key="test",
                model_name="test",
                model=TestModel(
                    call_tools=["fruityvice_get_fruit_nutrition"],
                    custom_output_text="Lookup complete.",
                ),
                server_names=["FruityVice"],
                execution_limits=ExecutionLimits(
                    max_rounds=3,
                    max_tool_calls=2,
                ),
                shared_mcp_urls={"FruityVice": endpoint},
                project_dir=PROJECT_ROOT,
            )
            try:
                await adapter.initialize()
                result = await adapter.respond(
                    "Look up apple nutrition.",
                    timeout_seconds=30,
                )
                return adapter.connections, result
            finally:
                await adapter.close()

        with tempfile.TemporaryDirectory() as directory:
            pool = SharedMCPPool(
                project_dir=PROJECT_ROOT,
                commands_path=PROJECT_ROOT / "mcp_servers/commands.json",
                server_names=["FruityVice"],
                runtime_dir=Path(directory),
                startup_timeout=30,
            )
            try:
                config_path = pool.start()
                endpoint = load_shared_mcp_urls(config_path)["FruityVice"]
                connections, result = asyncio.run(exercise(endpoint))
            finally:
                pool.stop()

        self.assertEqual(connections["shared_mcp_servers"], ["FruityVice"])
        self.assertEqual(connections["connected_servers"], ["FruityVice"])
        self.assertFalse(connections["tool_allowlists"])
        self.assertIn(
            "get_fruit_nutrition",
            connections["exposed_tools_by_server"]["FruityVice"],
        )
        self.assertEqual(result.response, "Lookup complete.")
        self.assertEqual(result.tool_calls, 1)
        self.assertTrue(result.execution_results[0]["transport_success"])


if __name__ == "__main__":
    unittest.main()
