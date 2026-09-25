import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from llm.provider import LLMProvider  # noqa: E402


def make_response(content, *, response_id, finish_reason, reasoning_content=""):
    return SimpleNamespace(
        id=response_id,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning_content,
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )


class LLMProviderTests(unittest.TestCase):
    def test_optional_max_tokens_is_omitted_and_temperature_is_forwarded(self):
        create = AsyncMock(
            return_value=make_response(
                "final answer",
                response_id="unbounded-response",
                finish_reason="stop",
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        provider = LLMProvider(
            client=client,
            deployment_name="test-model",
            provider_type="openai_compatible",
        )

        asyncio.run(
            provider.get_completion(
                "system",
                "user",
                None,
                temperature=1.0,
            )
        )

        kwargs = create.await_args.kwargs
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertEqual(kwargs["temperature"], 1.0)

    def test_native_messages_replace_system_user_pair(self):
        create = AsyncMock(
            return_value=make_response(
                '{"message":"Hello","status":"continue"}',
                response_id="native-response",
                finish_reason="stop",
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        provider = LLMProvider(
            client=client,
            deployment_name="test-model",
            provider_type="openai_compatible",
        )
        messages = [
            {"role": "system", "content": "private simulator context"},
            {"role": "user", "content": "begin"},
            {
                "role": "assistant",
                "content": '{"message":"Need help","status":"continue"}',
            },
            {"role": "user", "content": "What do you need?"},
        ]

        asyncio.run(
            provider.get_completion(
                "unused system",
                "unused user",
                1000,
                messages=messages,
            )
        )

        self.assertEqual(create.await_args.kwargs["messages"], messages)

    def test_observer_records_empty_provider_attempt_before_success(self):
        create = AsyncMock(
            side_effect=[
                make_response(
                    None,
                    response_id="empty-response",
                    finish_reason="length",
                    reasoning_content="internal reasoning",
                ),
                make_response(
                    '{"message":"Done","status":"satisfied"}',
                    response_id="successful-response",
                    finish_reason="stop",
                ),
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        provider = LLMProvider(
            client=client,
            deployment_name="deepseek-v4-flash",
            provider_type="openai_compatible",
        )
        observed = []

        with patch("llm.provider.asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(
                provider.get_completion(
                    "system",
                    "user",
                    1000,
                    return_usage=True,
                    response_format={"type": "json_object"},
                    extra_body={"thinking": {"type": "disabled"}},
                    response_observer=observed.append,
                )
            )

        self.assertEqual(result[0], '{"message":"Done","status":"satisfied"}')
        self.assertEqual([item["content"] for item in observed], ["", result[0]])
        self.assertEqual(observed[0]["finish_reason"], "length")
        self.assertEqual(observed[0]["reasoning_content_length"], 18)
        self.assertEqual(
            create.await_args_list[0].kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )


if __name__ == "__main__":
    unittest.main()
