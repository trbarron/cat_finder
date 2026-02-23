#!/usr/bin/env python3
"""
Analyze survived mutants from mutmut results: gather context (diff, source, tests)
and optionally call an LLM to decide if we should write a test for each.

Output: Markdown report with one section per survived mutant, including:
  - mutant_id, diff, tests that run, test file contents
  - LLM analysis (when --call-llm is used)

Usage:
  python scripts/analyze_survived_mutants.py

  # Limit to N mutants (for testing)
  python scripts/analyze_survived_mutants.py --limit 5

  # Call LLM (requires OPENAI_API_KEY) and append analysis to output
  python scripts/analyze_survived_mutants.py --call-llm
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the package directory (parent of this script's directory)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# LLM Configuration
LLM_MODEL = "gpt-4o-mini"  # Using GPT-4o mini for cost-effectiveness
LLM_TEMPERATURE = 1
LLM_API_URL = "https://api.openai.com/v1/chat/completions"


def find_package_dir() -> Path:
    """Script is in packages/moz-artery-tree/scripts/; package dir is parent of scripts."""
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent


def parse_survived_mutants(results_path: Path) -> list[str]:
    """Extract mutant IDs from mutmut results file (lines ending with ': survived')."""
    if not results_path.exists():
        return []
    mutant_ids = []
    for line in results_path.read_text().splitlines():
        line = line.strip()
        if line.endswith(": survived"):
            # "    moz_artery_tree.core.x_prepare_info__mutmut_3: survived" -> mutant_id
            mutant_id = line[: line.rindex(": survived")].strip()
            mutant_ids.append(mutant_id)
    return mutant_ids


def mangled_name_from_mutant_id(mutant_id: str) -> str:
    """e.g. moz_artery_tree.core.x_prepare_info__mutmut_3 -> moz_artery_tree.core.x_prepare_info."""
    if "__mutmut_" in mutant_id:
        return mutant_id.partition("__mutmut_")[0]
    return mutant_id


def run_mutmut_show(mutant_id: str, package_dir: Path, mutmut_bin: str | None) -> str:
    """Run 'mutmut show <mutant_id>' and return stdout+stderr."""
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{package_dir / 'mutants'}:{package_dir / '../../packages/moz-common'}"
    cmd = [mutmut_bin or "mutmut", "show", mutant_id]
    try:
        result = subprocess.run(
            cmd,
            cwd=package_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        return f"(mutmut show failed: {e})"


def parse_diff_for_file_and_line(diff_output: str) -> tuple[str | None, int | None]:
    """From mutmut show output, get source file path and approximate line number.
    Returns (relative_path, 1-based_line) e.g. ('moz_artery_tree/utils/data_utils.py', 77).
    """
    # --- moz_artery_tree/utils/data_utils.py  or  --- moz_artery_tree/message_processor/central_line_detection.py
    m = re.search(r"^---\s+(\S+)\s*$", diff_output, re.MULTILINE)
    if not m:
        return None, None
    path = m.group(1).strip()
    # @@ -5,7 +5,7 @@  means line 5 in the file (first number after -)
    m3 = re.search(r"^@@\s+-(\d+)", diff_output, re.MULTILINE)
    line = int(m3.group(1)) if m3 else 1
    # Normalize path: mutmut sometimes uses dotted module paths (e.g. moz_artery_tree.utils.data_utils)
    # Only convert dots to slashes if the path has no file extension (i.e. no slash and no common extension)
    if "/" not in path and not re.search(r"\.\w{1,4}$", path):
        path = path.replace(".", "/", 1)  # dotted.module.path -> dotted/module.path
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
        # tests/test_core.py::test_prepare_info -> tests/test_core.py
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
- If the mutation changes an argument to print/log (e.g. `print(f"...{{len(x) - 1}}")` → `print(f"...{{len(x) + 1}}")"`), that is still just a logging change. The arithmetic change is inside the log message, NOT in business logic.
- Only flag arithmetic or logic changes when they affect a **return value, assignment, conditional, or function argument that controls program behavior** — NOT when they only affect what gets printed.

Do NOT recommend tests for:
- ANY mutation whose changed code is an argument to print(), logging.*, logger.*, or warnings.warn()
- String formatting differences in log/warning/error messages
- Replacing a print/log call's content with None or a different string
- Cosmetic changes (variable names in error messages, f-string tweaks)
- Equivalent mutations that produce the same observable behavior
- Changes to code that only affects developer-facing output (not return values, not side effects)

Only recommend a test if the mutation changes a **return value, a stored value (e.g. database entry), a control flow decision, or an externally visible side effect** in a way that would be wrong.

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

Respond in JSON only, no markdown. If should_write_test is true, include an "agent_prompt" field with a detailed prompt that a coding agent can use to write the test. The agent prompt should reference specific file paths, the function under test, what assertion to add, and which test file to modify.

{{"should_write_test": true or false, "reason": "one or two sentences", "suggestion": "one-line hint if should_write_test is true, otherwise empty string", "agent_prompt": "detailed agent-ready prompt if should_write_test is true, otherwise empty string"}}

Example agent_prompt (only when should_write_test is true):
"In test_cat_finder.py, add a test to TestGetLabel that calls get_label with idx=3 (exactly len(labels)) and asserts the return value is 'unknown'. This kills the mutant that changes >= to > in the bounds check. The source function is get_label in cat_finder.py."
"""


