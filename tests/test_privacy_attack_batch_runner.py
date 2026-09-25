import tempfile
import unittest
import json
from pathlib import Path

from privacy_trace.attacker_view import (
    ATTACKER_OBSERVATION_FORMAT,
    ATTACKER_VIEW_POLICY,
    RAW_REPEATED,
)
from scripts.run_privacy_attacks import (
    build_arg_parser,
    has_usable_attack,
    select_trajectory_files,
)
from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES


class PrivacyAttackBatchRunnerTests(unittest.TestCase):
    def test_selects_sorted_trajectory_files_with_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("S0003.json", "S0001.json", "S0002.json", "_batch_summary.json"):
                value = {"scenario_id": Path(name).stem} if name.startswith("S") else {}
                (root / name).write_text(json.dumps(value), encoding="utf-8")

            selected = select_trajectory_files(root, 2)

        self.assertEqual([path.name for path in selected], ["S0001.json", "S0002.json"])

    def test_selects_records_from_canonical_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectories.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps({"scenario_id": scenario_id, "tools_info": []})
                    for scenario_id in ("S0001", "S0002", "S0003")
                )
                + "\n",
                encoding="utf-8",
            )

            selected = select_trajectory_files(path, 2)
            second = selected[1].read()

        self.assertEqual([record.name for record in selected], ["S0001.json", "S0002.json"])
        self.assertEqual(second["scenario_id"], "S0002")

    def test_workers_cli_defaults_to_one_and_accepts_five(self):
        parser = build_arg_parser()
        required = ["--input-dir", "in", "--output-dir", "out"]

        self.assertEqual(parser.parse_args(required).workers, 1)
        self.assertEqual(parser.parse_args(required + ["--workers", "5"]).workers, 5)

    def test_attack_mode_defaults_to_two_stage_and_accepts_one_shot(self):
        parser = build_arg_parser()
        required = ["--input-dir", "in", "--output-dir", "out"]

        self.assertEqual(parser.parse_args(required).attack_mode, "two_stage")
        self.assertEqual(
            parser.parse_args(required + ["--attack-mode", "one_shot"]).attack_mode,
            "one_shot",
        )
        self.assertEqual(
            parser.parse_args(
                required + ["--observation-format", RAW_REPEATED]
            ).observation_format,
            RAW_REPEATED,
        )
        self.assertIsNone(parser.parse_args(required).reuse_stage1_dir)
        self.assertEqual(
            parser.parse_args(
                required + ["--reuse-stage1-dir", "cached-stage1"]
            ).reuse_stage1_dir,
            Path("cached-stage1"),
        )

    def test_resume_requires_complete_matching_attack(self):
        profile = {
            attribute: {"status": "inferred", "value": "guess"}
            for attribute in PROFILE_ATTRIBUTES
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0001.json"
            path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "attack_type": "one_shot",
                            "attacker_view_policy": ATTACKER_VIEW_POLICY,
                            "attacker_observation_format": ATTACKER_OBSERVATION_FORMAT,
                        },
                        "profile": profile,
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(has_usable_attack(path, "one_shot"))
            self.assertFalse(has_usable_attack(path, "two_stage"))
            profile.pop(PROFILE_ATTRIBUTES[0])
            path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "attack_type": "one_shot",
                            "attacker_view_policy": ATTACKER_VIEW_POLICY,
                            "attacker_observation_format": ATTACKER_OBSERVATION_FORMAT,
                        },
                        "profile": profile,
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(has_usable_attack(path, "one_shot"))

    def test_resume_rejects_legacy_two_stage_protocol(self):
        profile = {
            attribute: {"status": "inferred", "value": "guess"}
            for attribute in PROFILE_ATTRIBUTES
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0001.json"
            path.write_text(
                json.dumps(
                    {
                        "metadata": {"attack_type": "two_stage"},
                        "profile": profile,
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(has_usable_attack(path, "two_stage"))

            path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "attack_type": "two_stage",
                            "attacker_view_policy": ATTACKER_VIEW_POLICY,
                            "attacker_observation_format": ATTACKER_OBSERVATION_FORMAT,
                            "protocol_version": "two_stage_conditional_prior",
                            "stage2_context": "stage1_values_only",
                            "stage2_has_trajectory": False,
                        },
                        "profile": profile,
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(has_usable_attack(path, "two_stage"))


if __name__ == "__main__":
    unittest.main()
