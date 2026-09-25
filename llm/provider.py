"""LLM Provider Module.

This module handles all interactions with Azure OpenAI and other LLM services,
providing a unified interface for model communication with retry logic and
error handling.

Classes:
    LLMProvider: Universal LLM provider supporting multiple model types
"""

import asyncio
import json
import logging
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple, Set
import json_repair

logger = logging.getLogger(__name__)

MODELS_WITH_MAX_COMPLETION_TOKENS: Set[str] = {
    "o1-preview", "o1-mini", "o4-mini", "o3-mini", "o3", 
    "gpt-4o", "gpt-4o-mini", "gpt-5"
}


class LLMProvider:
    """Universal LLM provider supporting multiple model types and providers.
    
    This class provides a unified interface for interacting with various LLM
    providers (Azure OpenAI, OpenAI, etc.) with built-in retry logic, error
    handling, and token management.
    
    Attributes:
        client: The LLM client instance (e.g., AsyncAzureOpenAI)
        deployment_name: Name of the model deployment
        provider_type: Type of provider ('azure', 'openai', etc.)
        
    Example:
        >>> from openai import AsyncAzureOpenAI
        >>> client = AsyncAzureOpenAI(...)
        >>> provider = LLMProvider(client, "gpt-4o", "azure")
        >>> response = await provider.get_completion("You are helpful", "Hello", 100)
    """
    
    def __init__(
        self, 
        client: Any, 
        deployment_name: str, 
        provider_type: str = "azure"
    ) -> None:
        """Initialize the LLM provider.
        
        Args:
            client: The LLM client instance for API calls
            deployment_name: Name of the model deployment to use
            provider_type: Type of provider, defaults to 'azure'
        """
        self.client = client
        self.deployment_name: str = deployment_name
        self.provider_type: str = provider_type

    def _is_token_limit_error(self, error_message: str) -> bool:
        """Check if the error is related to token limits.
        
        Args:
            error_message: Error message to analyze
            
        Returns:
            True if the error is token limit related, False otherwise
        """
        error_lower = str(error_message).lower()
        token_limit_indicators = [
            "maximum context length",
            "context length",
            "token limit",
            "too many tokens",
            "exceeds maximum",
            "requested too many tokens"
        ]
        return any(indicator in error_lower for indicator in token_limit_indicators)
    
    def _is_content_filter_error(self, error_message: str) -> bool:
        """Check if the error is related to Azure content filtering.
        
        Args:
            error_message: Error message to analyze
            
        Returns:
            True if the error is content filter related, False otherwise
        """
        error_lower = str(error_message).lower()
        content_filter_indicators = [
            "content management policy",
            "content filtering policies",
            "content_filter",
            "jailbreak",
            "responsibleaipolicyviolation"
        ]
        return any(indicator in error_lower for indicator in content_filter_indicators)
    
    def _extract_requested_tokens(self, error_message: str) -> Tuple[Optional[int], Optional[int]]:
        """Extract requested and max tokens from error message.
        
        Args:
            error_message: Error message containing token information
            
        Returns:
            Tuple of (requested_tokens, max_allowed_tokens), either can be None
        """
        import re
        
        # Pattern to match: "you requested X tokens ... maximum context length is Y tokens"
        pattern = r"you requested (\d+) tokens.*maximum context length is (\d+) tokens"
        match = re.search(pattern, str(error_message), re.IGNORECASE)
        
        if match:
            requested = int(match.group(1))
            max_allowed = int(match.group(2))
            return requested, max_allowed
        
        # Alternative pattern: "X tokens in the messages, Y in the completion"
        pattern2 = r"(\d+) tokens.*in the messages.*(\d+) in the completion"
        match2 = re.search(pattern2, str(error_message), re.IGNORECASE)
        
        if match2:
            message_tokens = int(match2.group(1))
            completion_tokens = int(match2.group(2))
            return message_tokens + completion_tokens, None
            
        return None, None

    async def get_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int],
        return_usage: bool = False,
        temperature: Optional[float] = None,
        response_format: Optional[dict[str, Any]] = None,
        extra_body: Optional[dict[str, Any]] = None,
        response_observer: Optional[Callable[[dict[str, Any]], None]] = None,
        messages: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> Any:
        """Get a completion from the LLM with retry mechanism.
        
        Args:
            system_prompt: System message to set context
            user_prompt: User's input prompt
            max_tokens: Maximum output tokens, or None to let the API/model
                choose its default.
            temperature: Optional sampling temperature. Omitted when None.
            return_usage: If True, returns tuple of (content, usage_dict)
            response_format: Optional OpenAI-compatible structured-output format.
            extra_body: Optional provider-specific request parameters.
            response_observer: Optional callback receiving response audit data
                for every provider attempt, including raw empty content.
            messages: Optional native chat messages. When supplied, these replace
                the system_prompt/user_prompt pair.
            
        Returns:
            The LLM's response as a string, or tuple of (content, usage_dict) if return_usage=True
            
        Raises:
            Exception: If all retry attempts fail
        """
        
        request_messages = (
            [dict(message) for message in messages]
            if messages is not None
            else [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        params = {
            "model": self.deployment_name,
            "messages": request_messages,
        }
        
        # Handle different token parameter names
        if max_tokens is not None and self.provider_type == "azure":
            if self.deployment_name in MODELS_WITH_MAX_COMPLETION_TOKENS:
                params["max_completion_tokens"] = max_tokens
            else:
                params["max_tokens"] = max_tokens
        elif max_tokens is not None:
            params["max_tokens"] = max_tokens
        if temperature is not None:
            params["temperature"] = temperature
        if response_format:
            params["response_format"] = response_format
        if extra_body:
            params["extra_body"] = extra_body
        
        # Simple retry mechanism: 3 attempts with exponential backoff
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                logger.info(f"Generating completion using {self.deployment_name} (attempt {attempt + 1}/{max_attempts}, max_tokens: {max_tokens})")
                
                response = await self.client.chat.completions.create(**params)
                choice = response.choices[0]
                message = choice.message
                content = message.content

                if response_observer:
                    usage = getattr(response, "usage", None)
                    reasoning_content = getattr(message, "reasoning_content", None)
                    response_observer(
                        {
                            "provider_attempt": attempt + 1,
                            "response_id": getattr(response, "id", None),
                            "finish_reason": getattr(choice, "finish_reason", None),
                            "content": "" if content is None else str(content),
                            "reasoning_content_length": len(reasoning_content or ""),
                            "usage": {
                                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                                "completion_tokens": getattr(usage, "completion_tokens", 0),
                                "total_tokens": getattr(usage, "total_tokens", 0),
                            } if usage else {},
                        }
                    )
                
                if content is None or content.strip() == "":
                    raise ValueError("Empty content received from LLM")
                
                if attempt > 0:
                    logger.info(f"Success on attempt {attempt + 1} for {self.deployment_name}")
                
                if return_usage and hasattr(response, 'usage') and response.usage:
                    usage_dict = {
                        'prompt_tokens': getattr(response.usage, 'prompt_tokens', 0),
                        'completion_tokens': getattr(response.usage, 'completion_tokens', 0),
                        'total_tokens': getattr(response.usage, 'total_tokens', 0)
                    }
                    return content.strip(), usage_dict
                
                return content.strip()
                
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Attempt {attempt + 1}/{max_attempts} failed for {self.deployment_name}: {e}")

                # Some OpenAI-compatible endpoints do not implement
                # response_format. Fall back to prompt-enforced JSON instead
                # of failing an otherwise usable simulator call.
                if attempt < max_attempts - 1 and "response_format" in params and any(
                    marker in error_msg.lower()
                    for marker in ("response_format", "response format", "json_object")
                ):
                    params.pop("response_format", None)
                    logger.info(
                        "Endpoint does not support structured output; retrying without response_format"
                    )
                    continue
                
                # Check for content filter errors - fail fast, no retries
                if self._is_content_filter_error(error_msg):
                    logger.info(f"Content filter error detected for {self.deployment_name}, failing fast")
                    raise e
                
                # For other errors, wait before retry (except last attempt)
                if attempt < max_attempts - 1:
                    wait_time = 2 ** attempt  # 1, 2 seconds
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    await asyncio.sleep(wait_time)
                else:
                    # Last attempt failed
                    raise e
        
        # This should never be reached due to the raise in the last attempt
        raise Exception("Unexpected completion flow")

    def clean_and_parse_json(self, raw_json: str) -> Any:
        """Clean and parse JSON response with enhanced error handling."""
        try:
            # Remove markdown code blocks if present
            if '```json' in raw_json:
                raw_json = raw_json.split('```json')[1].split('```')[0].strip()
            elif '```' in raw_json:
                # Handle cases where it's just ```
                parts = raw_json.split('```')
                if len(parts) >= 2:
                    raw_json = parts[1].strip()
            
            # Clean up common formatting issues
            raw_json = raw_json.strip()
            if not raw_json.startswith('{') and not raw_json.startswith('['):
                # Find the first { or [
                first_brace = raw_json.find('{')
                first_bracket = raw_json.find('[')
                
                if first_brace == -1 and first_bracket == -1:
                    logger.error(f"No JSON object or array found in the raw response: {raw_json}")
                    raise ValueError(f"No JSON object or array found in LLM response: {raw_json[:500]}")
                
                start_idx = -1
                if first_brace != -1 and first_bracket != -1:
                    start_idx = min(first_brace, first_bracket)
                elif first_brace != -1:
                    start_idx = first_brace
                else:
                    start_idx = first_bracket
                    
                if start_idx != -1:
                    raw_json = raw_json[start_idx:]
            
            # Try standard JSON parsing first
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError:
                # Fall back to json_repair for malformed JSON
                return json_repair.loads(raw_json)
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
            logger.error(f"Raw response: {raw_json[:500]}...")
            raise ValueError(f"Failed to parse JSON from LLM response: {e}. Raw response: {raw_json[:500]}")
        except Exception as e:
            logger.error(f"Unexpected error parsing JSON: {e}")
            raise ValueError(f"Unexpected error parsing JSON from LLM response: {e}. Raw response: {raw_json[:500] if 'raw_json' in locals() else 'N/A'}")
