#!/usr/bin/env python3
"""
Shared LLM client for calling the OpenAI-compatible chat completions API.

Used by test_generator, test_fixer, and triage modules.
"""

import json
import re
import urllib.request

API_URL = "https://api.openai.com/v1/chat/completions"


def call_llm(
    messages: list[dict],
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 1,
    timeout: int = 120,
) -> dict:
    """
    Call the OpenAI chat completions API and return parsed JSON.

    Args:
        messages: Chat messages (system + user)
        api_key: OpenAI API key
        model: Model name
        temperature: Sampling temperature
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON dict from the LLM response

    Raises:
        Exception on network/parse errors (caller should handle)
    """
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    req = urllib.request.Request(
        API_URL,
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
    text = text.strip()

    # Strip markdown code blocks if present
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    return json.loads(text)
