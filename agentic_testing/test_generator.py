#!/usr/bin/env python3
"""
Generate test code using LLM based on agent_prompt from triage analysis.
"""

import json
import os
import urllib.request
from pathlib import Path


LLM_MODEL = "gpt-4o-mini"
LLM_TEMPERATURE = 0.2
LLM_API_URL = "https://api.openai.com/v1/chat/completions"


def generate_test_code(
    agent_prompt: str,
    test_file_path: Path,
    existing_test_content: str,
    api_key: str,
    source_snippet: str = "",
) -> dict:
    """
    Generate test code using LLM based on the agent_prompt from triage.

    Args:
        agent_prompt: Detailed prompt from triage analysis
        test_file_path: Path to the test file to modify
        existing_test_content: Current content of the test file
        api_key: OpenAI API key

    Returns:
        dict with:
            - success: bool
            - test_code: str (the generated test method code)
            - test_class: str (which test class to add it to)
            - test_method_name: str (name of the new test method)
            - explanation: str
            - error: str (if success is False)
    """
    system_prompt = """You are an expert Python test engineer. Your job is to write unit tests that kill specific mutants.

**CRITICAL: What "killing a mutant" means:**
A test "kills" a mutant if it:
1. PASSES when run against the ORIGINAL (correct) code
2. FAILS when run against the MUTATED (incorrect) code

Your test should assert the CORRECT behavior (what the original code does), NOT what the mutant does.

**Example:**
- Original code: `return x < 10`
- Mutant code: `return x <= 10`
- To kill this mutant, test with x=10:
  - Assert False (because 10 < 10 is False in original)
  - This passes with original (10 < 10 = False -- PASS)
  - This fails with mutant (10 <= 10 = True -- FAIL) => MUTANT KILLED

Given:
1. A description of what mutation survived
2. The existing test file content
3. Instructions on what test to write

Generate ONLY the new test method code (not the entire class or file).

Output JSON format:
{
  "test_class": "TestClassName",
  "test_method_name": "test_specific_edge_case",
  "test_code": "    def test_specific_edge_case(self):\\n        ...",
  "explanation": "Brief explanation of what this test verifies"
}

Requirements:
- Use proper indentation (4 spaces for method body, 8 spaces for method contents)
- Include docstring explaining what the test does
- Use appropriate assertions from unittest (assertEqual, assertTrue, assertFalse, etc.)
- Follow the style of existing tests
- Make the test as minimal as possible - only test the specific mutation
- **DO NOT include:**
  - Class definitions
  - Import statements (all imports are already at the top of the test file)
  - Only the method definition with decorators (if needed)
- **ALWAYS assert the ORIGINAL code's behavior, not the mutant's behavior**
- **ALWAYS use an EXISTING test class from the test file - NEVER create a new class**
- If the agent_prompt suggests a new class, choose the most appropriate existing class instead

**COMMON MOCK PATTERNS - follow these exactly:**

1. **Accessing call_args from mock:**
   - `call_args[1]` gives keyword arguments, `call_args[0]` gives positional arguments
   - Example: `table.put_item.call_args[1]['Item']` gets the Item kwarg from put_item(Item={...})
   - NEVER do `call_args[0]` then index with string keys - that's positional args, not kwargs

2. **Patching functions correctly:**
   - When patching, patch where the function is USED, not where it's defined
   - If you patch 'cat_finder.process_detection', calling process_detection() directly still calls the REAL function (you imported it). The mock only intercepts calls made through the cat_finder module namespace.
   - To test a function's behavior, call it directly with mocked dependencies - don't patch the function itself

3. **Testing process_detection - follow this pattern from the existing tests:**
```python
def test_example(self):
    self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
    self.imx500.get_outputs.return_value = [np.array([[0.05, 0.90, 0.05]])]

    result = process_detection(
        self.request, self.imx500, self.intrinsics,
        self.data_table, self.url_table, self.s3_client,
        self.labels, s3_bucket="bucket",
        previous_label="checo", darkness_threshold=30
    )
    self.assertEqual(result, "checo")
    self.data_table.put_item.assert_called_once()
```

4. **Testing add_to_data_dynamodb:**
   - Signature: `add_to_data_dynamodb(dynamodb_table, timestamp, image_name, cat_label, cat_confidence)`
   - These are positional args, not kwargs. Assert on the Item dict in put_item.

5. **Testing button_pressed:**
   - Signature: `button_pressed(button_pressed_flag, button_press_lock, gpio, level, tick)`
   - flag is a list like [False], lock is a threading.Lock()
   - Test by passing real flag/lock and asserting flag[0] after the call
"""

    source_context = ""
    if source_snippet:
        source_context = f"""
**Source code being tested (for reference):**
```python
{source_snippet}
```
"""

    user_prompt = f"""**Agent prompt (what to test):**
{agent_prompt}

**Test file to modify:** {test_file_path}
{source_context}
**Existing test file content:**
```python
{existing_test_content}
```

Generate a new test method that kills this mutant. Output JSON only.
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
            text = text.split("\n", 1)[1]  # Remove first line
            text = text.rsplit("```", 1)[0]  # Remove last line
            text = text.strip()

        result = json.loads(text)
        result["success"] = True
        return result

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "test_code": "",
            "test_class": "",
            "test_method_name": "",
            "explanation": "",
        }


if __name__ == "__main__":
    # Example usage
    example_agent_prompt = """In test_cat_finder.py, add a test to TestGetLabel that calls get_label with idx=3 (exactly len(labels)) and asserts the return value is 'unknown'. This kills the mutant that changes >= to > in the bounds check. The source function is get_label in cat_finder.py."""

    example_existing_content = """import unittest

class TestGetLabel(unittest.TestCase):
    def setUp(self):
        self.labels = ["neither", "checo", "tuni"]

    def test_valid_index(self):
        self.assertEqual(get_label(self.labels, 0), "neither")
"""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set")
        exit(1)

    result = generate_test_code(
        example_agent_prompt,
        Path("test_cat_finder.py"),
        example_existing_content,
        api_key,
    )

    print(json.dumps(result, indent=2))
