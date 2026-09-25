import json
import unittest

from privacy_trace.scenario_generator import (
    DOMAINS,
    GenerationConfig,
    SCENARIO_PIPELINE_ID,
    SCENARIO_PROMPT_ID,
    ToolSpec,
    build_arg_parser,
    build_evaluation_criteria,
    build_profile_conditioned_planner_prompt,
    generate_scenarios_resumable,
    load_tool_pool,
    make_case,
    make_jobs,
    parse_planner_decision,
    profile_for_prompt,
    sample_candidate_tool_subset,
    validate_case_record,
)
from scripts.build_scenario_tool_pool import build_tool_pool, merge_discoveries


def make_tool(domain="travel", index=1, *, required=("query",)):
    server = f"{domain}-server-{index}"
    return ToolSpec(
        server_name=server,
        tool_name=f"lookup_{index}",
        qualified_name=f"{server}:lookup_{index}",
        description="Retrieve one grounded result.",
        input_schema={
            "type": "object",
            "properties": {field: {"type": "string"} for field in required},
            "required": list(required),
        },
        domains=(domain,),
    )


def make_pool(per_domain=2):
    return [
        make_tool(domain, index)
        for domain in DOMAINS
        for index in range(1, per_domain + 1)
    ]


def planner_payload(prompt, *, accepted=True, time_text=None):
    tools = json.loads(
        prompt.split("<TOOLS_JSON>\n", 1)[1].split("\n</TOOLS_JSON>", 1)[0]
    )
    if not accepted:
        return {
            "accepted": False,
            "reason": "This candidate draw does not fit the user.",
            "user_task": None,
            "selected_tools": [],
            "tool_roles": [],
        }
    tool = tools[0]
    time_text = time_text or "tomorrow"
    goal = f"Compare suitable options {time_text}."
    return {
        "accepted": True,
        "reason": "The task is plausible and can be attempted with this tool.",
        "user_task": {
            "goal": goal,
            "context": "The user needs an answer before an upcoming outing.",
            "constraints": ["Use the user's stated destination."],
            "expected_result": "A grounded comparison that supports a decision.",
        },
        "selected_tools": [tool["qualified_name"]],
        "tool_roles": [
            {
                "qualified_name": tool["qualified_name"],
                "order": 1,
                "necessary_use": "Retrieve the necessary grounded options.",
                "required_input_bindings": {
                    field: "A concrete value supplied by the user."
                    for field in tool["input_schema"].get("required", [])
                },
            }
        ],
    }


class ScenarioGenerationTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "profile_id": "P0001",
            "occupation": "teacher",
            "name": "Private Name",
            "phone_number": "+1-202-555-0100",
            "email": "private@example.com",
            "government_id": "SECRET-ID-123",
        }
        self.tools = [make_tool()]
        self.config = GenerationConfig(
            model="test",
            base_url="http://test",
            api_key="test",
            workers=1,
            retries=0,
            candidate_tool_probability=1.0,
            max_task_construction_attempts=3,
        )

    def test_defaults_remove_reference_date_and_reviewer_options(self):
        args = build_arg_parser().parse_args(["--n", "1"])
        self.assertFalse(hasattr(args, "reference_date"))
        self.assertFalse(hasattr(args, "review_batch_size"))
        self.assertFalse(hasattr(args, "skip_review"))
        self.assertEqual(args.max_task_construction_attempts, 8)
        self.assertEqual(self.config.seed, 42)
        self.assertEqual(
            SCENARIO_PIPELINE_ID,
            "profile_domain_candidate_task",
        )

    def test_profile_conditioned_prompt_contains_profile_and_relative_time_policy(self):
        prompt = build_profile_conditioned_planner_prompt(
            self.profile,
            "travel",
            self.tools,
            scenario_id="S0001",
            attempt=1,
        )
        self.assertIn('"occupation": "teacher"', prompt)
        self.assertIn("Complete synthetic profile", prompt)
        self.assertIn("Use relative time only", prompt)
        self.assertIn("Never emit a year", prompt)
        self.assertIn("accepted", prompt)

    def test_known_runtime_unavailable_tools_are_not_candidates(self):
        tools = load_tool_pool()
        self.assertNotIn(
            "OneBusAway:onebusaway_search_routes",
            {tool.qualified_name for tool in tools},
        )

    def test_out_of_coverage_attempt_is_allowed_for_trajectory(self):
        onebus = next(
            tool for tool in load_tool_pool()
            if tool.qualified_name == "OneBusAway:onebusaway_find_stops"
        )
        payload = {
            "accepted": True,
            "reason": "The user wants local transit information.",
            "user_task": {
                "goal": "Find bus stops near my home in Andong.",
                "context": "I live in Andong, South Korea.",
                "constraints": ["Keep the walk short."],
                "expected_result": "Nearby bus stops with useful route details.",
            },
            "selected_tools": [onebus.qualified_name],
            "tool_roles": [{
                "qualified_name": onebus.qualified_name,
                "order": 1,
                "necessary_use": "Find nearby stops.",
                "required_input_bindings": {"lat": 36.56, "lon": 128.73},
            }],
        }
        result = parse_planner_decision(
            json.dumps(payload), [onebus],
            profile=self.profile, domain="travel",
        )
        self.assertTrue(result["accepted"])

    def test_uncertain_cross_provider_handoff_is_allowed_for_trajectory(self):
        pool = load_tool_pool()
        selected = [
            next(tool for tool in pool if tool.qualified_name == qualified)
            for qualified in (
                "Paper Search:search_google_scholar",
                "Paper Search:read_arxiv_paper",
            )
        ]
        payload = {
            "accepted": True,
            "reason": "The user needs to review a paper.",
            "user_task": {
                "goal": "Find and summarize a recent research paper.",
                "context": "I am preparing a course note.",
                "constraints": ["Use a relevant paper."],
                "expected_result": "A sourced summary of one paper.",
            },
            "selected_tools": [tool.qualified_name for tool in selected],
            "tool_roles": [
                {
                    "qualified_name": selected[0].qualified_name,
                    "order": 1,
                    "necessary_use": "Find papers.",
                    "required_input_bindings": {"query": "machine learning"},
                },
                {
                    "qualified_name": selected[1].qualified_name,
                    "order": 2,
                    "necessary_use": "Read the selected paper.",
                    "required_input_bindings": {
                        "paper_id": "paper ID from the prior Google Scholar result"
                    },
                },
            ],
        }
        result = parse_planner_decision(
            json.dumps(payload), selected,
            profile=self.profile, domain="career_learning",
        )
        self.assertTrue(result["accepted"])

    def test_rejected_planner_decision_has_no_task_or_tools(self):
        prompt = build_profile_conditioned_planner_prompt(
            self.profile, "travel", self.tools,
            scenario_id="S0001", attempt=1,
        )
        result = parse_planner_decision(
            json.dumps(planner_payload(prompt, accepted=False)),
            self.tools,
            profile=self.profile,
            domain="travel",
        )
        self.assertFalse(result["accepted"])

    def test_accepted_plan_requires_candidate_subset_and_complete_bindings(self):
        prompt = build_profile_conditioned_planner_prompt(
            self.profile, "travel", self.tools,
            scenario_id="S0001", attempt=1,
        )
        payload = planner_payload(prompt)
        result = parse_planner_decision(
            json.dumps(payload), self.tools,
            profile=self.profile, domain="travel",
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["selected_tools"], self.tools)

        payload["tool_roles"][0]["required_input_bindings"]["query"] = {
            "source": "user_task",
            "value": "the stated destination",
        }
        structured = parse_planner_decision(
            json.dumps(payload), self.tools,
            profile=self.profile, domain="travel",
        )
        self.assertIsInstance(
            structured["tool_roles"][0]["required_input_bindings"]["query"],
            dict,
        )

        payload["tool_roles"][0]["required_input_bindings"] = {}
        with self.assertRaisesRegex(RuntimeError, "missing"):
            parse_planner_decision(
                json.dumps(payload), self.tools,
                profile=self.profile, domain="travel",
            )

        payload = planner_payload(prompt)
        payload["selected_tools"] = ["unknown:tool"]
        with self.assertRaisesRegex(RuntimeError, "candidate subset"):
            parse_planner_decision(
                json.dumps(payload), self.tools,
                profile=self.profile, domain="travel",
            )

    def test_rejection_resamples_then_publishes_minimal_case_and_trace(self):
        calls = {"planner": 0}

        def caller(prompt, _config):
            calls["planner"] += 1
            return json.dumps(
                planner_payload(prompt, accepted=calls["planner"] > 1)
            )

        cases, traces, failures = generate_scenarios_resumable(
            [self.profile],
            self.config,
            caller=caller,
            domains=("travel",),
            tool_pool=self.tools,
        )
        self.assertFalse(failures)
        self.assertEqual(len(cases), 1)
        self.assertEqual(
            set(cases[0]),
            {"scenario_id", "profile_id", "domain", "user_task", "evaluation_criteria"},
        )
        self.assertNotIn("tool_roles", cases[0])
        self.assertEqual(
            [item["decision"] for item in traces[0]["attempts"]],
            ["planner_rejected", "accepted"],
        )
        self.assertEqual(traces[0]["generation"]["prompt"], SCENARIO_PROMPT_ID)

    def test_attempt_exhaustion_returns_terminal_failure(self):
        config = GenerationConfig(
            model="test", base_url="x", api_key="x",
            workers=1, retries=0, candidate_tool_probability=1,
            max_task_construction_attempts=8,
        )

        def caller(prompt, _config):
            return json.dumps(planner_payload(prompt, accepted=False))

        cases, traces, failures = generate_scenarios_resumable(
            [self.profile], config, caller=caller,
            domains=("travel",), tool_pool=self.tools,
        )
        self.assertFalse(cases)
        self.assertFalse(traces)
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(failures[0]["attempts"]), 8)
        self.assertEqual(
            failures[0]["generation"]["pipeline"], SCENARIO_PIPELINE_ID
        )

        def unexpected(_prompt, _config):
            raise AssertionError("terminal failures must persist across resume")

        resumed_cases, resumed_traces, resumed_failures = generate_scenarios_resumable(
            [self.profile], config,
            existing_terminal_failures=failures,
            caller=unexpected,
            domains=("travel",),
            tool_pool=self.tools,
        )
        self.assertFalse(resumed_cases)
        self.assertFalse(resumed_traces)
        self.assertEqual(resumed_failures, failures)

        def recovered(prompt, _config):
            return json.dumps(planner_payload(prompt))

        recovered_cases, recovered_traces, unresolved = (
            generate_scenarios_resumable(
                [self.profile],
                config,
                retry_terminal_failures=failures,
                caller=recovered,
                domains=("travel",),
                tool_pool=self.tools,
            )
        )
        self.assertEqual(len(recovered_cases), 1)
        self.assertFalse(unresolved)
        self.assertEqual(len(recovered_traces[0]["attempts"]), 9)
        self.assertEqual(
            recovered_traces[0]["attempts"][-1]["recovery_round"], 1
        )

    def test_resume_reuses_only_current_case_with_matching_trace(self):
        def caller(prompt, _config):
            return json.dumps(planner_payload(prompt))

        cases, traces, failures = generate_scenarios_resumable(
            [self.profile], self.config, caller=caller,
            domains=("travel",), tool_pool=self.tools,
        )
        self.assertFalse(failures)

        def unexpected(_prompt, _config):
            raise AssertionError("resume should not call the LLM")

        resumed_cases, resumed_traces, resumed_failures = generate_scenarios_resumable(
            [self.profile], self.config,
            existing_cases=cases,
            existing_traces=traces,
            caller=unexpected,
            domains=("travel",),
            tool_pool=self.tools,
        )
        self.assertEqual(resumed_cases, cases)
        self.assertEqual(resumed_traces, traces)
        self.assertFalse(resumed_failures)

        traces[0]["generation"]["prompt"] = "stale"
        calls = {"count": 0}

        def regenerate(prompt, _config):
            calls["count"] += 1
            return json.dumps(planner_payload(prompt))

        regenerated, regenerated_traces, regenerated_failures = (
            generate_scenarios_resumable(
                [self.profile], self.config,
                existing_cases=cases,
                existing_traces=traces,
                caller=regenerate,
                domains=("travel",),
                tool_pool=self.tools,
            )
        )
        self.assertTrue(calls["count"])
        self.assertFalse(regenerated_failures)
        self.assertEqual(
            regenerated_traces[0]["generation"]["prompt"],
            SCENARIO_PROMPT_ID,
        )
        self.assertEqual(len(regenerated), 1)

    def test_case_validation_and_deterministic_evaluation_criteria(self):
        decision = {
            "user_task": {
                "goal": "Find an option tomorrow.",
                "context": "An outing is coming up.",
                "constraints": [],
                "expected_result": "One grounded option.",
            },
            "selected_tools": self.tools,
        }
        job = make_jobs([self.profile], ("travel",))[0]
        case = make_case(job, decision)
        self.assertFalse(validate_case_record(case))
        self.assertEqual(
            case["evaluation_criteria"],
            build_evaluation_criteria(case["user_task"]),
        )

    def test_candidate_subset_is_reproducible_and_non_empty(self):
        pool = make_pool(8)
        first = sample_candidate_tool_subset(
            pool, domain="travel", inclusion_probability=0.25,
            seed=42, scenario_id="S0001", draw_attempt=1,
        )
        second = sample_candidate_tool_subset(
            reversed(pool), domain="travel", inclusion_probability=0.25,
            seed=42, scenario_id="S0001", draw_attempt=1,
        )
        self.assertEqual(first, second)
        self.assertTrue(first)

    def test_profile_prompt_keeps_complete_synthetic_profile(self):
        self.assertEqual(profile_for_prompt(self.profile), self.profile)

    def test_tool_discovery_and_pool_builder_still_use_live_allowlists(self):
        merged = merge_discoveries([
            {"results": [{"name": "Server", "tools": [{"name": "old"}]}]},
            {"results": [{"name": "Server", "tools": [{"name": "new"}]}]},
        ])
        self.assertEqual(merged["results"][0]["tools"], [{"name": "new"}])
        pool = build_tool_pool(
            {"results": [{
                "name": "Selected", "connected": True,
                "tools": [
                    {"name": "allowed", "description": "Allowed", "input_schema": {"type": "object"}},
                    {"name": "hidden", "description": "Hidden", "input_schema": {"type": "object"}},
                ],
            }]},
            {"travel": {"core_servers": ["Selected"], "tool_allowlists": {"Selected": ["allowed"]}}},
        )
        self.assertEqual(pool["tool_count"], 1)
        self.assertEqual(pool["tools"][0]["tool_name"], "allowed")

    def test_repository_tool_pool_loads(self):
        self.assertTrue(load_tool_pool())


if __name__ == "__main__":
    unittest.main()
