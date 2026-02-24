#!/usr/bin/env python3
"""
Generate test code using LLM based on agent_prompt from triage analysis.
"""

import json
import os
from pathlib import Path

from .llm_client import call_llm_json


LLM_MODEL = "gpt-5-mini"
LLM_TEMPERATURE = 1  # gpt-5-mini only supports temperature=1


def generate_test_code(
    agent_prompt: str,
    test_file_path: Path,
    existing_test_content: str,
    api_key: str,
    source_snippet: str = "",
    full_source: str = "",
    mutation_diff: str = "",
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

6. **Import rules inside test methods:**
   - Stdlib imports (threading, uuid, datetime, etc.) ARE allowed inside test methods.
   - `import cat_finder` (bare import) is allowed inside test methods -- use `cat_finder.main()`, `cat_finder.DARKNESS_THRESHOLD`, etc.
   - `from cat_finder import ...` is STRICTLY FORBIDDEN inside test methods. This will cause an immediate rejection.
   - If you need main(), use: `import cat_finder` then `cat_finder.main()` -- NEVER `from cat_finder import main`.

7. **NEVER use source inspection as a test strategy:**
   - NEVER use `inspect.getsource()` to check source code text
   - NEVER assert that a specific string exists in the source code
   - Tests must verify BEHAVIOR (call the function, check the result/side effects), not source text
   - Source inspection tests are fragile, don't test real behavior, and will be rejected

8. **Module-level constants vs function parameter defaults:**
   - If a mutant changes a module-level constant (e.g., `DARKNESS_THRESHOLD = 69`), your test must exercise the code path that USES that constant (e.g., `main()` or `process_detection()` which passes it explicitly)
   - Do NOT call a function without arguments and assume it uses the module constant -- check the function signature for its actual default value
   - Example: `is_image_too_dark(request)` uses default=30 from the function signature, NOT the module-level `DARKNESS_THRESHOLD=69`

9. **Don't duplicate existing test coverage:**
   - Before writing a new test, carefully review the existing tests provided below
   - If an existing test already exercises the SAME code branch / condition that the mutant changes, your new test must use a DIFFERENT input or assertion strategy that specifically distinguishes original from mutant behavior
   - Ask yourself: "Would the existing test already fail if this mutant were applied?" If yes, the mutant is likely already killed and a new test won't help
   - Example: If existing tests already cover `not os.getenv(var)` with missing env vars (None), adding a test with empty strings ('') doesn't help -- both are falsy and hit the same branch
   - Focus on the EXACT boundary the mutant changes (e.g., `<` vs `<=`, `>=` vs `>`) and pick an input that sits exactly on that boundary

10. **NEVER test main() -- test helper functions directly instead:**
    - NEVER write tests that call main(). main() requires FakePi, pigpio, Picamera2, IMX500, camera, boto3, environment variables, and many more mocks. These tests ALWAYS fail and waste all 5 fix attempts.
    - If the mutant is in a helper function (process_detection, is_image_too_dark, button_pressed, etc.), test that function DIRECTLY
    - If the mutant is in main()'s own setup code (e.g., env var checking, pi.callback registration, camera setup), the mutation is NOT testable with a simple unit test. Return a test that calls the specific helper function with the affected parameter instead.
    - Example: If the mutant changes `darkness_threshold` passed to `process_detection()` inside main(), test `process_detection()` directly with the original threshold value.
    - Example: If the mutant changes `pi.callback(BUTTON_PIN, ...)` in main(), test `button_pressed()` directly instead -- you cannot reliably mock the entire pigpio/camera setup.

11. **Don't assert on print/log output to verify behavior:**
    - NEVER use `mock_print.assert_any_call("some message")` as your primary assertion
    - Print messages are cosmetic and may change -- assert on return values, mock call counts, or side effects instead
    - Example: Instead of asserting a print message, assert that `process_detection` was called with `is_button_triggered=True`

12. **Know the function signatures -- don't guess kwargs:**
    - Check the ACTUAL function signature in the source code before writing assertions on call_args
    - process_detection does NOT take an `Item` kwarg -- that's a DynamoDB pattern
    - If unsure, assert on positional args (`call_args[0]`) or use `assert_called_with()` with the correct signature
"""

    # Format mutation diff as clear before/after
    diff_context = ""
    if mutation_diff:
        before_lines = []
        after_lines = []
        for line in mutation_diff.splitlines():
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue
            if line.startswith("-") and not line.startswith("---"):
                before_lines.append(line[1:].strip())
            elif line.startswith("+") and not line.startswith("+++"):
                after_lines.append(line[1:].strip())

        if before_lines or after_lines:
            diff_context = f"""
**EXACT MUTATION (this is what your test must distinguish):**
- ORIGINAL code (correct): `{' | '.join(before_lines)}`
- MUTANT code (incorrect):  `{' | '.join(after_lines)}`

Your test MUST pick an input where the original code produces a DIFFERENT result than the mutant code.

**Full diff:**
```diff
{mutation_diff}
```
"""

    source_context = ""
    if full_source:
        source_context = f"""
**Full source file (cat_finder.py):**
```python
{full_source}
```
"""
    elif source_snippet:
        source_context = f"""
**Source code around the mutation:**
```python
{source_snippet}
```
"""

    # Extract imports section from test file to highlight what's available
    import_lines = []
    for line in existing_test_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            import_lines.append(stripped)
        elif stripped.startswith("class ") and import_lines:
            break  # stop at first class definition

    imports_note = "\n".join(f"  - {imp}" for imp in import_lines)

    user_prompt = f"""{diff_context}
**Agent prompt (what to test):**
{agent_prompt}

**Test file to modify:** {test_file_path}
{source_context}
**Already imported in the test file (DO NOT re-import these):**
{imports_note}

`import cat_finder` is allowed inside test methods if you need to access `cat_finder.main()` or other module-level attributes. `from cat_finder import ...` is NOT allowed inside test methods.

**Existing test file content:**
```python
{existing_test_content}
```

Generate a new test method that kills this mutant. Output JSON only.
"""

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        result = call_llm_json(
            messages, api_key, LLM_MODEL,
            temperature=LLM_TEMPERATURE, timeout=120, max_retries=1,
        )
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
