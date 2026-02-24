#!/usr/bin/env python3
"""
Shared LLM client with JSON retry logic.

Handles:
- OpenAI API calls
- Markdown code block stripping
- JSON parse retries (re-prompts LLM on parse failure)
"""

import json
import re
import urllib.request


LLM_API_URL = "https://api.openai.com/v1/chat/completions"


def _strip_markdown(text: str) -> str:
    """Strip markdown code fences from LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0] if "```" in text else text
        text = text.strip()
    return text


def call_llm_json(
    messages: list[dict],
    api_key: str,
    model: str,
    temperature: float = 1,
    timeout: int = 120,
    max_retries: int = 1,
) -> dict:
    """
    Call LLM and parse JSON response, with retry on parse failure.

    Args:
        messages: Chat messages (system + user)
        api_key: OpenAI API key
        model: Model name
        temperature: Sampling temperature
        timeout: Request timeout in seconds
        max_retries: Number of retries on JSON parse failure (default 1)

    Returns:
        Parsed JSON dict from LLM response.

    Raises:
        Exception on API or final parse failure.
    """
    last_error = None
    last_raw_text = None

    for attempt in range(1 + max_retries):
        if attempt > 0:
            # Retry: append the error feedback as a user message
            retry_messages = messages + [
                {
                    "role": "assistant",
                    "content": last_raw_text or "",
                },
                {
                    "role": "user",
                    "content": (
                        f"Your previous response was not valid JSON: {last_error}\n"
                        "Please respond with ONLY valid JSON, no markdown, no explanation. "
                        "Just the JSON object."
                    ),
                },
            ]
        else:
            retry_messages = messages

        body = {
            "model": model,
            "messages": retry_messages,
            "temperature": temperature,
        }

        req = urllib.request.Request(
            LLM_API_URL,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        text = _strip_markdown(text)
        last_raw_text = text

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            last_error = str(e)
            if attempt == max_retries:
                raise

    # Should not reach here, but just in case
    raise json.JSONDecodeError(f"Failed after {max_retries + 1} attempts", "", 0)
