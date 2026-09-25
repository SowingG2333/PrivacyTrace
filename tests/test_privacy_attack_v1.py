import json
import os
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from agent_env.recovery import require_nonempty_model
from privacy_trace.privacy_attacker import (
    PROFILE_ATTRIBUTES,
    run_stage1_attack,
    run_stage2_from_stage1,
)
from scripts.analyze_privacy_experiments import paired_outcomes, two_stage_metrics
from scripts.build_privacy_eval_records import add_arm, rows_for_output
from scripts.run_channel_experiments import build_arg_parser as channel_parser
from scripts.run_privacy_attacks import build_arg_parser as attack_parser
from scripts.run_semantic_judge import build_arg_parser as judge_parser
from scripts.run_view_scope_experiments import cross_domain_observation
from scripts.run_view_scope_experiments import parser as view_scope_parser
from scripts.run_privacy_recovery_watchdog import _validate, output_count, supervise
from privacy_trace.llm_client import DirectOpenAIClient


def stage1_payload():
    return json.dumps({"profile": {
        attr: {"status": "unresolved", "evidence": [], "value": None,
               "confidence": None, "alternative_values": [], "unresolved_reason": "x"}
        for attr in PROFILE_ATTRIBUTES
    }})


class PrivacyAttackV1Tests(unittest.TestCase):
    def test_attacker_model_is_generic_but_nonempty(self):
        self.assertEqual(require_nonempty_model("deepseek-v4-flash", stage="attack"), "deepseek-v4-flash")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            require_nonempty_model(" ", stage="attack")

    def test_environment_key_pool_includes_secondary_slots(self):
        with patch.dict(
            os.environ,
            {
                "MY_MODEL_API_KEY": "primary",
                "MY_MODEL_API_KEYS": "key2,key3",
                "MY_MODEL_API_KEY_RPMS": "500,100,0",
                "PRIVACY_ATTACK_API_KEY_INDEXES": "1,2",
            },
            clear=False,
        ):
            self.assertEqual(DirectOpenAIClient.environment_key_count(), 2)

    def test_timeout_resets_only_affected_key_transport(self):
        fake_http_clients = [MagicMock(name=f"http-{index}") for index in range(4)]
        fake_openai_clients = [MagicMock(name=f"openai-{index}") for index in range(4)]
        with (
            patch("httpx.Client", side_effect=fake_http_clients),
            patch("openai.OpenAI", side_effect=fake_openai_clients),
        ):
            client = DirectOpenAIClient(
                api_keys=("key-1", "key-2", "key-3"),
                key_slots=(1, 2, 3),
                base_url="https://example.invalid/v1",
                timeout=1,
                max_connections=3,
            )
            slot_1 = client._key_clients[1]
            slot_2 = client._key_clients[2]
            client._abort_key_client(slot_1.generation, slot_1.slot)

            self.assertNotIn(1, client._key_clients)
            self.assertIs(client._key_clients[2], slot_2)
            slot_1.client.close.assert_called_once()
            slot_1.http_client.close.assert_called_once()
            slot_2.client.close.assert_not_called()

            _, rebuilt = client._acquire_key_client()
            self.assertEqual(rebuilt.slot, 1)
            self.assertGreater(rebuilt.generation, slot_1.generation)
            self.assertEqual(client.key_count, 3)
            client.close()

    def test_key_selection_is_weighted_by_independent_rpm(self):
        with (
            patch("httpx.Client"),
            patch("openai.OpenAI"),
        ):
            client = DirectOpenAIClient(
                api_keys=("key-1", "key-2"),
                key_rpms=(500, 100),
                key_slots=(1, 2),
                base_url="https://example.invalid/v1",
            )
            selected = [client._acquire_key_client()[1].slot for _ in range(60)]
            self.assertEqual(selected.count(1), 50)
            self.assertEqual(selected.count(2), 10)
            client.close()

    def test_request_timeout_does_not_schedule_shared_pool_abort(self):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = '{"ok": true}'
        response.usage = None
        openai_client = MagicMock()
        openai_client.chat.completions.create.return_value = response
        limiter = MagicMock()
        with (
            patch("httpx.Client"),
            patch("openai.OpenAI", return_value=openai_client),
            patch("privacy_trace.llm_client.threading.Timer") as timer,
        ):
            client = DirectOpenAIClient(
                api_keys=("key-1",),
                base_url="https://example.invalid/v1",
                timeout=1,
            )
            result = client.complete_json(
                prompt="test",
                model="model",
                temperature=0,
                stage="test",
                limiter=limiter,
            )
            timer.assert_not_called()
            self.assertEqual(
                result.metadata["timeout_enforcement"],
                "httpx_per_request_timeout",
            )
            client.close()

    def test_no_max_token_default_and_attack_timeout(self):
        parsed = attack_parser().parse_args(["--input", "x", "--output-dir", "out"])
        self.assertEqual(parsed.timeout, 600.0)
        self.assertEqual(parsed.thinking_mode, "disabled")
        self.assertFalse(hasattr(parsed, "max_tokens"))
        self.assertIsNone(channel_parser().parse_args(["--input", "x", "--output-root", "out"]).max_tokens)
        self.assertIsNone(judge_parser().parse_args(["--records", "r", "--output-root", "o"]).max_tokens)
        self.assertIsNone(
            view_scope_parser().parse_args(
                ["--input", "x", "--output-root", "out", "--scope", "cross_domain_all_servers", "--model", "m"]
            ).max_tokens
        )

    def test_stage1_and_stage2_use_distinct_request_stages(self):
        seen = []
        def request(prompt, stage):
            seen.append(stage)
            if stage == "stage1":
                return stage1_payload(), {"stage": stage}
            unresolved = PROFILE_ATTRIBUTES
            return json.dumps({"profile": {
                attr: {"status": "inferred", "value": f"v-{attr}", "evidence": [], "alternative_values": [], "unresolved_reason": ""}
                for attr in unresolved
            }}), {"stage": stage}
        stage1, metadata, _ = run_stage1_attack([], "m", "openai", request_fn=request)
        final = run_stage2_from_stage1(stage1, "m", "openai", request_fn=request, observation_metadata=metadata)
        self.assertEqual(seen, ["stage1", "stage2"])
        self.assertEqual(final["metadata"]["requests"]["stage2"]["stage"], "stage2")

    def test_cross_domain_catalog_keeps_segments_and_one_catalog_entry(self):
        call = {"server_name": "A", "tool_name": "lookup", "tool_description": "d", "tool_schema": {}, "call_statement": {"x": 1}, "returned_result": {"y": 2}}
        text, stats = cross_domain_observation([
            {"domain": "health", "scenario_id": "S1", "tools_info": [call], "observation_format": "lossless_tool_catalog"},
            {"domain": "travel", "scenario_id": "S2", "tools_info": [call], "observation_format": "lossless_tool_catalog"},
        ])
        self.assertEqual(stats["tool_catalog_count"], 1)
        self.assertIn("Domain: health", text)
        self.assertIn("Domain: travel", text)

    def test_record_builder_marks_failed_slots_incorrect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0001.json"
            path.write_text(json.dumps({"profile": {a: {"status": "failed", "value": None} for a in PROFILE_ATTRIBUTES}, "metadata": {"profile_id": "P0001", "scenario_id": "S0001", "failure_scored_as_incorrect": True}}))
            rows = list(rows_for_output("arm", path, {"P0001": {a: "truth" for a in PROFILE_ATTRIBUTES}}))
        self.assertEqual(len(rows), 17)
        self.assertTrue(all(row["generation_failed"] and not row["normalized"] for row in rows))

    def test_two_stage_and_raw_pair_aggregates(self):
        def row(arm, semantic, status="inferred"):
            return {"arm": arm, "scenario_id": "S0001", "attribute": "age", "profile_id": "P0001", "semantic": semantic, "prediction_status": status}
        self.assertEqual(
            paired_outcomes([row("raw", True)], [row("catalog", False)])["left_only"], 1
        )
        metrics = two_stage_metrics(
            [row("stage1", False, "unresolved")], [row("final", True)]
        )
        self.assertEqual(metrics["stage2_added_correct_slots"], 1)
        self.assertEqual(metrics["unresolved_slot_fill_accuracy"], 1.0)

    def test_record_builder_rejects_duplicate_view_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {"profile": {a: {"status": "inferred", "value": "v"} for a in PROFILE_ATTRIBUTES}, "metadata": {"profile_id": "P0001", "scenario_id": "S0001"}}
            (root / "S0001.json").write_text(json.dumps(payload))
            rows, seen = [], set()
            truth = {"P0001": {a: "v" for a in PROFILE_ATTRIBUTES}}
            add_arm(rows, seen, "arm", root, truth, recursive=False, two_stage=False)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                add_arm(rows, seen, "arm", root, truth, recursive=False, two_stage=False)

    def test_connection_watchdog_requires_resumable_command(self):
        module = __import__("scripts.run_privacy_recovery_watchdog", fromlist=["parser"])
        parsed = module.parser().parse_args(
            ["--output-dir", "out", "--expected-count", "4", "--", "python", "runner.py", "--resume"]
        )
        self.assertEqual(_validate(parsed), ["python", "runner.py", "--resume"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "S0001.json").write_text("{}")
            (root / "not-result.json").write_text("{}")
            self.assertEqual(output_count(root, "S*.json"), 1)

    def test_connection_watchdog_terminates_child_on_signal(self):
        module = __import__("scripts.run_privacy_recovery_watchdog", fromlist=["parser"])
        child = MagicMock(pid=12345)
        child.poll.return_value = None

        def interrupt_sleep(_seconds):
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)

        with tempfile.TemporaryDirectory() as directory:
            parsed = module.parser().parse_args(
                [
                    "--output-dir",
                    str(Path(directory) / "batches"),
                    "--expected-count",
                    "1",
                    "--poll-seconds",
                    "1",
                    "--",
                    "python",
                    "runner.py",
                    "--resume",
                ]
            )
            with (
                patch.object(module.subprocess, "Popen", return_value=child),
                patch.object(module.time, "sleep", side_effect=interrupt_sleep),
                patch.object(module, "_terminate") as terminate,
            ):
                code = supervise(parsed)

        self.assertEqual(code, 128 + signal.SIGTERM)
        terminate.assert_called_once_with(child)

    def test_connection_watchdog_allows_final_bookkeeping(self):
        module = __import__("scripts.run_privacy_recovery_watchdog", fromlist=["parser"])
        child = MagicMock(pid=12345)
        child.poll.return_value = None
        child.wait.return_value = 0

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "batches"
            output_dir.mkdir()
            parsed = module.parser().parse_args(
                [
                    "--output-dir",
                    str(output_dir),
                    "--expected-count",
                    "1",
                    "--pattern",
                    "S*.json",
                    "--poll-seconds",
                    "1",
                    "--",
                    "python",
                    "runner.py",
                    "--resume",
                ]
            )

            def materialize_final_output(_seconds):
                (output_dir / "S0001.json").write_text("{}")

            with (
                patch.object(module.subprocess, "Popen", return_value=child),
                patch.object(module.time, "sleep", side_effect=materialize_final_output),
                patch.object(module, "_terminate") as terminate,
            ):
                code = supervise(parsed)

        self.assertEqual(code, 0)
        child.wait.assert_called_once_with(timeout=30.0)
        terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
