import json
import unittest
from pathlib import Path

from privacy_trace.privacy_attacker import (
    PROFILE_ATTRIBUTES as ATTACKER_PROFILE_ATTRIBUTES,
    build_prompt,
    validate_output,
)
from privacy_trace.profile_generator import FULL_SCHEMA_FIELDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_PROFILES_PATH = (
    PROJECT_ROOT
    / "artifacts/profile_pool/profiles.jsonl"
)


def inference(value=None, status="unresolved"):
    return {
        "status": status,
        "evidence": [],
        "value": value,
        "confidence": 3 if status == "inferred" else None,
        "alternative_values": [],
        "unresolved_reason": "" if status == "inferred" else "unresolved",
    }


class PrivacyTraceSchemaTests(unittest.TestCase):
    def test_inference_schema_matches_generated_profile_schema(self):
        self.assertEqual(ATTACKER_PROFILE_ATTRIBUTES, FULL_SCHEMA_FIELDS)

    def test_attacker_schema_matches_synthetic_profile_records(self):
        if SYNTHETIC_PROFILES_PATH.is_file():
            with SYNTHETIC_PROFILES_PATH.open(encoding="utf-8") as handle:
                first_profile = json.loads(next(handle))
        else:
            first_profile = json.loads(
                (PROJECT_ROOT / "examples/travel.json").read_text(encoding="utf-8")
            )["profile"]

        self.assertEqual(list(first_profile), ATTACKER_PROFILE_ATTRIBUTES)

    def test_attacker_prompt_uses_generated_profile_names(self):
        prompt = build_prompt([])

        for attribute in FULL_SCHEMA_FIELDS:
            self.assertIn(f'"{attribute}"', prompt)
        for legacy_attribute in (
            "physical_issue",
            "mental_issue",
            "income",
            "native_language",
            "philosophical_belief",
            "political_orientation",
        ):
            self.assertNotIn(f'"{legacy_attribute}"', prompt)

    def test_attacker_validation_rebuilds_summary_arrays(self):
        profile = {attribute: inference() for attribute in FULL_SCHEMA_FIELDS}
        profile["age"] = inference(50, "inferred")
        output = {
            "profile": profile,
            "inferred_attributes": ["physical_issue"],
            "unresolved_attributes": [],
        }

        validated = validate_output(output)

        self.assertEqual(validated["inferred_attributes"], ["age"])
        self.assertEqual(
            validated["unresolved_attributes"],
            [attribute for attribute in FULL_SCHEMA_FIELDS if attribute != "age"],
        )

    def test_attacker_validation_rejects_extra_profile_attributes(self):
        profile = {attribute: inference() for attribute in FULL_SCHEMA_FIELDS}
        profile["physical_issue"] = inference()

        with self.assertRaisesRegex(RuntimeError, "unexpected profile attributes"):
            validate_output({"profile": profile})


if __name__ == "__main__":
    unittest.main()
