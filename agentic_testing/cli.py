#!/usr/bin/env python3
"""
CLI for agentic testing loop.

Usage:
    # Interactive mode (human approves each test)
    python -m agentic_testing.cli

    # Auto mode (no approval, commit at end)
    python -m agentic_testing.cli --auto

    # Limit to first N mutants
    python -m agentic_testing.cli --limit 5

    # Dry run (don't modify files)
    python -m agentic_testing.cli --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .triage import (
    parse_survived_mutants,
    mangled_name_from_mutant_id,
    run_mutmut_show,
    parse_diff_for_file_and_line,
    get_source_snippet,
    get_tests_for_mutant,
    read_test_file_contents,
    build_llm_prompt,
    fetch_llm_analysis,
)
from .agent_loop import run_agent_loop


def run_mutmut(package_dir: Path) -> bool:
    """Run mutmut to generate mutations."""
    print("Step 1: Running mutmut to generate mutations...")
    print("=" * 80)

    # Use the custom run_mutmut.py wrapper (from agentic_testing folder)
    mutmut_wrapper = Path(__file__).parent / "run_mutmut.py"

    cmd = [
        sys.executable,
        str(mutmut_wrapper),
        "run",
        "--paths-to-mutate",
        "cat_finder.py",
        "--test-time-base=10.0",
    ]

    try:
        result = subprocess.run(cmd, cwd=package_dir, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        # Generate results file
        results_cmd = [sys.executable, str(mutmut_wrapper), "results"]
        cache_dir = package_dir / ".agentic_testing_cache"
        cache_dir.mkdir(exist_ok=True)
        results_output = cache_dir / "mutmut_results.txt"

        with open(results_output, "w") as f:
            result = subprocess.run(
                results_cmd, cwd=package_dir, capture_output=True, text=True
            )
            f.write(result.stdout)

        print(f"\nResults written to {results_output}")
        return True

    except Exception as e:
        print(f"Error running mutmut: {e}", file=sys.stderr)
        return False


def triage_mutants(
    package_dir: Path, api_key: str, limit: int | None = None
) -> list[dict]:
    """
    Run triage on survived mutants using LLM.

    This reuses the logic from analyze_survived_mutants.py.
    """
    print("\n\nStep 2: Triaging survived mutants with LLM...")
    print("=" * 80)

    cache_dir = package_dir / ".agentic_testing_cache"
    cache_dir.mkdir(exist_ok=True)
    results_path = cache_dir / "mutmut_results.txt"
    stats_path = package_dir / "mutants" / "mutmut-stats.json"

    survived = parse_survived_mutants(results_path)
    if not survived:
        print("No survived mutants found.")
        return []

    print(f"Found {len(survived)} survived mutant(s)")

    if limit:
        survived = survived[:limit]
        print(f"Limited to first {limit} mutant(s)")

    # Load stats
    stats = {}
    if stats_path.exists():
        stats = json.loads(stats_path.read_text())

    # Find mutmut binary
    mutmut_bin = None
    venv_mutmut = package_dir / "../../.venv/bin/mutmut"
    if venv_mutmut.exists():
        mutmut_bin = str(venv_mutmut.resolve())

    # Analyze each mutant with LLM triage
    triaged_mutants = []

    for i, mutant_id in enumerate(survived):
        print(f"\n  [{i+1}/{len(survived)}] Triaging {mutant_id}...")

        mangled = mangled_name_from_mutant_id(mutant_id)
        diff_output = run_mutmut_show(mutant_id, package_dir, mutmut_bin)
        source_file_rel, line_no = parse_diff_for_file_and_line(diff_output)

        source_path = (package_dir / source_file_rel) if source_file_rel else None
        source_snippet = ""
        if source_path and line_no:
            source_snippet = get_source_snippet(source_path, line_no)

        tests_that_run = get_tests_for_mutant(mangled, stats)
        test_files_content = read_test_file_contents(package_dir, tests_that_run)

        # Truncate long test files
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

        # Build prompt and call LLM
        prompt = build_llm_prompt(entry)
        analysis = fetch_llm_analysis(prompt, api_key)
        entry["llm_analysis"] = analysis

        should_write = analysis.get("should_write_test", False)
        reason = analysis.get("reason", "")

        print(f"    should_write_test: {should_write}")
        print(f"    reason: {reason}")

        if should_write:
            triaged_mutants.append(entry)

    print(f"\n{len(triaged_mutants)} mutant(s) need tests (after triage)")
    return triaged_mutants


def create_pr(package_dir: Path, summary: dict) -> bool:
    """Create a pull request with the generated tests."""
    print("\n\nCreating pull request...")
    print("=" * 80)

    # Commit changes
    try:
        subprocess.run(
            ["git", "add", "test_cat_finder.py"], cwd=package_dir, check=True
        )

        commit_msg = f"""Add tests from agentic testing loop

