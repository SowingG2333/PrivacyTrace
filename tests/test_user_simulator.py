import asyncio
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_env.user_simulator import (  # noqa: E402
    SimulatorOutputError,
    UserSimulator,
    build_simulator_system_prompt,
    build_simulator_messages,
    parse_simulator_output,
)


USER_TASK = {
    "goal": "Choose a replacement phone before an upcoming trip.",
    "context": "The user's current phone stopped working.",
    "constraints": ["Keep the recommendation within budget."],
    "expected_result": "A short comparison and a justified recommendation.",
}


class StubProvider:
    deployment_name = "deepseek-v4-flash"

    def __init__(self, response):
        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls = []

    async def get_completion(
        self, system_prompt, user_prompt, max_tokens, return_usage=False,
        temperature=None, messages=None, response_format=None, extra_body=None,
        response_observer=None,
    ):
        self.calls.append(
            (
                system_prompt, user_prompt, max_tokens, return_usage,
                response_format, extra_body, messages, temperature,
            )
        )
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        usage = {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "total_tokens": 42,
        }
        if response_observer:
            response_observer(
                {
                    "provider_attempt": 1,
                    "response_id": "stub-response",
                    "finish_reason": "stop",
                    "content": response,
                    "reasoning_content_length": 0,
                    "usage": usage,
                }
            )
        return response, usage


