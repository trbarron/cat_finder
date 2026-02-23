#!/usr/bin/env python3
"""
Fix failed tests using LLM based on error messages.
"""

import json
import urllib.request
from pathlib import Path


LLM_MODEL = "gpt-4o-mini"
LLM_TEMPERATURE = 0.3  # Slightly higher for creative problem-solving
LLM_API_URL = "https://api.openai.com/v1/chat/completions"


def fix_failed_test(
    failed_test_code: str,
    error_message: str,
    test_class: str,
    test_method_name: str,
    source_snippet: str,
    agent_prompt: str,
    api_key: str,
) -> dict:
    """
    Fix a failed test using LLM based on the error message.

    Args:
        failed_test_code: The test code that failed
        error_message: The pytest error message
        test_class: Name of the test class
        test_method_name: Name of the test method
        source_snippet: Source code being tested
        agent_prompt: Original prompt for context
        api_key: OpenAI API key

    Returns:
        dict with:
            - success: bool
            - test_code: str (fixed test code)
            - explanation: str (what was fixed)
            - error: str (if success is False)
    """
    system_prompt = """You are an expert Python test engineer specializing in fixing broken tests.

Your job is to analyze a failed test and fix it based on the error message.

Common issues to fix:
1. **Incorrect UUID mocking**: Use proper UUID objects, not strings
   - ❌ mock_uuid.uuid4.return_value = 'fixed-uuid'
   - ✅ from uuid import UUID; mock_uuid.uuid4.return_value = UUID('12345678-1234-5678-1234-567812345678')

2. **Incorrect datetime mocking**: Use proper datetime objects
   - ❌ mock_dt.now.return_value.strftime.return_value = '2020-01-02_03-04-05'
   - ✅ from datetime import datetime; mock_dt.now.return_value = datetime(2020, 1, 2, 3, 4, 5)

3. **Wrong regex patterns**: Match actual output format
   - Check if pattern expects hyphens in UUID but mock returns plain string

4. **Wrong dict keys**: Check actual function implementation
   - Use correct Item keys from DynamoDB put_item calls

5. **Incorrect assertions**: Match actual behavior

Output JSON format:
{
  "test_code": "    def test_method_name(self):\\n        ...",
  "explanation": "Fixed UUID mocking to use proper UUID object instead of string",
  "changes": "Changed mock_uuid.uuid4.return_value from 'fixed-uuid' to UUID object"
}

IMPORTANT: Output ONLY the fixed test method code (with proper indentation), not the entire class.
"""

    user_prompt = f"""**Test that failed:**
```python
{failed_test_code}
```

**Error message:**
```
{error_message}
```

**Original requirement (for context):**
{agent_prompt}

**Source code being tested:**
```python
{source_snippet}
```

**Task**: Fix this test so it passes. Analyze the error and make the necessary corrections.

Common fixes needed:
- If error mentions UUID format: use `from uuid import UUID` and `UUID('12345678-1234-5678-1234-567812345678')`
- If mocking datetime: use proper datetime objects, not just strings
- If regex doesn't match: adjust pattern or fix the mock

Output JSON only with the fixed test code.
"""

    try:
        body = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": LLM_TEMPERATURE,
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

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        text = text.strip()

        # Strip markdown code blocks if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0] if "```" in text else text
            text = text.strip()

        result = json.loads(text)
        result["success"] = True
        return result

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "test_code": "",
            "explanation": "",
            "changes": "",
        }


if __name__ == "__main__":
    # Example usage
    import os

    failed_code = """    def test_timestamp_format_in_url(self):
        mock_table = MagicMock()
        expected_uuid = 'fixed-uuid'
        with patch('cat_finder.uuid') as mock_uuid:
            mock_uuid.uuid4.return_value = expected_uuid
            add_to_url_dynamodb(mock_table, 's3://bucket/object')
            url = mock_table.put_item.call_args[1]['Item']['URL']
            pattern = r'\\d{4}-\\d{2}-\\d{2}_\\d{2}-\\d{2}-\\d{2}_\\w{8}-\\w{4}-\\w{4}-\\w{4}-\\w{12}'
            self.assertRegex(url, pattern)"""

    error_msg = """AssertionError: Regex didn't match: '\\d{4}-\\d{2}-\\d{2}_\\d{2}-\\d{2}-\\d{2}_\\w{8}-\\w{4}-\\w{4}-\\w{4}-\\w{12}' not found in '2020-01-02_03-04-05_fixed-uuid'"""

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        result = fix_failed_test(
            failed_code,
            error_msg,
            "TestAddToUrlDynamodb",
            "test_timestamp_format_in_url",
            "def add_to_url_dynamodb(table, url): ...",
            "Test timestamp format",
            api_key,
        )
        print(json.dumps(result, indent=2))
