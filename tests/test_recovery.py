import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agent_env.recovery import (
    FailureLedger,
    RecoveryContract,
    RollingFailureWindow,
    atomic_write_json,
    atomic_write_jsonl,
    ensure_recovery_manifest,
    is_nonretryable_request_error,
    is_transient_provider_error,
    quarantine_artifact,
)


class RecoveryContractTests(unittest.TestCase):
    def test_transient_failure_window_trips_above_five_percent_of_fifty(self):
        window = RollingFailureWindow()
        for _ in range(47):
            self.assertFalse(window.record(False))
        self.assertFalse(window.record(True))
        self.assertFalse(window.record(True))
        self.assertTrue(window.record(True))
        self.assertEqual(window.snapshot()["failure_rate"], 0.06)
        self.assertTrue(is_transient_provider_error("Error code: 429"))
        self.assertTrue(is_transient_provider_error("Status code: 502"))
        self.assertTrue(is_transient_provider_error("connection timed out"))
        self.assertFalse(is_transient_provider_error("invalid JSON"))

    def test_context_overflow_is_not_retried(self):
        self.assertTrue(
            is_nonretryable_request_error(
                "Error code: 400: maximum context length exceeded"
            )
        )

    def test_worker_downgrade_does_not_invalidate_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            atomic_write_json(source, {"value": 1})
            contract = RecoveryContract(
                run_id="run-1",
                phase="test",
                model="glm-5.2",
                prompt_version="test-prompt",
                seed=42,
                input_hashes={str(source): "hash"},
                config={"batch_size": 25},
            )
            path = root / "manifest.json"
            ensure_recovery_manifest(path, contract, workers=64, resume=False)
            resumed = ensure_recovery_manifest(
                path, contract, workers=16, resume=True
            )

            self.assertEqual(resumed["workers"], 16)
            self.assertEqual(resumed["attempt_count"], 2)

    def test_changed_input_contract_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            first = RecoveryContract(
                run_id="run-1",
                phase="test",
                model="glm-5.2",
                prompt_version="test-prompt",
                seed=42,
                input_hashes={"input": "one"},
                config={},
            )
            changed = RecoveryContract(
                run_id="run-1",
                phase="test",
                model="glm-5.2",
                prompt_version="test-prompt",
                seed=42,
                input_hashes={"input": "two"},
                config={},
            )
            ensure_recovery_manifest(path, first, workers=5, resume=False)
            with self.assertRaisesRegex(ValueError, "incompatible"):
                ensure_recovery_manifest(path, changed, workers=3, resume=True)

    def test_explicitly_compatible_contract_signature_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            first = RecoveryContract(
                run_id="run-1",
                phase="profile",
                model="glm-5.2",
                prompt_version="test-prompt",
                seed=42,
                input_hashes={"input": "one"},
                config={"max_backfill_rounds": 3},
            )
            extended = RecoveryContract(
                run_id="run-1",
                phase="profile",
                model="glm-5.2",
                prompt_version="test-prompt",
                seed=42,
                input_hashes={"input": "one"},
                config={"max_backfill_rounds": 6},
            )
            ensure_recovery_manifest(path, first, workers=5, resume=False)
            resumed = ensure_recovery_manifest(
                path,
                extended,
                workers=5,
                resume=True,
                compatible_contract_signatures=[first.signature],
            )
            self.assertEqual(resumed["contract_signature"], extended.signature)

    def test_failure_history_is_append_only_after_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = FailureLedger(root, phase="judge", run_id="run-1")
            ledger.record_failure(
                "batch-1",
                error_type="timeout",
                error="timed out",
                attempts=4,
                recovery_round=0,
            )
            ledger.resolve("batch-1", metadata={"output": "batch-1.json"})

            history = [
                json.loads(line)
                for line in (root / "failure_history.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual([item["status"] for item in history], [
                "unresolved",
                "resolved",
            ])
            self.assertIn("resolved_at", history[-1])
            self.assertEqual(
                (root / "unresolved_failures.jsonl").read_text(encoding="utf-8"),
                "",
            )

    def test_resolving_unknown_item_does_not_rewrite_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = FailureLedger(Path(directory), phase="judge", run_id="run-1")
            with patch("agent_env.recovery.atomic_write_jsonl") as write_snapshot:
                ledger.resolve("already-valid")
            write_snapshot.assert_not_called()

    def test_invalid_artifact_can_be_quarantined_and_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "batch.jsonl"
            output.write_text('{"ok": true}\n{"truncated":', encoding="utf-8")
            moved = quarantine_artifact(
                output,
                root / "quarantine",
                item_id="batch-1",
                reason="invalid_jsonl",
            )
            atomic_write_jsonl(output, [{"ok": True}, {"ok": True}])

            self.assertIsNotNone(moved)
            self.assertTrue(moved.is_file())
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