def generate_report(entries: list[dict], results_path: Path) -> str:
    """Build a human-readable Markdown report from analyzed mutant entries."""
    lines: list[str] = []
    lines.append(f"# Survived Mutants Report")
    lines.append("")
    lines.append(f"- **Source:** `{results_path}`")
    lines.append(f"- **Survived mutants:** {len(entries)}")
    lines.append("")

    for entry in entries:
        mutant_id = entry.get("mutant_id", "unknown")
        lines.append(f"## Mutant: {mutant_id}")
        lines.append("")

        source_file = entry.get("source_file") or "unknown"
        diff = entry.get("diff") or "(no diff)"
        snippet = entry.get("source_snippet") or "(no snippet)"
        tests = entry.get("tests_that_run") or []
        test_content = entry.get("test_files_content") or ""

        lines.append(f"**Source file:** `{source_file}`")
        lines.append("")

        lines.append("### Diff")
        lines.append("")
        lines.append("```diff")
        lines.append(diff)
        lines.append("```")
        lines.append("")

        lines.append("### Tests that run")
        lines.append("")
        if tests:
            lines.append(f"<details><summary>{len(tests)} test(s)</summary>")
            lines.append("")
            for t in tests:
                lines.append(f"- `{t}`")
            lines.append("")
            lines.append("</details>")
        else:
            lines.append("- (none)")
        lines.append("")

        if test_content:
            lines.append("### Test file contents")
            lines.append("")
            # test_files_content is a concatenated string with "--- path ---" headers
            for block in test_content.split("--- "):
                block = block.strip()
                if not block:
                    continue
                header, _, body = block.partition(" ---\n")
                if not body:
                    body = header
                    header = "test file"
                lines.append(f"<details><summary>{header}</summary>")
                lines.append("")
                lines.append("```python")
                lines.append(body.strip())
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")

        analysis = entry.get("llm_analysis")
        if analysis and isinstance(analysis, dict):
            lines.append("### LLM Analysis")
            lines.append("")
            lines.append(f"- **should_write_test:** {analysis.get('should_write_test')}")
            lines.append(f"- **reason:** {analysis.get('reason', '')}")
            lines.append(f"- **suggestion:** {analysis.get('suggestion', '')}")
            agent_prompt = analysis.get("agent_prompt", "")
            if agent_prompt:
                lines.append("")
                lines.append("**Agent prompt:**")
                lines.append("")
                lines.append("```text")
                lines.append(agent_prompt)
                lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def fetch_llm_analysis(prompt: str, api_key: str) -> dict | None:
    """Call OpenAI API (or compatible) and return parsed JSON analysis."""
    try:
        import urllib.request

        body = {
            "model": LLM_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
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
        # Try to parse JSON from the response (might be wrapped in markdown)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        return json.loads(text)
    except Exception as e:
        return {"error": str(e), "should_write_test": None, "reason": "", "suggestion": ""}


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze survived mutants and optionally ask LLM if we should write tests.")
    parser.add_argument(
        "--results",
        default=None,
        help="Path to mutmut_results.txt (default: <package_dir>/mutmut_results.txt)",
    )
    parser.add_argument(
        "--stats",
        default=None,
        help="Path to mutmut-stats.json (default: <package_dir>/mutants/mutmut-stats.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="survived_test_report.md",
        help="Output Markdown report file (default: survived_test_report.md)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N survived mutants (for testing)",
    )
    parser.add_argument(
        "--call-llm",
        action="store_true",
        help="Call OpenAI API to get should_write_test/reason (requires OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--mutmut",
        default=None,
        help="Path to mutmut binary (default: mutmut from PATH or ../../.venv/bin/mutmut)",
    )
    args = parser.parse_args()

    package_dir = find_package_dir()
    results_path = Path(args.results) if args.results else package_dir / "./mutation_testing/mutmut_results.txt"
    stats_path = Path(args.stats) if args.stats else package_dir / "mutants" / "mutmut-stats.json"
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = package_dir / output_path

    mutmut_bin = args.mutmut
    if not mutmut_bin:
        venv_mutmut = package_dir / "../../.venv/bin/mutmut"
        if venv_mutmut.resolve().exists():
            mutmut_bin = str(venv_mutmut.resolve())

    survived = parse_survived_mutants(results_path)
    if not survived:
        print("No survived mutants found in", results_path, file=sys.stderr)
        return 1

    if args.limit:
        survived = survived[: args.limit]
    print(f"Processing {len(survived)} survived mutant(s)...", file=sys.stderr)

    stats = {}
    if stats_path.exists():
        stats = json.loads(stats_path.read_text())

    entries = []
    for i, mutant_id in enumerate(survived):
        print(f"  [{i+1}/{len(survived)}] {mutant_id}", file=sys.stderr)
        mangled = mangled_name_from_mutant_id(mutant_id)
        diff_output = run_mutmut_show(mutant_id, package_dir, mutmut_bin)
        source_file_rel, line_no = parse_diff_for_file_and_line(diff_output)

        source_path = (package_dir / source_file_rel) if source_file_rel else None
        source_snippet = ""
        if source_path and line_no:
            source_snippet = get_source_snippet(source_path, line_no)

        tests_that_run = get_tests_for_mutant(mangled, stats)
        test_files_content = read_test_file_contents(package_dir, tests_that_run)
        # Truncate long test files for prompt (e.g. first 120 lines each)
        test_content_str = ""
        for path, content in test_files_content.items():
            lines = content.splitlines()
            if len(lines) > 120:
                content = "\n".join(lines[:120]) + "\n... (truncated)"
            test_content_str += f"--- {path} ---\n{content}\n\n"

        entry = {
            "mutant_id": mutant_id,
            "mangled_name": mangled,
            "diff": diff_output.strip(),
            "source_file": source_file_rel,
            "source_snippet": source_snippet,
            "tests_that_run": tests_that_run,
            "test_files_content": test_content_str.strip(),
        }
        entry["prompt_for_llm"] = build_llm_prompt(entry)

        if args.call_llm:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                print("  OPENAI_API_KEY not set, skipping LLM call", file=sys.stderr)
            else:
                analysis = fetch_llm_analysis(entry["prompt_for_llm"], api_key)
                entry["llm_analysis"] = analysis

        entries.append(entry)

    report_text = generate_report(entries, results_path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_text)
        print(f"Wrote Markdown report to {output_path}", file=sys.stderr)
    except OSError as e:
        print(f"Error: Could not write report to '{output_path}': {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
