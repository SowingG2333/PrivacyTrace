import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_env.execution_limits import ExecutionLimits  # noqa: E402
from agent_env.case_loader import load_task_case  # noqa: E402
from agent_env.tool_outcomes import classify_tool_output  # noqa: E402
from agent_env.domain_registry import (  # noqa: E402
    load_available_server_names,
    load_domain_registry,
    validate_domain_servers,
)
from agent_env.models import AgentTurnResult, RunLimits, TaskCase  # noqa: E402
from agent_env.orchestrator import AgentEnv  # noqa: E402
from agent_env.user_simulator import (  # noqa: E402
    SimulatedUserTurn,
    SimulatorOutputError,
)
from scripts.run_agent_scenario import build_arg_parser  # noqa: E402


class StubSimulator:
    def __init__(self):
        self.index = 0

    async def next_turn(self, history):
        self.index += 1
        if self.index == 1:
            return SimulatedUserTurn(
                "Please check the weather.",
                "continue",
                remaining_requirement="expected_result",
                llm_outputs=(
                    {
                        "provider_attempt": 1,
                        "content": '{"message":"Please check the weather.","status":"continue"}',
                    },
                ),
            )
        return SimulatedUserTurn("That answers it, thanks.", "satisfied")


class StubAgent:
    session_id = "test-session"
    connections = {"connected_count": 1}

    def __init__(self):
        self.closed = False

    async def initialize(self):
        return None

    async def respond(self, message, timeout_seconds):
        return AgentTurnResult(
            response="It will be mild.",
            total_rounds=1,
            tool_calls=1,
            execution_results=[
                {
                    "tool": "Weather:forecast",
                    "parameters": {"city": "Paris"},
                    "result": '{"temperature": 20}',
                    "success": True,
                    "content_success": True,
                    "outcome": "success",
                }
            ],
        )

    async def close(self):
        self.closed = True


