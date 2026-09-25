import unittest

from privacy_trace.attacker_view import (
    LOSSLESS_TOOL_CATALOG,
    RAW_REPEATED,
    build_attacker_observation,
    format_attacker_observation,
    prepare_attacker_view,
)


class AttackerViewTests(unittest.TestCase):
    def test_filters_cached_and_failed_calls_but_keeps_empty_and_parameter_errors(self):
        calls = [
            {
                "tool_name": "lookup",
                "success": True,
                "outcome": "success",
                "returned_result": {"name": "visible"},
            },
            {
                "tool_name": "lookup",
                "success": True,
                "outcome": "success",
                "cache_hit": True,
                "mcp_executed": False,
                "duplicate_call_count": 2,
                "returned_result": {"name": "visible"},
            },
            {
                "tool_name": "search",
                "success": False,
                "outcome": "empty",
                "returned_result": [],
            },
            {
                "tool_name": "search",
                "success": False,
                "outcome": "application_error",
                "error": "Invalid state code: KR",
            },
            {
                "tool_name": "search",
                "success": False,
                "outcome": "application_error",
                "error": "Rate limit exceeded; retry later",
            },
            {
                "tool_name": "search",
                "success": False,
                "outcome": "transport_error",
                "error": "Connection refused",
            },
            {
                "tool_name": "search",
                "success": False,
                "outcome": "stalled_tool_loop",
                "cache_hit": True,
                "duplicate_call_count": 3,
            },
        ]

        view = prepare_attacker_view(calls)

        self.assertEqual(len(view.calls), 3)
        self.assertEqual(view.stats.removed_cached_duplicate_count, 2)
        self.assertEqual(view.stats.removed_failed_call_count, 2)
        self.assertEqual(view.stats.retained_empty_result_count, 1)
        self.assertEqual(view.stats.retained_parameter_error_count, 1)
        self.assertFalse(view.stats.prior_equivalent)
        self.assertFalse(view.stats.raw_zero_call_prior_baseline)
        self.assertFalse(view.stats.filtered_to_prior_equivalent)

    def test_zero_visible_calls_are_prior_equivalent(self):
        view = prepare_attacker_view([])

        self.assertTrue(view.stats.prior_equivalent)
        self.assertTrue(view.stats.raw_zero_call_prior_baseline)
        self.assertFalse(view.stats.filtered_to_prior_equivalent)
        self.assertEqual(view.stats.visible_call_count, 0)

    def test_all_filtered_calls_are_not_raw_zero_baseline(self):
        view = prepare_attacker_view(
            [
                {
                    "outcome": "transport_error",
                    "success": False,
                    "transport_success": False,
                }
            ]
        )

        self.assertTrue(view.stats.prior_equivalent)
        self.assertFalse(view.stats.raw_zero_call_prior_baseline)
        self.assertTrue(view.stats.filtered_to_prior_equivalent)

    def test_catalog_is_factorized_without_truncating_long_results(self):
        calls = [
            {
                "server_name": "Maps",
                "tool_name": "search",
                "tool_description": "Find places",
                "tool_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                "call_statement": {"query": "private-place-one"},
                "returned_result": "A" * 5_000 + "private-tail-one",
                "call_order": 1,
            },
            {
                "server_name": "Maps",
                "tool_name": "search",
                "tool_description": "Find places",
                "tool_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                "call_statement": {"query": "private-place-two"},
                "returned_result": "B" * 5_000 + "private-tail-two",
                "call_order": 2,
            },
        ]

        formatted = format_attacker_observation(calls)

        self.assertGreater(len(formatted.text), 10_000)
        self.assertEqual(formatted.text.count("Tool Description: Find places"), 1)
        self.assertIn("private-place-one", formatted.text)
        self.assertIn("private-place-two", formatted.text)
        self.assertIn("private-tail-one", formatted.text)
        self.assertIn("private-tail-two", formatted.text)
        self.assertEqual(formatted.stats.observation_chars, len(formatted.text))
        self.assertEqual(formatted.stats.tool_catalog_count, 1)

    def test_raw_repeated_baseline_preserves_all_calls_and_repeated_metadata(self):
        calls = [
            {
                "tool_name": "lookup",
                "tool_description": "Lookup records",
                "tool_schema": {"type": "object"},
                "call_statement": {"query": "private"},
                "returned_result": "result",
                "success": True,
                "outcome": "success",
            },
            {
                "tool_name": "lookup",
                "tool_description": "Lookup records",
                "tool_schema": {"type": "object"},
                "call_statement": {"query": "private"},
                "returned_result": "cached result",
                "success": True,
                "outcome": "success",
                "cache_hit": True,
                "mcp_executed": False,
                "duplicate_call_count": 2,
            },
        ]

        raw = build_attacker_observation(calls, RAW_REPEATED)
        catalog = build_attacker_observation(calls, LOSSLESS_TOOL_CATALOG)

        self.assertEqual(raw.view.stats.visible_call_count, 2)
        self.assertEqual(raw.view.stats.removed_cached_duplicate_count, 0)
        self.assertEqual(raw.formatted.text.count("Lookup records"), 2)
        self.assertEqual(raw.formatted.stats.tool_catalog_count, 0)
        self.assertEqual(catalog.view.stats.visible_call_count, 1)
        self.assertEqual(catalog.formatted.text.count("Lookup records"), 1)


if __name__ == "__main__":
    unittest.main()
