import json
import unittest
from unittest.mock import patch

from privacy_trace.attacker_view import RAW_REPEATED
from privacy_trace.privacy_attacker import (
    PROFILE_ATTRIBUTES,
    build_one_shot_prompt,
    build_stage2_prompt,
    get_stage1_inferred_values,
    merge_stage_outputs,
    parse_json_output,
    run_one_shot_attack,
    run_stage2_from_stage1,
    run_two_stage_attack,
    validate_one_shot_output,
    validate_output,
    validate_stage2_output,
)


def inference(value=None, status="unresolved", confidence=None):
    return {
        "status": status,
        "evidence": [],
        "value": value,
        "confidence": confidence if status == "inferred" else None,
        "alternative_values": [],
        "unresolved_reason": "" if status == "inferred" else "unresolved",
    }


def stage1_with(**inferred_values):
    profile = {attribute: inference() for attribute in PROFILE_ATTRIBUTES}
    for attribute, (value, confidence) in inferred_values.items():
        profile[attribute] = inference(value, "inferred", confidence)
    return validate_output({"profile": profile})


def stage2_for(targets, **inferred_values):
    profile = {
        attribute: inference(f"guess-{attribute}", "inferred", 1)
        for attribute in targets
    }
    for attribute, (value, confidence) in inferred_values.items():
        profile[attribute] = inference(value, "inferred", confidence)
    for item in profile.values():
        item.pop("confidence", None)
    return validate_stage2_output({"profile": profile}, list(targets))


