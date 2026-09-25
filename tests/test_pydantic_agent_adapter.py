import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from agent_env.execution_limits import ExecutionLimits
from agent_env.pydantic_agent import (
    DEFAULT_INSTRUCTIONS,
    PydanticAIAgentAdapter,
    StalledToolLoop,
    canonical_tool_signature,
    compact_tool_activity,
    server_tool_prefix,
    stringify_tool_result,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HAS_PYDANTIC_AI = importlib.util.find_spec("pydantic_ai") is not None


class PydanticAgentAdapterTests(unittest.TestCase):
    def test_default_instructions_keep_entity_resolution_and_final_text_grounded(self):
        self.assertIn("Never resolve an unspecified date", DEFAULT_INSTRUCTIONS)
        self.assertIn("# Response contract", DEFAULT_INSTRUCTIONS)
        self.assertIn("Do all analysis", DEFAULT_INSTRUCTIONS)
        self.assertIn("begin with a substantive user-facing result", DEFAULT_INSTRUCTIONS)

    def test_server_prefix_and_result_serialization_are_stable(self):
        self.assertEqual(server_tool_prefix("DailyMed MCP"), "dailymed_mcp")
        self.assertEqual(server_tool_prefix("Geoapify"), "geoapify")
        self.assertEqual(
            stringify_tool_result({"value": "上海", "items": [1]}),
            '{"value": "上海", "items": [1]}',
        )

    def test_canonical_signature_ignores_mapping_key_order(self):
        first = canonical_tool_signature(
            "Search", "lookup", {"query": "agent", "filters": {"b": 2, "a": 1}}
        )
        second = canonical_tool_signature(
            "Search", "lookup", {"filters": {"a": 1, "b": 2}, "query": "agent"}
        )
        self.assertEqual(first, second)

    def test_duplicate_call_is_cached_then_stalls_without_more_mcp_calls(self):
        adapter = PydanticAIAgentAdapter(
            base_url="http://unused.test/v1",
            api_key="test",
            model_name="test",
            server_names=["Search"],
            execution_limits=ExecutionLimits(),
        )
        callback = adapter._process_tool_call("Search")
        ctx = SimpleNamespace(run_step=1, tool_call_id="provider-call")
        mcp_calls = 0

        async def call_tool(_name, _args):
            nonlocal mcp_calls
            mcp_calls += 1
            return {"items": ["one result"]}

        async def exercise():
            first = await callback(ctx, call_tool, "lookup", {"q": "same"})
            second = await callback(ctx, call_tool, "lookup", {"q": "same"})
            with self.assertRaises(StalledToolLoop):
                await callback(ctx, call_tool, "lookup", {"q": "same"})
            return first, second

        first, second = asyncio.run(exercise())

        self.assertEqual(first, {"items": ["one result"]})
        self.assertIn("already executed", second)
        self.assertEqual(mcp_calls, 1)
        self.assertEqual(len(adapter._execution_results), 3)
        self.assertTrue(adapter._execution_results[0]["mcp_executed"])
        self.assertTrue(adapter._execution_results[1]["cache_hit"])
        self.assertFalse(adapter._execution_results[1]["mcp_executed"])
        self.assertEqual(
            adapter._execution_results[2]["outcome"], "stalled_tool_loop"
        )
        self.assertFalse(adapter._execution_results[2]["success"])

    def test_parallel_fourth_duplicate_is_suppressed_after_third_stalls(self):
        adapter = PydanticAIAgentAdapter(
            base_url="http://unused.test/v1",
            api_key="test",
            model_name="test",
            server_names=["Search"],
            execution_limits=ExecutionLimits(),
        )
        callback = adapter._process_tool_call("Search")
        ctx = SimpleNamespace(run_step=1, tool_call_id="provider-call")
        mcp_calls = 0

        async def call_tool(_name, _args):
            nonlocal mcp_calls
            mcp_calls += 1
            await asyncio.sleep(0)
            return {"items": ["one result"]}

        async def exercise():
            return await asyncio.gather(
                *[
                    callback(ctx, call_tool, "lookup", {"q": "same"})
                    for _ in range(4)
                ],
                return_exceptions=True,
            )

        results = asyncio.run(exercise())

        self.assertEqual(mcp_calls, 1)
        self.assertEqual(len(adapter._execution_results), 3)
        self.assertEqual(
            [item["duplicate_call_count"] for item in adapter._execution_results],
            [1, 2, 3],
        )
        self.assertEqual(
            sum(isinstance(item, StalledToolLoop) for item in results),
            2,
        )

    def test_old_tool_results_are_shortened_in_compacted_history(self):
        raw_result = "raw-result-" + ("x" * 1000)
        summary = compact_tool_activity(
            [
                {
                    "server_name": "Search",
                    "tool_name": "lookup",
                    "call_statement": {"q": "agent"},
                    "success": True,
                    "outcome": "success",
                    "returned_result": raw_result,
                }
            ]
        )

        self.assertIn('"tool":"lookup"', summary)
        self.assertIn('"args":{"q":"agent"}', summary)
        self.assertIn('"success":true', summary)
        self.assertLess(len(summary), len(raw_result))
        self.assertNotIn(raw_result, summary)

    @unittest.skipUnless(HAS_PYDANTIC_AI, "PydanticAI is unavailable")
    def test_cross_turn_history_contains_no_raw_tool_parts_or_full_results(self):
        adapter = PydanticAIAgentAdapter(
            base_url="http://unused.test/v1",
            api_key="test",
            model_name="test",
            server_names=["Search"],
            execution_limits=ExecutionLimits(),
        )
        raw_result = "raw-result-" + ("x" * 1000)
        adapter._append_compacted_turn_history(
            adapter._load_runtime(),
            user_message="Find the item.",
            assistant_response="I found one matching item.",
            executions=[
                {
                    "server_name": "Search",
                    "tool_name": "lookup",
                    "call_statement": {"q": "item"},
                    "success": True,
                    "outcome": "success",
                    "returned_result": raw_result,
                }
            ],
        )

        self.assertEqual(len(adapter._message_history), 2)
        part_kinds = [
            part.part_kind
            for message in adapter._message_history
            for part in message.parts
        ]
        self.assertEqual(part_kinds, ["user-prompt", "text"])
        carried_text = adapter._message_history[1].parts[0].content
        self.assertIn("I found one matching item.", carried_text)
        self.assertIn("<prior_tool_activity>", carried_text)
        self.assertNotIn(raw_result, carried_text)

    @unittest.skipUnless(
        HAS_PYDANTIC_AI
        and (PROJECT_ROOT / "mcp_servers/dailymed-mcp-server/dist/index.js").is_file(),
        "PydanticAI or built DailyMed MCP fixture is unavailable",
    )
    def test_real_existing_mcp_server_connection_and_tool_trace(self):
        from pydantic_ai.models.test import TestModel

        async def exercise():
            adapter = PydanticAIAgentAdapter(
                base_url="http://unused.test/v1",
                api_key="test",
                model_name="test",
                model=TestModel(
                    call_tools=["dailymed_mcp_search_spls"],
                    custom_output_text="Search complete.",
                ),
                server_names=["DailyMed MCP"],
                tool_allowlists={"DailyMed MCP": ["search_spls"]},
                execution_limits=ExecutionLimits(
                    max_rounds=3,
                    max_tool_calls=2,
                ),
                project_dir=PROJECT_ROOT,
            )
            try:
                await adapter.initialize()
                discovered = adapter.discovered_tools()
                result = await adapter.respond(
                    "Search official DailyMed labels.",
                    timeout_seconds=30,
                )
                return adapter.connections, discovered, result
            finally:
                await adapter.close()

        connections, discovered, result = asyncio.run(exercise())

        self.assertEqual(connections["runtime"], "pydantic-ai")
        self.assertEqual(connections["connected_servers"], ["DailyMed MCP"])
        self.assertEqual(
            connections["exposed_tools_by_server"],
            {"DailyMed MCP": ["search_spls"]},
        )
        self.assertEqual(connections["discovered_tools_by_server"], {"DailyMed MCP": 6})
        self.assertIn(
            "search_spls",
            {
                tool["name"]
                for tool in discovered["DailyMed MCP"]
            },
        )
        self.assertEqual(result.response, "Search complete.")
        self.assertEqual(result.tool_calls, 1)
        call = result.execution_results[0]
        self.assertEqual(call["server_name"], "DailyMed MCP")
        self.assertEqual(call["tool_name"], "search_spls")
        self.assertEqual(call["call_order"], 1)
        self.assertTrue(call["transport_success"])
        self.assertIn(call["outcome"], {"success", "application_error"})


if __name__ == "__main__":
    unittest.main()