class LegacyStubProvider:
    deployment_name = "legacy-model"

    def __init__(self):
        self.calls = []

    async def get_completion(
        self, system_prompt, user_prompt, max_tokens, return_usage=False
    ):
        self.calls.append((system_prompt, user_prompt, max_tokens, return_usage))
        return (
            '{"message":"I need help choosing a phone.","status":"continue",'
            '"remaining_requirement":"expected_result"}',
            {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        )


class UserSimulatorTests(unittest.TestCase):
    def test_policy_can_enforce_goal_boundary_and_terminal_states(self):
        prompt = build_simulator_system_prompt(
            profile={"age": 30},
            user_task=USER_TASK,
            behavior_guidelines=(
                "Never add a new request, follow-up topic, refinement, or subtask "
                "outside the original SCENARIO goal, even if the Agent offers further "
                "help. If the Agent has met expected_result, immediately return a "
                "brief closing message with satisfied and ask nothing else; if "
                "progress requires a missing fact that PROFILE cannot provide, return "
                "cannot_continue instead of inventing it or changing the task."
            ),
        )
        self.assertIn("outside the original SCENARIO goal", prompt)
        self.assertIn("closing message with satisfied", prompt)
        self.assertIn("return cannot_continue", prompt)
        self.assertIn("remaining_requirement", prompt)
        self.assertIn("Never reinterpret", prompt)
        self.assertIn('"constraint_1":"Keep the recommendation within budget."', prompt)
        self.assertIn('"expected_result":"A short comparison', prompt)

    def test_native_messages_contain_complete_private_context_and_mapped_history(self):
        messages = build_simulator_messages(
            profile={
                "age": 30,
                "occupation": "technician",
                "name": "Private User",
                "email": "private@example.com",
                "phone_number": "+1-202-555-0100",
                "government_id": "TEST-ID-123",
            },
            user_task=USER_TASK,
            behavior_guidelines="Be concise.",
            history=[
                {"role": "user", "content": "I need a phone."},
                {"role": "assistant", "content": "What is your budget?"},
            ],
        )

        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(messages[1]["content"], "Start.")
        self.assertIn('"occupation":"technician"', messages[0]["content"])
        self.assertIn("current phone stopped working", messages[0]["content"])
        self.assertIn("Be concise", messages[0]["content"])
        self.assertIn('"name":"Private User"', messages[0]["content"])
        self.assertIn('"email":"private@example.com"', messages[0]["content"])
        self.assertIn(
            '"phone_number":"+1-202-555-0100"', messages[0]["content"]
        )
        self.assertIn('"government_id":"TEST-ID-123"', messages[0]["content"])
        self.assertEqual(messages[2]["content"], "I need a phone.")
        self.assertEqual(messages[3]["content"], "What is your budget?")

    def test_first_turn_requires_continue(self):
        with self.assertRaises(ValueError):
            parse_simulator_output(
                '{"message":"Thanks","status":"satisfied",'
                '"remaining_requirement":null}',
                is_first_turn=True,
            )

    def test_repairs_minor_json_syntax(self):
        message, status, remaining = parse_simulator_output(
            "prefix {'message':'Still checking', 'status':'continue', "
            "'remaining_requirement':'expected_result',} suffix"
        )
        self.assertEqual(message, "Still checking")
        self.assertEqual(status, "continue")
        self.assertEqual(remaining, "expected_result")

    def test_remaining_requirement_matches_status_contract(self):
        with self.assertRaisesRegex(ValueError, "non-empty remaining_requirement"):
            parse_simulator_output(
                '{"message":"One more thing.","status":"continue",'
                '"remaining_requirement":null}'
            )
        with self.assertRaisesRegex(ValueError, "requires remaining_requirement=null"):
            parse_simulator_output(
                '{"message":"Thanks.","status":"satisfied",'
                '"remaining_requirement":"expected_result"}'
            )
        message, status, remaining = parse_simulator_output(
            '{"message":"I cannot provide that fact.",'
            '"status":"cannot_continue",'
            '"remaining_requirement":"constraint_1"}'
        )
        self.assertEqual(message, "I cannot provide that fact.")
        self.assertEqual(status, "cannot_continue")
        self.assertEqual(remaining, "constraint_1")
        with self.assertRaisesRegex(ValueError, "exact key"):
            parse_simulator_output(
                '{"message":"One more thing.","status":"continue",'
                '"remaining_requirement":"invented_task"}',
                allowed_requirements=("constraint_1", "expected_result"),
            )

    def test_simulator_returns_validated_turn_and_usage(self):
        provider = StubProvider(
            '{"message":"Could you help me compare a few options?",'
            '"status":"continue",'
            '"remaining_requirement":"expected_result"}'
        )
        simulator = UserSimulator(
            provider=provider,
            profile={"age": 30},
            user_task=USER_TASK,
            behavior_guidelines="Be concise.",
        )

        turn = asyncio.run(simulator.next_turn([]))

        self.assertEqual(turn.status, "continue")
        self.assertEqual(
            turn.remaining_requirement, "expected_result"
        )
        self.assertIn("compare", turn.message)
        self.assertEqual(turn.usage["total_tokens"], 42)
        self.assertTrue(provider.calls[0][3])
        self.assertEqual(provider.calls[0][4], {"type": "json_object"})
        self.assertEqual(
            provider.calls[0][5], {"thinking": {"type": "disabled"}}
        )
        self.assertEqual(
            [message["role"] for message in provider.calls[0][6]],
            ["system", "user"],
        )
        self.assertEqual(provider.calls[0][7], 1.3)
        self.assertIn("Be concise.", provider.calls[0][6][0]["content"])
        self.assertIn("Return JSON only:", provider.calls[0][6][0]["content"])
        self.assertEqual(len(turn.llm_outputs), 1)
        self.assertEqual(
            turn.llm_outputs[0]["content"],
            '{"message":"Could you help me compare a few options?",'
            '"status":"continue",'
            '"remaining_requirement":"expected_result"}',
        )

    def test_legacy_provider_receives_flattened_native_history(self):
        provider = LegacyStubProvider()
        simulator = UserSimulator(
            provider=provider,
            profile={"age": 30},
            user_task=USER_TASK,
            behavior_guidelines="Be concise.",
        )

        turn = asyncio.run(simulator.next_turn([]))

        self.assertEqual(turn.status, "continue")
        self.assertEqual(turn.usage["total_tokens"], 30)
        self.assertIn("<PROFILE>", provider.calls[0][0])
        self.assertIn("<CHAT_HISTORY>", provider.calls[0][1])
        self.assertTrue(provider.calls[0][3])

    def test_simulator_retries_invalid_json_and_accumulates_usage(self):
        provider = StubProvider(
            [
                "I would ask another question.",
                '{"message":"Which model is available?","status":"continue",'
                '"remaining_requirement":"expected_result"}',
            ]
        )
        simulator = UserSimulator(
            provider=provider,
            profile={"age": 30},
            user_task=USER_TASK,
            behavior_guidelines="Be concise.",
            max_format_attempts=3,
        )

        turn = asyncio.run(simulator.next_turn([]))

        self.assertEqual(turn.format_attempts, 2)
        self.assertEqual(len(turn.format_failures), 1)
        self.assertEqual(
            [item["content"] for item in turn.llm_outputs],
            [
                "I would ask another question.",
                '{"message":"Which model is available?","status":"continue",'
                '"remaining_requirement":"expected_result"}',
            ],
        )
        self.assertEqual(turn.usage["total_tokens"], 84)
        retry_messages = provider.calls[1][6]
        self.assertEqual(retry_messages[-2]["role"], "assistant")
        self.assertEqual(retry_messages[-2]["content"], "I would ask another question.")
        self.assertEqual(retry_messages[-1]["role"], "user")
        self.assertIn(
            "previous response could not be parsed",
            retry_messages[-1]["content"],
        )
        self.assertIn("remaining_requirement", retry_messages[-1]["content"])

    def test_simulator_error_retains_failed_attempts_and_usage(self):
        provider = StubProvider(["bad one", "bad two", "bad three"])
        simulator = UserSimulator(
            provider=provider,
            profile={"age": 30},
            user_task=USER_TASK,
            behavior_guidelines="Be concise.",
            max_format_attempts=3,
        )

        with self.assertRaises(SimulatorOutputError) as captured:
            asyncio.run(simulator.next_turn([]))

        self.assertEqual(len(captured.exception.raw_outputs), 3)
        self.assertEqual(len(captured.exception.llm_outputs), 3)
        self.assertEqual(captured.exception.usage["total_tokens"], 126)


if __name__ == "__main__":
    unittest.main()
