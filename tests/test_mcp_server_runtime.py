import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_env.server_catalog import (
    build_server_process,
    load_server_commands,
)
from agent_env.settings import load_project_env


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MCPServerRuntimeTests(unittest.TestCase):
    def test_catalog_contains_pinned_read_only_domain_extensions(self):
        commands = load_server_commands(
            PROJECT_ROOT / "mcp_servers/commands.json"
        )
        self.assertEqual(len(commands), 25)

        for server_name in {
            "Duffel Flight Search",
            "Keenable Web Search",
            "Remoote Jobs",
            "DailyMed MCP",
            "OneBusAway",
            "tickadoo",
            "Open Food Facts",
            "OpenFDA",
        }:
            self.assertIn(server_name, commands)
        self.assertNotIn("OctoTrip Flights", commands)
        self.assertEqual(
            commands["Duffel Flight Search"]["env"],
            ["DUFFEL_ACCESS_TOKEN"],
        )

        remoote_command = commands["Remoote Jobs"]["cmd"]
        self.assertIn("run-stateless-http-mcp-proxy.mjs", remoote_command)
        self.assertIn("--ignore-tool create_apply_intent", remoote_command)
        self.assertIn("--ignore-tool create_alert", remoote_command)

        for server_name in {
            "OneBusAway",
            "tickadoo",
            "Open Food Facts",
            "OpenFDA",
        }:
            self.assertIn(
                "run-stateless-http-mcp-proxy.mjs",
                commands[server_name]["cmd"],
            )
            self.assertEqual(commands[server_name]["env"], [])

        adapter_package = json.loads(
            (
                PROJECT_ROOT / "mcp_servers/remote-adapters/package.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(adapter_package["dependencies"]["mcp-remote"], "0.1.38")
        self.assertEqual(adapter_package["dependencies"]["@keenable/mcp"], "0.1.0")
        self.assertEqual(adapter_package["dependencies"]["undici"], "7.29.0")

    def test_python_servers_use_declared_uv_dependencies(self):
        commands = load_server_commands(
            PROJECT_ROOT / "mcp_servers/commands.json"
        )
        isolated_servers = {
            "Weather Data",
            "Movie Recommender",
            "Car Price Evaluator",
            "Game Trends",
            "Call for Papers",
        }
        for server_name in isolated_servers:
            with self.subTest(server=server_name):
                self.assertTrue(
                    commands[server_name]["cmd"].startswith("uv run "),
                    commands[server_name]["cmd"],
                )
        self.assertEqual(
            commands["Paper Search"]["cmd"],
            ".venv/bin/python -m paper_search_mcp.server",
        )
        self.assertEqual(
            commands["Medical Calculator"]["cmd"],
            ".venv/bin/python medcalc/__main__.py",
        )

    def test_project_local_runtime_paths_reach_child_servers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "server").mkdir()
            with patch.dict(
                os.environ,
                {"HTTPS_PROXY": "http://proxy.test:8080"},
                clear=True,
            ):
                load_project_env(project_dir)
                process = build_server_process(
                    project_dir=project_dir,
                    server_name="test-server",
                    config={
                        "cmd": "uv run python server.py",
                        "cwd": "server",
                        "env": [],
                    },
                )

                self.assertEqual(
                    os.environ["UV_CACHE_DIR"],
                    str(project_dir / ".uv-cache"),
                )
                self.assertEqual(
                    os.environ["UV_PYTHON_INSTALL_DIR"],
                    str(project_dir / ".uv-python"),
                )
                self.assertEqual(
                    os.environ["BIOMCP_CACHE_DIR"],
                    str(project_dir / ".cache/biomcp"),
                )
                self.assertEqual(
                    process.env["UV_CACHE_DIR"],
                    str(project_dir / ".uv-cache"),
                )
                self.assertEqual(
                    process.env["UV_PYTHON_INSTALL_DIR"],
                    str(project_dir / ".uv-python"),
                )
                self.assertEqual(
                    process.env["BIOMCP_CACHE_DIR"],
                    str(project_dir / ".cache/biomcp"),
                )
                self.assertEqual(
                    process.env["HTTPS_PROXY"],
                    "http://proxy.test:8080",
                )

    def test_project_env_fallback_loads_values_without_dotenv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / ".env").write_text(
                "MY_MODEL_NAME=glm-5.2\n"
                "MY_MODEL_BASE_URL=https://example.invalid/v1\n"
                "MY_MODEL_API_KEY='test-key'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_project_env(project_dir)

                self.assertEqual(os.environ["MY_MODEL_NAME"], "glm-5.2")
                self.assertEqual(
                    os.environ["MY_MODEL_BASE_URL"],
                    "https://example.invalid/v1",
                )
                self.assertEqual(os.environ["MY_MODEL_API_KEY"], "test-key")


if __name__ == "__main__":
    unittest.main()
