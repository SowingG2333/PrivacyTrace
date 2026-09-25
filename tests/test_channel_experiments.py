import json
import tempfile
import unittest
from pathlib import Path

from privacy_trace.attacker_view import (
    ATTACKER_OBSERVATION_FORMAT,
    ATTACKER_VIEW_POLICY,
    RAW_REPEATED,
)
from privacy_trace.channel_experiments import (
    ATTRIBUTE_DESCRIPTIONS,
    METADATA_SEQUENCE,
    METADATA_SEQUENCE_PARAMETERS,
    METADATA_SEQUENCE_RESULTS,
    SERVER_VIEW,
    build_observation_prompt,
    build_schema_only_prompt,
    project_tool_calls,
    server_slug,
    unique_server_names,
)
from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES
from scripts.analyze_attack_comparison import normalized_match
from scripts.run_channel_experiments import is_usable_output


class ChannelExperimentPromptTests(unittest.TestCase):
    def setUp(self):
        self.calls = [
            {
                "server_name": "Server A",
                "tool_name": "Server A:lookup",
                "tool_description": "Lookup a record",
                "tool_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                "call_statement": {"query": "private-parameter"},
                "returned_result": "private-result-a",
                "call_order": 2,
            },
            {
                "server_name": "Server B",
                "tool_name": "Server B:search",
                "tool_description": "Search records",
                "tool_schema": {"type": "object"},
                "call_statement": {"query": "other-private-parameter"},
                "returned_result": "private-result-b",
                "call_order": 3,
            },
            {
                "server_name": "Server A",
                "tool_name": "Server A:detail",
                "tool_description": "Get details",
                "tool_schema": {"type": "object"},
                "call_statement": {"id": "private-id"},
                "returned_result": "private-result-c",
                "call_order": 8,
            },
        ]

    def test_schema_only_contains_only_schema_contract(self):
        prompt = build_schema_only_prompt()
        self.assertEqual(set(ATTRIBUTE_DESCRIPTIONS), set(PROFILE_ATTRIBUTES))
        for attribute in PROFILE_ATTRIBUTES:
            self.assertIn(f"* {attribute}:", prompt)
        for forbidden in (
            "trajectory",
            "domain",
            "scenario",
            "server",
            "tool call",
            "private-parameter",
        ):
            self.assertNotIn(forbidden, prompt.casefold())

    def test_metadata_sequence_excludes_parameters_and_results(self):
        prompt, projected = build_observation_prompt(self.calls, METADATA_SEQUENCE)
        self.assertEqual(len(projected), 3)
        self.assertNotIn("private-parameter", prompt)
        self.assertNotIn("private-result", prompt)
        self.assertIn("Lookup a record", prompt)
        self.assertIn("Call Order: 8", prompt)

    def test_parameter_and_result_arms_are_separate(self):
        parameter_prompt, _ = build_observation_prompt(
            self.calls, METADATA_SEQUENCE_PARAMETERS
        )
        result_prompt, _ = build_observation_prompt(
            self.calls, METADATA_SEQUENCE_RESULTS
        )
        self.assertIn("private-parameter", parameter_prompt)
        self.assertNotIn("private-result", parameter_prompt)
        self.assertNotIn("private-parameter", result_prompt)
        self.assertIn("private-result", result_prompt)

    def test_server_view_removes_other_servers_and_global_order(self):
        prompt, projected = build_observation_prompt(
            self.calls, SERVER_VIEW, server_name="Server A"
        )
        self.assertEqual([item["call_order"] for item in projected], [1, 2])
        self.assertNotIn("Server B:search", prompt)
        self.assertNotIn("other-private-parameter", prompt)
        self.assertNotIn("private-result-b", prompt)
        self.assertIn("private-parameter", prompt)
        self.assertIn("private-result-c", prompt)
        self.assertNotIn("Call Order: 8", prompt)

    def test_projection_and_server_identity_are_stable(self):
        projected = project_tool_calls(
            self.calls,
            include_parameters=True,
            include_results=True,
            server_name="Server A",
            local_order=True,
        )
        self.assertEqual(len(projected), 2)
        self.assertEqual(unique_server_names(self.calls), ["Server A", "Server B"])
        self.assertEqual(server_slug("Server A"), server_slug("Server A"))
        self.assertNotEqual(server_slug("Server A"), server_slug("Server-A"))

    def test_projection_hides_cached_and_infrastructure_failures(self):
        calls = [
            {
                **self.calls[0],
                "success": True,
                "outcome": "success",
            },
            {
                **self.calls[1],
                "success": True,
                "outcome": "success",
                "cache_hit": True,
                "mcp_executed": False,
                "duplicate_call_count": 2,
            },
            {
                **self.calls[2],
                "success": False,
                "outcome": "application_error",
                "error": "Rate limit exceeded",
            },
        ]

        projected = project_tool_calls(
            calls,
            include_parameters=True,
            include_results=True,
        )
        raw_projected = project_tool_calls(
            calls,
            include_parameters=True,
            include_results=True,
            observation_format=RAW_REPEATED,
        )

        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["tool_name"], "Server A:lookup")
        self.assertEqual(len(raw_projected), 3)


class ExpandedNormalizationTests(unittest.TestCase):
    def test_country_and_location_aliases(self):
        self.assertTrue(normalized_match("citizenship", "American", "United States"))
        self.assertTrue(
            normalized_match(
                "current_location", "New York, USA", "New York, United States"
            )
        )
        self.assertTrue(normalized_match("citizenship", "British", "United Kingdom"))

    def test_additional_schema_aliases(self):
        self.assertTrue(normalized_match("sex", "woman", "female"))
        self.assertTrue(normalized_match("education_level", "PhD", "doctorate"))
        self.assertTrue(normalized_match("income_level", "middle income", "middle"))
        self.assertTrue(normalized_match("relationship_status", "partnered", "in relationship"))
        self.assertTrue(normalized_match("mental_condition", "no condition", "None"))


class ChannelResumeTests(unittest.TestCase):
    def test_resume_requires_model_and_trajectory_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0001.json"
            profile = {
                attribute: {"status": "failed", "value": None}
                for attribute in PROFILE_ATTRIBUTES
            }
            path.write_text(
                json.dumps(
                    {
                        "profile": profile,
                        "metadata": {
                            "experiment_mode": "schema_only",
                            "model": "glm-5.2",
                            "trajectory_sha256": "trajectory-hash",
                            "attacker_view_policy": ATTACKER_VIEW_POLICY,
                            "attacker_observation_format": ATTACKER_OBSERVATION_FORMAT,
                            "failure_scored_as_incorrect": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                is_usable_output(
                    path,
                    "schema_only",
                    None,
                    model="glm-5.2",
                    trajectory_sha256="trajectory-hash",
                )
            )
            self.assertFalse(
                is_usable_output(
                    path,
                    "schema_only",
                    None,
                    model="glm-5.2",
                    trajectory_sha256="changed",
                )
            )
            path.write_text('{"truncated":', encoding="utf-8")
            self.assertFalse(is_usable_output(path, "schema_only", None))


if __name__ == "__main__":
    unittest.main()