Generated {summary['success']} new test(s) to kill survived mutants.

- Success: {summary['success']}
- Errors: {summary['error']}
- Skipped: {summary['skipped']}

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"""

        subprocess.run(
            ["git", "commit", "-m", commit_msg], cwd=package_dir, check=True
        )

        # Push to remote
        branch_name = "agentic-testing-improvements"
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name], cwd=package_dir, check=True
        )

        # Create PR using gh
        pr_body = f"""## Summary
Automatically generated tests to kill survived mutants from mutation testing.

## Results
- Tests generated: {summary['success']}
- Errors: {summary['error']}
- Skipped: {summary['skipped']}

## Test Coverage
These tests were generated by analyzing survived mutants and creating targeted tests to catch the specific mutations.

🤖 Generated with agentic testing loop (inspired by Meta's ACH)
"""

        subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                "Add tests from agentic testing loop",
                "--body",
                pr_body,
                "--base",
                "dev",
            ],
            cwd=package_dir,
            check=True,
        )

        print("✓ Pull request created successfully")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Error creating PR: {e}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agentic testing loop - automatically generate tests for survived mutants"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto mode: skip human approval and create PR automatically",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: don't modify files or create PR",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit to first N mutants (for testing)",
    )
    parser.add_argument(
        "--skip-mutmut",
        action="store_true",
        help="Skip running mutmut (use existing results)",
    )
    parser.add_argument(
        "--skip-triage",
        action="store_true",
        help="Skip triage step (use existing triage results from triage.json)",
    )
    args = parser.parse_args()

    # Get package directory
    package_dir = Path(__file__).resolve().parent.parent

    # Get API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set", file=sys.stderr)
        return 1

    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "AGENTIC TESTING LOOP" + " " * 38 + "║")
    print("║" + " " * 15 + "Inspired by Meta's ACH System" + " " * 34 + "║")
    print("╚" + "=" * 78 + "╝")

    # Step 1: Run mutmut (unless skipped)
    if not args.skip_mutmut:
        if not run_mutmut(package_dir):
            return 1
    else:
        print("Skipping mutmut run (using existing results)")

    # Step 2: Triage mutants (unless skipped)
    triage_results = []
    cache_dir = package_dir / ".agentic_testing_cache"
    cache_dir.mkdir(exist_ok=True)
    triage_cache_path = cache_dir / "triage.json"

    if args.skip_triage and triage_cache_path.exists():
        print(f"Loading triage results from {triage_cache_path}")
        triage_results = json.loads(triage_cache_path.read_text())
    else:
        triage_results = triage_mutants(package_dir, api_key, args.limit)

        # Cache triage results
        triage_cache_path.parent.mkdir(parents=True, exist_ok=True)
        triage_cache_path.write_text(json.dumps(triage_results, indent=2))
        print(f"\nTriage results cached to {triage_cache_path}")

    if not triage_results:
        print("\nNo mutants need tests. Exiting.")
        return 0

    # Step 3: Run agentic loop
    print("\n\nStep 3: Running agentic loop to generate tests...")
    print("=" * 80)

    summary = run_agent_loop(
        triage_results=triage_results,
        package_dir=package_dir,
        api_key=api_key,
        interactive=not args.auto,
        dry_run=args.dry_run,
        limit=args.limit,
    )

    # Step 4: Create PR if auto mode and not dry run
    if args.auto and not args.dry_run and summary["success"] > 0:
        create_pr(package_dir, summary)

    print("\n\n✓ Agentic testing loop complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