class PrivacyAttackerTwoStageTests(unittest.TestCase):
    def test_stage2_receives_only_stage1_values_without_trajectory_metadata(self):
        stage1 = stage1_with(
            current_location=("Bengaluru, India", 1),
            age=(50, 5),
        )
        stage1["profile"]["current_location"]["evidence"] = [
            {
                "channel": "parameter",
                "tool_call": "maps",
                "observation": "secret trajectory marker",
                "implication": "reveals location",
            }
        ]

        inferred = get_stage1_inferred_values(stage1)
        prompt = build_stage2_prompt(stage1)

        self.assertEqual(list(inferred), ["age", "current_location"])
        self.assertEqual(inferred["current_location"], "Bengaluru, India")
        self.assertIn('"current_location"', prompt)
        self.assertIn('"age"', prompt)
        self.assertNotIn('"confidence"', prompt)
        self.assertNotIn("secret trajectory marker", prompt)
        self.assertNotIn('"channel": "parameter"', prompt)
        self.assertNotIn("Observable Information", prompt)
        self.assertNotIn("Tool Call Statement", prompt)

    def test_stage2_force_completes_exactly_stage1_unresolved_attributes(self):
        stage1 = stage1_with(current_location=("Bengaluru, India", 4))
        targets = stage1["unresolved_attributes"]
        profile = {
            attribute: inference(f"guess-{attribute}", "inferred", 1)
            for attribute in targets
        }
        profile["citizenship"] = inference("India", "inferred", 3)

        validated = validate_stage2_output({"profile": profile}, targets)

        self.assertEqual(validated["inferred_attributes"], targets)
        self.assertEqual(validated["unresolved_attributes"], [])
        self.assertTrue(
            all(item["confidence"] == 1 for item in validated["profile"].values())
        )
        self.assertNotIn("current_location", validated["profile"])

        profile["current_location"] = inference("India", "inferred", 5)
        with self.assertRaisesRegex(RuntimeError, "unexpected profile attributes"):
            validate_stage2_output({"profile": profile}, targets)

    def test_stage2_rejects_unresolved_or_null_guesses(self):
        stage1 = stage1_with(current_location=("Bengaluru, India", 4))
        targets = stage1["unresolved_attributes"]
        profile = {
            attribute: inference(f"guess-{attribute}", "inferred", 1)
            for attribute in targets
        }
        profile["age"] = inference()

        with self.assertRaisesRegex(RuntimeError, "must be 'inferred'"):
            validate_stage2_output({"profile": profile}, targets)

        profile["age"] = inference(None, "inferred", 4)
        with self.assertRaisesRegex(RuntimeError, "value must not be null"):
            validate_stage2_output({"profile": profile}, targets)

    def test_stage2_accepts_literal_none_for_condition_fields(self):
        stage1 = stage1_with(current_location=("Bengaluru, India", 4))
        targets = stage1["unresolved_attributes"]
        stage2 = stage2_for(
            targets,
            physical_condition=("None", 3),
            mental_condition=("None", 2),
        )

        self.assertEqual(stage2["profile"]["physical_condition"]["value"], "None")
        self.assertEqual(stage2["profile"]["mental_condition"]["value"], "None")
        self.assertEqual(stage2["profile"]["physical_condition"]["status"], "inferred")
        self.assertEqual(stage2["profile"]["physical_condition"]["confidence"], 1)

    def test_merge_preserves_stage1_and_marks_stage2_source(self):
        stage1 = stage1_with(current_location=("Bengaluru, India", 1))
        stage2 = stage2_for(
            stage1["unresolved_attributes"],
            citizenship=("India", 3),
        )

        result = merge_stage_outputs(stage1, stage2)

        self.assertEqual(result["profile"]["current_location"]["confidence"], 1)
        self.assertEqual(result["profile"]["current_location"]["source_stage"], 1)
        self.assertEqual(result["profile"]["citizenship"]["confidence"], 1)
        self.assertEqual(result["profile"]["citizenship"]["source_stage"], 2)
        self.assertEqual(result["profile"]["age"]["source_stage"], 2)

    def test_attack_calls_stage2_once_and_only_for_unresolved_attributes(self):
        stage1 = stage1_with(current_location=("Bengaluru, India", 1))
        stage2 = stage2_for(
            stage1["unresolved_attributes"],
            citizenship=("India", 2),
        )
        responses = [json.dumps(stage1), json.dumps(stage2)]

        with patch(
            "privacy_trace.privacy_attacker.call_llm",
            side_effect=responses,
        ) as mocked_call:
            result = run_two_stage_attack(
                [{"tool_name": "maps", "call_statement": {"city": "Bengaluru"}}],
                "test-model",
                "openai",
            )

        self.assertEqual(mocked_call.call_count, 2)
        stage2_prompt = mocked_call.call_args_list[1].args[0]
        self.assertIn('"current_location"', stage2_prompt)
        self.assertNotIn('"confidence": 1', stage2_prompt)
        self.assertNotIn('"city": "Bengaluru"', stage2_prompt)
        self.assertIn("Do not output or estimate confidence", stage2_prompt)
        self.assertEqual(
            result["stage2"]["inferred_attributes"],
            stage1["unresolved_attributes"],
        )
        self.assertEqual(result["profile"]["citizenship"]["source_stage"], 2)
        self.assertEqual(result["unresolved_attributes"], [])
        self.assertEqual(result["metadata"]["stage2_context"], "stage1_values_only")
        self.assertFalse(result["metadata"]["stage2_has_trajectory"])

    def test_attack_skips_stage2_when_stage1_infers_everything(self):
        profile = {
            attribute: inference(f"value-{attribute}", "inferred", 1)
            for attribute in PROFILE_ATTRIBUTES
        }
        stage1 = validate_output({"profile": profile})

        with patch(
            "privacy_trace.privacy_attacker.call_llm",
            return_value=json.dumps(stage1),
        ) as mocked_call:
            result = run_two_stage_attack([], "test-model", "openai")

        self.assertEqual(mocked_call.call_count, 1)
        self.assertEqual(result["stage2"]["profile"], {})
        self.assertEqual(result["unresolved_attributes"], [])

    def test_reused_stage1_calls_only_conditional_prior_stage2(self):
        stage1 = stage1_with(current_location=("Bengaluru, India", 4))
        stage2 = stage2_for(stage1["unresolved_attributes"])

        with patch(
            "privacy_trace.privacy_attacker.call_llm",
            return_value=json.dumps(stage2),
        ) as mocked_call:
            result = run_stage2_from_stage1(
                stage1,
                "test-model",
                "openai",
                tool_call_count=3,
            )

        self.assertEqual(mocked_call.call_count, 1)
        prompt = mocked_call.call_args.args[0]
        self.assertIn('"current_location": "Bengaluru, India"', prompt)
        self.assertNotIn("Observable Information", prompt)
        self.assertTrue(result["metadata"]["stage1_reused"])
        self.assertEqual(result["metadata"]["tool_call_count"], 3)


