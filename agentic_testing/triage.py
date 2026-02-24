#!/usr/bin/env python3
"""
Triage module for analyzing survived mutants.

This module provides functions to:
- Parse mutmut results
- Extract mutation diffs and context
- Call LLM to decide if a test should be written
- Build prompts for test generation

Extracted from mutation_testing/analyze_survived_mutants.py to make
agentic_testing/ standalone and portable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .llm_client import call_llm

# LLM Configuration
LLM_MODEL = "gpt-4o-mini"
LLM_TEMPERATURE = 0.2


def parse_survived_mutants(results_path: Path) -> list[str]:
    """Extract mutant IDs from mutmut results file (lines ending with ': survived')."""
    if not results_path.exists():
        return []
    mutant_ids = []
    for line in results_path.read_text().splitlines():
        line = line.strip()
        if line.endswith(": survived"):
            mutant_id = line[: line.rindex(": survived")].strip()
            mutant_ids.append(mutant_id)
    return mutant_ids


def mangled_name_from_mutant_id(mutant_id: str) -> str:
    """e.g. cat_finder.x_add_to_url__mutmut_3 -> cat_finder.x_add_to_url."""
    if "__mutmut_" in mutant_id:
        return mutant_id.partition("__mutmut_")[0]
    return mutant_id


def run_mutmut_show(mutant_id: str, package_dir: Path, mutmut_bin: str | None) -> str:
    """Run 'mutmut show <mutant_id>' and return stdout+stderr."""
    cmd = [mutmut_bin or "mutmut", "show", mutant_id]
    try:
        result = subprocess.run(
            cmd,
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        return f"(mutmut show failed: {e})"


def parse_diff_for_file_and_line(diff_output: str) -> tuple[str | None, int | None]:
    """
    From mutmut show output, get source file path and approximate line number.
    Returns (relative_path, 1-based_line) e.g. ('cat_finder.py', 77).
    """
    m = re.search(r"^---\s+(\S+)\s*$", diff_output, re.MULTILINE)
    if not m:
        return None, None
    path = m.group(1).strip()

    # Get line number from diff
    m3 = re.search(r"^@@\s+-(\d+)", diff_output, re.MULTILINE)
    line = int(m3.group(1)) if m3 else 1

    # Normalize path: mutmut sometimes uses dotted module paths
    if "/" not in path and not re.search(r"\.\w{1,4}$", path):
        path = path.replace(".", "/") + ".py"

    return path, line


def get_source_snippet(source_path: Path, around_line: int, context_lines: int = 15) -> str:
    """Read source file and return lines around the given 1-based line."""
    if not source_path.exists() or around_line is None:
        return ""
    lines = source_path.read_text().splitlines()
    start = max(0, around_line - 1 - context_lines)
    end = min(len(lines), around_line + context_lines)
    snippet = "\n".join(f"{i+1:4d}| {lines[i]}" for i in range(start, end))
    return snippet


def get_tests_for_mutant(mangled_name: str, stats: dict) -> list[str]:
    """Return list of test node IDs that run for this mutant."""
    by_name = stats.get("tests_by_mangled_function_name") or {}
    return list(by_name.get(mangled_name) or [])


def read_test_file_contents(package_dir: Path, test_node_ids: list[str]) -> dict[str, str]:
    """Read contents of test files referenced by node IDs. Returns { test_path: content }."""
    paths = set()
    for node_id in test_node_ids:
        # test_cat_finder.py::TestClass::test_method -> test_cat_finder.py
        path = node_id.split("::")[0]
        paths.add(path)
    out = {}
    for path in paths:
        full = package_dir / path
        if full.exists():
            out[path] = full.read_text()
        else:
            out[path] = f"(file not found: {full})"
    return out


def build_llm_prompt(entry: dict) -> str:
    """Build a prompt for the LLM to decide if we should write a test for this mutant."""
    return f"""You are a software engineer reviewing mutation testing results. A "survived" mutant means the test suite did not fail when this mutation was applied. Your job is to decide if a new or stronger test is worth writing.

**Be conservative.** Most survived mutants do NOT need a new test. Only recommend writing a test if the mutation would cause a **real, observable bug** that affects correctness in production.