class FailingSimulator:
    async def next_turn(self, history):
        raise SimulatorOutputError(
            "invalid simulator output",
            raw_outputs=["not json"],
            parse_errors=["missing object"],
            usage={"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        )


class AgentEnvTests(unittest.TestCase):
    def test_case_loader_consumes_structured_user_task(self):
        user_task = {
            "goal": "Compare travel options tomorrow.",
            "context": "The user is planning a short outing.",
            "constraints": ["Stay within the stated budget."],
            "expected_result": "A grounded comparison.",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles.jsonl"
            cases = root / "cases.jsonl"
            profiles.write_text(
                json.dumps({"profile_id": "P0001", "age": 30}) + "\n",
                encoding="utf-8",
            )
            cases.write_text(
                json.dumps({
                    "scenario_id": "S0001",
                    "profile_id": "P0001",
                    "domain": "travel",
                    "user_task": user_task,
                    "selected_tools": [{
                        "server_name": "Weather Data",
                        "tool_name": "forecast",
                        "qualified_name": "Weather Data:forecast",
                    }],
                    "evaluation_criteria": ["criterion"],
                }) + "\n",
                encoding="utf-8",
            )

            task = load_task_case(profiles, cases, "S0001")

        self.assertEqual(task.user_task, user_task)
        self.assertNotIn("selected_tools", task.metadata)

    def test_direct_runner_has_one_canonical_agent_runtime(self):
        args = build_arg_parser().parse_args(["--scenario-id", "S0001"])

        self.assertFalse(hasattr(args, "agent_runtime"))

    def test_tool_outcomes_distinguish_transport_from_content(self):
        self.assertEqual(classify_tool_output('{"results": []}').kind, "empty")
        self.assertEqual(
            classify_tool_output("403 Client Error: Forbidden").kind,
            "application_error",
        )
        self.assertEqual(
            classify_tool_output("anything", transport_error=True).kind,
            "transport_error",
        )
        self.assertTrue(classify_tool_output('{"results": ["ok"]}').usable)
        self.assertEqual(
            classify_tool_output('location: {"lat": 1, "lng": 2} []').kind,
            "empty",
        )

    def test_limits_reject_unknown_or_non_positive_values(self):
        with self.assertRaises(ValueError):
            ExecutionLimits(max_rounds=0)
        with self.assertRaises(ValueError):
            ExecutionLimits(max_tool_calls=0)
        with self.assertRaises(ValueError):
            ExecutionLimits.from_mapping({"unknown": 1})
        self.assertIsNone(ExecutionLimits().max_rounds)
        self.assertIsNone(ExecutionLimits().max_tool_calls)
        self.assertIsNone(ExecutionLimits().max_total_tokens)
        self.assertIsNone(RunLimits().max_user_turns)

    def test_domain_registry_matches_catalog(self):
        registry = load_domain_registry(PROJECT_ROOT / "config/domains.json")
        available = load_available_server_names(
            PROJECT_ROOT / "mcp_servers/commands.json"
        )
        validate_domain_servers(registry, available)
        self.assertEqual(
            set(registry), {"travel", "health", "shopping", "career_learning"}
        )

    def test_orchestrator_emits_v2_trajectory_and_evaluation(self):
        agent = StubAgent()
        env = AgentEnv(
            task=TaskCase(
                scenario_id="S-test",
                profile_id="P-test",
                domain="travel",
                user_task={
                    "goal": "Check the weather before tomorrow's trip.",
                    "context": "The user is deciding what to pack.",
                    "constraints": ["Use the destination already provided."],
                    "expected_result": "A concise forecast and packing advice.",
                },
                profile={"age": 30},
            ),
            simulator=StubSimulator(),
            agent=agent,
            limits=RunLimits(),
            servers=["Weather Data"],
            server_selection="test",
            tool_allowlists={"Weather Data": ["forecast"]},
        )

        trajectory = asyncio.run(env.run())

        self.assertEqual(trajectory["schema_version"], "2.0")
        self.assertEqual(
            trajectory["task"]["user_task"]["goal"],
            "Check the weather before tomorrow's trip.",
        )
        self.assertEqual(trajectory["status"], "satisfied")
        self.assertEqual(trajectory["total_tool_calls"], 1)
        self.assertEqual(
            trajectory["environment"]["server_policy"],
            {
                "required_servers": ["Weather Data"],
                "optional_servers": [],
                "tool_allowlists": {"Weather Data": ["forecast"]},
            },
        )
        self.assertEqual(
            trajectory["turns"][0]["simulator_llm_outputs"][0]["provider_attempt"],
            1,
        )
        self.assertEqual(
            trajectory["turns"][0]["simulator_remaining_requirement"],
            "expected_result",
        )
        self.assertTrue(trajectory["evaluation"]["passed"])
        self.assertTrue(agent.closed)

    def test_simulator_failure_is_distinct_and_counts_failed_usage(self):
        agent = StubAgent()
        env = AgentEnv(
            task=TaskCase(
                scenario_id="S-fail",
                profile_id="P-test",
                domain="travel",
                user_task={
                    "goal": "Choose a route for tomorrow's trip.",
                    "context": "The user needs to arrive before an appointment.",
                    "constraints": ["Avoid an overly long detour."],
                    "expected_result": "A practical route recommendation.",
                },
                profile={"age": 30},
            ),
            simulator=FailingSimulator(),
            agent=agent,
            limits=RunLimits(),
            servers=["Geoapify"],
            server_selection="test",
        )

        trajectory = asyncio.run(env.run())

        self.assertEqual(trajectory["status"], "simulator_error")
        self.assertEqual(trajectory["termination_reason"], "simulator_output_error")
        self.assertEqual(trajectory["token_usage"]["simulator"]["total_tokens"], 9)
        self.assertEqual(trajectory["total_known_tokens"], 9)
        self.assertFalse(trajectory["evaluation"]["checks"]["no_agent_error"])
        self.assertTrue(agent.closed)


if __name__ == "__main__":
    unittest.main()