class PrivacyAttackerOneShotTests(unittest.TestCase):
    def one_shot_profile(self):
        profile = {
            attribute: inference(f"guess-{attribute}", "inferred", 5)
            for attribute in PROFILE_ATTRIBUTES
        }
        for item in profile.values():
            item.pop("confidence", None)
        return profile

    def test_prompt_force_completes_all_attributes_without_stage1_context(self):
        prompt = build_one_shot_prompt(
            [{"tool_name": "maps", "call_statement": {"city": "Bengaluru"}}]
        )

        self.assertIn("Infer every listed profile attribute", prompt)
        self.assertIn("Do not output or estimate confidence", prompt)
        self.assertIn('"government_id"', prompt)
        self.assertNotIn("Stage 1 Inferred Attributes", prompt)

    def test_raw_repeated_prompt_remains_available_as_baseline(self):
        calls = [
            {
                "tool_name": "maps",
                "tool_description": "Map lookup",
                "call_statement": {"city": "Bengaluru"},
            },
            {
                "tool_name": "maps",
                "tool_description": "Map lookup",
                "call_statement": {"city": "Bengaluru"},
                "cache_hit": True,
                "duplicate_call_count": 2,
                "mcp_executed": False,
            },
        ]

        raw_prompt = build_one_shot_prompt(
            calls,
            observation_format=RAW_REPEATED,
        )
        catalog_prompt = build_one_shot_prompt(calls)

        self.assertEqual(raw_prompt.count("Tool Description: Map lookup"), 2)
        self.assertEqual(catalog_prompt.count("Tool Description: Map lookup"), 1)

    def test_validator_requires_every_attribute_and_assigns_fixed_confidence(self):
        profile = self.one_shot_profile()
        validated = validate_one_shot_output({"profile": profile})

        self.assertEqual(validated["inferred_attributes"], PROFILE_ATTRIBUTES)
        self.assertEqual(validated["unresolved_attributes"], [])
        self.assertTrue(
            all(item["confidence"] == 1 for item in validated["profile"].values())
        )

        del profile["age"]
        with self.assertRaisesRegex(RuntimeError, "missing target attributes"):
            validate_one_shot_output({"profile": profile})

    def test_attack_calls_model_once_and_returns_full_profile(self):
        response = json.dumps({"profile": self.one_shot_profile()})

        with patch(
            "privacy_trace.privacy_attacker.call_llm",
            return_value=response,
        ) as mocked_call:
            result = run_one_shot_attack([], "test-model", "openai")

        self.assertEqual(mocked_call.call_count, 1)
        self.assertEqual(result["metadata"]["attack_type"], "one_shot")
        self.assertEqual(result["inferred_attributes"], PROFILE_ATTRIBUTES)
        self.assertEqual(result["unresolved_attributes"], [])

    def test_parser_accepts_reasoning_prefix_around_one_json_object(self):
        parsed = parse_json_output(
            '<think>internal reasoning</think>\\n{"profile": {"age": 30}}'
        )
        self.assertEqual(parsed, {"profile": {"age": 30}})

if __name__ == "__main__":
    unittest.main()
