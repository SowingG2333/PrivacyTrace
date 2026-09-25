import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_agent_scenarios import (  # noqa: E402
    assess_trajectory,
    build_failure_records,
    build_arg_parser,
    build_single_command,
    has_usable_trajectory,
    mark_recovery_manifest_interrupted,
    run_isolated_subprocess,
    select_cases,
    termination_signal_handlers,
    terminate_active_processes,
)


def make_args(**overrides):
    values = {
        "scenario_ids": None,
        "domains": None,
        "limit": None,
        "one_per_profile": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class BatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"scenario_id": "S0001", "domain": "travel"},
            {"scenario_id": "S0002", "domain": "health"},
            {"scenario_id": "S0003", "domain": "travel"},
        ]

    def test_filters_by_domain_and_limit_in_input_order(self):
        selected = select_cases(
            self.records, make_args(domains=["travel"], limit=1)
        )
        self.assertEqual([item["scenario_id"] for item in selected], ["S0001"])

    def test_unknown_explicit_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            select_cases(self.records, make_args(scenario_ids=["S9999"]))

    def test_one_per_profile_rotates_domains(self):
        records = [
            {
                "scenario_id": f"S{profile * 4 + domain + 1:04d}",
                "profile_id": f"P{profile + 1:04d}",
                "domain": name,
            }
            for profile in range(4)
            for domain, name in enumerate(
                ("travel", "health", "shopping", "career_learning")
            )
        ]

        selected = select_cases(records, make_args(one_per_profile=True))

        self.assertEqual(len(selected), 4)
        self.assertEqual(
            [item["domain"] for item in selected],
            ["travel", "health", "shopping", "career_learning"],
        )
        self.assertEqual(len({item["profile_id"] for item in selected}), 4)

    def test_workers_cli_defaults_to_one_and_accepts_parallelism(self):
        parser = build_arg_parser()

        self.assertEqual(parser.parse_args([]).workers, 1)
        self.assertEqual(parser.parse_args([]).retries, 1)
        self.assertEqual(parser.parse_args([]).wall_timeout, 1800.0)
        self.assertFalse(parser.parse_args([]).no_shared_mcp)
        self.assertEqual(parser.parse_args([]).shared_mcp_startup_timeout, 180.0)
        self.assertEqual(parser.parse_args([]).env_config.name, "agent_env.json")
        self.assertEqual(
            parser.parse_args([]).profiles,
            PROJECT_ROOT
            / "artifacts/profile_pool/profiles.jsonl",
        )
        self.assertEqual(
            parser.parse_args([]).scenarios,
            PROJECT_ROOT
            / "artifacts/scenario/cases.jsonl",
        )
        self.assertEqual(
            parser.parse_args([]).output_dir,
            PROJECT_ROOT / "artifacts/trajectory_runs/latest",
        )
        self.assertEqual(parser.parse_args(["--workers", "5"]).workers, 5)
        self.assertEqual(
            parser.parse_args(["--env-config", "custom.json"]).env_config,
            Path("custom.json"),
        )

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "no SIGTERM")
    def test_sigterm_uses_keyboard_interrupt_cleanup_path(self):
        previous = signal.getsignal(signal.SIGTERM)
        with termination_signal_handlers():
            handler = signal.getsignal(signal.SIGTERM)
            with self.assertRaises(KeyboardInterrupt):
                handler(signal.SIGTERM, None)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_interruption_marks_only_running_recovery_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps({"status": "running", "attempt_count": 2}),
                encoding="utf-8",
            )

            mark_recovery_manifest_interrupted(
                path, unresolved_failure_count=7
            )
            interrupted = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual(interrupted["unresolved_failure_count"], 7)
            self.assertIn("interrupted_at", interrupted)

            path.write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            mark_recovery_manifest_interrupted(path)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["status"],
                "completed",
            )

    def test_single_command_receives_shared_mcp_config(self):
        parser = build_arg_parser()
        args = parser.parse_args([])
        args.shared_mcp_config = PROJECT_ROOT / "artifacts/shared/endpoints.json"

        command = build_single_command(
            args,
            "S0001",
            PROJECT_ROOT / "artifacts/trajectories/S0001.json",
        )

        index = command.index("--shared-mcp-config")
        self.assertEqual(
            command[index + 1],
            str(args.shared_mcp_config.resolve()),
        )
    def test_resume_only_skips_usable_trajectories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0001.json"
            path.write_text(
                json.dumps({
                    "status": "satisfied",
                    "tools_info": [{"tool": "x"}],
                    "server_connections": {
                        "configured_count": 2,
                        "connected_count": 2,
                        "failed_servers": [],
                    },
                }),
                encoding="utf-8",
            )
            self.assertTrue(has_usable_trajectory(path))

            path.write_text(
                json.dumps({"status": "environment_error", "tools_info": []}),
                encoding="utf-8",
            )
            self.assertFalse(has_usable_trajectory(path))

            path.write_text(
                json.dumps({
                    "status": "satisfied",
                    "tools_info": [{"tool": "x"}],
                    "server_connections": {
                        "configured_count": 2,
                        "connected_count": 1,
                        "failed_servers": ["broken"],
                    },
                }),
                encoding="utf-8",
            )
            self.assertFalse(has_usable_trajectory(path))

    def test_evaluator_pass_allows_empty_tools_and_optional_server_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0004.json"
            path.write_text(
                json.dumps({
                    "status": "satisfied",
                    "tools_info": [],
                    "evaluation": {"passed": True},
                    "server_connections": {
                        "configured_servers": ["Wikipedia", "Remoote Jobs"],
                        "connected_servers": ["Wikipedia"],
                        "failed_servers": ["Remoote Jobs"],
                        "configured_count": 2,
                        "connected_count": 1,
                    },
                }),
                encoding="utf-8",
            )

            assessment = assess_trajectory(path, ["Wikipedia"])

        self.assertTrue(assessment["usable"])
        self.assertEqual(
            assessment["failed_optional_servers"], ["Remoote Jobs"]
        )

    def test_required_server_failure_is_infrastructure_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0004.json"
            path.write_text(
                json.dumps({
                    "status": "satisfied",
                    "tools_info": [],
                    "evaluation": {"passed": True},
                    "server_connections": {
                        "configured_servers": ["Wikipedia", "Remoote Jobs"],
                        "connected_servers": ["Wikipedia"],
                        "failed_servers": ["Remoote Jobs"],
                        "configured_count": 2,
                        "connected_count": 1,
                    },
                }),
                encoding="utf-8",
            )

            assessment = assess_trajectory(
                path, ["Wikipedia", "Remoote Jobs"]
            )

        self.assertFalse(assessment["usable"])
        self.assertEqual(assessment["failure_type"], "infrastructure")
        self.assertEqual(
            assessment["failure_reason"], "required_server_unavailable"
        )

    def test_agent_task_failure_is_retained_as_usable_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "S0008.json"
            path.write_text(
                json.dumps({
                    "status": "max_user_turns",
                    "tools_info": [],
                    "evaluation": {"passed": False},
                    "server_connections": {
                        "configured_servers": ["Wikipedia"],
                        "connected_servers": ["Wikipedia"],
                        "failed_servers": [],
                        "configured_count": 1,
                        "connected_count": 1,
                    },
                }),
                encoding="utf-8",
            )

            assessment = assess_trajectory(path, ["Wikipedia"])

        self.assertTrue(assessment["usable"])
        self.assertIsNone(assessment["failure_type"])
        self.assertIsNone(assessment["failure_reason"])
        self.assertFalse(assessment["evaluator_passed"])

    def test_resume_skips_are_not_published_as_failures(self):
        failures = build_failure_records(
            [
                {
                    "scenario_id": "S0001",
                    "status": "skipped",
                    "usable": True,
                },
                {
                    "scenario_id": "S0002",
                    "status": "failed",
                    "usable": False,
                    "failure_type": "infrastructure",
                },
            ],
            {"S0001": "P0001", "S0002": "P0002"},
        )

        self.assertEqual([item["scenario_id"] for item in failures], ["S0002"])

    def test_wall_timeout_terminates_isolated_child(self):
        result = run_isolated_subprocess(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            wall_timeout=0.05,
            termination_grace_period=0.05,
        )

        self.assertTrue(result["timed_out"])
        self.assertLess(result["duration_seconds"], 2)

    @unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
    def test_normal_exit_cleans_up_orphaned_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            child_pid_path = Path(directory) / "child.pid"
            child_code = "import time; time.sleep(30)"
            parent_code = (
                "import pathlib, subprocess, sys; "
                f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid))"
            )

            result = run_isolated_subprocess(
                [sys.executable, "-c", parent_code],
                wall_timeout=5,
                termination_grace_period=0.05,
            )
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
            else:
                self.fail(f"orphaned descendant {child_pid} is still running")

        self.assertEqual(result["returncode"], 0)

    def test_interruption_terminates_registered_process_group(self):
        active_processes = {}
        active_processes_lock = threading.Lock()
        result = {}

        def run_child():
            result.update(
                run_isolated_subprocess(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    wall_timeout=60,
                    termination_grace_period=0.1,
                    active_processes=active_processes,
                    active_processes_lock=active_processes_lock,
                )
            )

        thread = threading.Thread(target=run_child)
        thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with active_processes_lock:
                if active_processes:
                    break
            time.sleep(0.01)
        else:
            self.fail("child process was not registered")

        terminate_active_processes(
            active_processes,
            active_processes_lock,
            grace_period=0.1,
        )
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertNotEqual(result["returncode"], 0)
        with active_processes_lock:
            self.assertEqual(active_processes, {})


if __name__ == "__main__":
    unittest.main()
