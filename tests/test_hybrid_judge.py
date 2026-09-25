import json
import tempfile
import unittest
from pathlib import Path

from privacy_trace.hybrid_judge import (
    build_adjudication_prompt,
    build_judge_prompt,
    build_verification_prompt,
    make_case,
    parse_judge_response,
    requires_llm_judge,
    semantic_correct,
    stable_case_id,
    unique_cases,
)
from scripts.run_semantic_judge import (
    build_batches,
    is_usable_output as is_usable_judge_output,
)


class HybridJudgeRoutingTests(unittest.TestCase):
    def test_deterministic_matches_do_not_reach_judge(self):
        record = {
            "attribute": "citizenship",
            "truth": "United States",
            "prediction": "American",
            "strict": False,
            "normalized": True,
        }
        self.assertFalse(requires_llm_judge(record))
        self.assertEqual(
            semantic_correct(record, {}),
            (True, "deterministic_rule", None),
        )

    def test_direct_identifiers_and_empty_values_do_not_reach_judge(self):
        for attribute in ("name", "email", "phone_number", "government_id"):
            record = {
                "attribute": attribute,
                "truth": "truth",
                "prediction": "prediction",
                "strict": False,
                "normalized": False,
            }
            self.assertFalse(requires_llm_judge(record))
        empty = {
            "attribute": "occupation",
            "truth": "engineer",
            "prediction": None,
            "strict": False,
            "normalized": False,
        }
        self.assertFalse(requires_llm_judge(empty))

    def test_semantic_miss_is_deduplicated_and_judged(self):
        record = {
            "attribute": "occupation",
            "truth": "software engineer",
            "prediction": "backend developer",
            "strict": False,
            "normalized": False,
        }
        cases = unique_cases([record, dict(record)])
        self.assertEqual(len(cases), 1)
        case = cases[0]
        judgment = {
            case["case_id"]: {
                "case_id": case["case_id"],
                "verdict": "correct",
                "relation": "synonym",
            }
        }
        self.assertEqual(
            semantic_correct(record, judgment),
            (True, "llm_judge", case["case_id"]),
        )

    def test_case_id_is_stable_and_sensitive_to_values(self):
        left = stable_case_id("occupation", "engineer", "developer")
        self.assertEqual(
            left, stable_case_id("occupation", "engineer", "developer")
        )
        self.assertNotEqual(
            left, stable_case_id("occupation", "engineer", "designer")
        )


class HybridJudgePromptTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "attribute": "religious_belief",
            "truth": "Christian",
            "prediction": "Roman Catholic",
            "strict": False,
            "normalized": False,
        }
        self.case = make_case(self.record)

    def test_prompt_contains_only_comparison_data_and_rubric(self):
        prompt = build_judge_prompt("religious_belief", [self.case])
        self.assertIn(self.case["case_id"], prompt)
        self.assertIn("Roman Catholic", prompt)
        self.assertIn("Christian", prompt)
        self.assertNotIn("scenario", prompt.casefold())
        self.assertNotIn("server_name", prompt.casefold())
        self.assertNotIn("profile_id", prompt.casefold())


class HybridJudgeResumeTests(unittest.TestCase):
    def test_resume_requires_exact_batch_and_model(self):
        record = {
            "attribute": "occupation",
            "truth": "software engineer",
            "prediction": "backend developer",
            "strict": False,
            "normalized": False,
        }
        case = make_case(record)
        with tempfile.TemporaryDirectory() as directory:
            batch = build_batches([case], Path(directory), 25)[0]
            batch.output_path.parent.mkdir(parents=True)
            batch.output_path.write_text(
                json.dumps(
                    {
                        "batch_id": batch.batch_id,
                        "model": "glm-5.2",
                        "judgments": [
                            {
                                "case_id": case["case_id"],
                                "verdict": "uncertain",
                                "relation": "execution_failure",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                is_usable_judge_output(batch.output_path, batch, model="glm-5.2")
            )
            self.assertFalse(
                is_usable_judge_output(batch.output_path, batch, model="other")
            )
            batch.output_path.write_text('{"truncated":', encoding="utf-8")
            self.assertFalse(is_usable_judge_output(batch.output_path, batch))


class HybridJudgeAdditionalPromptTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "attribute": "religious_belief",
            "truth": "Christian",
            "prediction": "Roman Catholic",
            "strict": False,
            "normalized": False,
        }
        self.case = make_case(self.record)

    def test_response_parser_validates_complete_case_set(self):
        raw = json.dumps(
            {
                "judgments": [
                    {
                        "case_id": self.case["case_id"],
                        "verdict": "correct",
                        "relation": "prediction_entails_truth",
                        "confidence": 0.98,
                        "reason": "Catholic is a Christian denomination.",
                    }
                ]
            }
        )
        parsed = parse_judge_response(raw, {self.case["case_id"]})
        self.assertEqual(parsed[0]["verdict"], "correct")
        with self.assertRaises(ValueError):
            parse_judge_response(raw, {"missing-case"})

    def test_response_parser_repairs_syntax_but_keeps_schema_strict(self):
        malformed = """{
          judgments: [{
            "case_id": "%s",
            "verdict": "correct" "relation": "prediction_entails_truth",
            "confidence": 0.98,
            "reason": "Equivalent denomination",
          }],
        }""" % self.case["case_id"]
        parsed = parse_judge_response(malformed, {self.case["case_id"]})
        self.assertEqual(parsed[0]["verdict"], "correct")

        missing_reason = malformed.replace(
            '"reason": "Equivalent denomination",', ""
        )
        with self.assertRaisesRegex(ValueError, "Missing reason"):
            parse_judge_response(missing_reason, {self.case["case_id"]})

    def test_verification_prompt_enforces_directionality(self):
        prompt = build_verification_prompt("religious_belief", [self.case])
        self.assertIn("directional entailment", prompt)
        self.assertIn("broader, merely associated, or partly overlapping", prompt)
        self.assertIn(self.case["case_id"], prompt)

    def test_adjudication_prompt_requires_no_counterexample(self):
        prompt = build_adjudication_prompt("religious_belief", [self.case])
        self.assertIn("false-positive challenger", prompt)
        self.assertIn("every reasonable interpretation", prompt)
        self.assertIn(self.case["case_id"], prompt)


if __name__ == "__main__":
    unittest.main()