**CRITICAL: Carefully check WHERE the mutation occurs.** Look at the diff closely:
- If the changed line is INSIDE a print(), logging, logger, or warning call, it is a display-only change — do NOT recommend a test, even if the change involves arithmetic or variable substitution within the message string.
- If the mutation changes an argument to print/log (e.g. `print(f"...{{len(x) - 1}}")` becomes `print(f"...{{len(x) + 1}}")"`), that is still just a logging change. The arithmetic change is inside the log message, NOT in business logic.
- Only flag arithmetic or logic changes when they affect a **return value, assignment, conditional, or function argument that controls program behavior** — NOT when they only affect what gets printed.

Do NOT recommend tests for:
- ANY mutation whose changed code is an argument to print(), logging.*, logger.*, or warnings.warn()
- String formatting differences in log/warning/error messages
- Replacing a print/log call's content with None or a different string
- Cosmetic changes (variable names in error messages, f-string tweaks)
- Equivalent mutations that produce the same observable behavior
- Changes to code that only affects developer-facing output (not return values, not side effects)
- **Mutations inside main()** — main() requires pigpio, Picamera2, IMX500, camera, boto3, and complex hardware mocking that cannot be reliably unit tested. If the mutation is in main()'s setup code (pi.callback, camera config, etc.), do NOT recommend a test.

Only recommend a test if the mutation changes a **return value, a stored value (e.g. database entry), a control flow decision, or an externally visible side effect** in a way that would be wrong.

**CRITICAL: Check if existing tests already cover the mutated branch.**
- Look at the test file content provided below. If an existing test already exercises the SAME code path / condition that the mutant changes, a new test is unlikely to help — the mutant likely survives for a reason other than missing coverage (e.g., equivalent mutation).
- Only recommend a new test if you can identify a SPECIFIC input that sits on the exact boundary the mutant changes (e.g., `<` vs `<=` at threshold=10 means testing with exactly 10).
- If the mutant changes a module-level constant, check whether any test actually exercises the code path that USES that constant (not just calls the function with a different default).
- Do NOT recommend tests that would be equivalent to existing ones (same branch, same kind of input).

**Mutant ID:** {entry["mutant_id"]}

**What the mutation did (diff):**
```
{entry.get("diff", "(no diff)")}
```

**Source file:** {entry.get("source_file", "?")}
**Relevant source snippet (line numbers on the left):**
```
{entry.get("source_snippet", "(no snippet)")}
```

**Tests that run when this code is exercised (but did not fail):**
{chr(10).join('- ' + t for t in entry.get("tests_that_run", []))}

**Relevant test file(s) content (excerpts):**
{entry.get("test_files_content", "(none)")}

**Task:** Should we write or strengthen a test for this mutation?

Respond in JSON only, no markdown. If should_write_test is true, include an "agent_prompt" field with a detailed prompt that a coding agent can use to write the test. The agent prompt MUST specify:
1. Which test file and EXISTING test class (never suggest creating a new class)
2. What inputs to use
3. What to assert (the ORIGINAL code's behavior, not the mutant's)
4. Why this kills the mutant

{{"should_write_test": true or false, "reason": "one or two sentences", "suggestion": "one-line hint if should_write_test is true, otherwise empty string", "agent_prompt": "detailed agent-ready prompt if should_write_test is true, otherwise empty string"}}

Example agent_prompts (only when should_write_test is true):

Example 1 (bounds check):
"In test_cat_finder.py, add a test to TestGetLabel that calls get_label with idx=3 (exactly len(labels)) and asserts the return value is 'unknown'. This tests the correct behavior of the original code (idx >= len returns 'unknown'). The mutant changed >= to >, so it would incorrectly return labels[3] and crash. The source function is get_label in cat_finder.py."

Example 2 (comparison operator):
"In test_cat_finder.py, add a test to TestIsImageTooDark that creates an image with average brightness equal to the threshold (e.g., 30) and asserts that is_image_too_dark returns False. This tests the correct behavior of the original code (brightness < threshold). The mutant changed < to <=, so it would incorrectly return True, killing the mutant."
"""


def fetch_llm_analysis(prompt: str, api_key: str) -> dict | None:
    """Call OpenAI API (or compatible) and return parsed JSON analysis."""
    try:
        messages = [{"role": "user", "content": prompt}]
        return call_llm(messages, api_key, model=LLM_MODEL, temperature=LLM_TEMPERATURE, timeout=60)
    except Exception as e:
        return {"error": str(e), "should_write_test": None, "reason": "", "suggestion": ""}
