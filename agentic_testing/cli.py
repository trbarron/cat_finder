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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required if env vars set directly

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
from .mutahunter_parser import parse_mutahunter_results


def run_mutahunter(
    package_dir: Path,
    source_file: str = "cat_finder.py",
    test_file: str = "test_cat_finder.py",
    model: str = "gpt-4o-mini",
) -> bool:
    """Run mutahunter to generate LLM-powered mutations."""
    print("\n" + "=" * 80)
    print("STEP 1: MUTATION GENERATION (LLM-Powered)")
    print("=" * 80)
    print(f"Engine: mutahunter")
    print(f"Source: {source_file}")
    print(f"Model:  {model}")
    print("Note:   LLM-powered mutations are slower but more realistic")
    print("=" * 80)

    venv_path = Path(__file__).parent / "venv"
    venv_python = venv_path / "bin" / "python"

    if not venv_python.exists():
        print("\nError: venv not found at", venv_path, file=sys.stderr)
        print("Run: cd agentic_testing && python3.11 -m venv venv", file=sys.stderr)
        return False

    # Import and run mutahunter wrapper
    from .run_mutahunter import run_mutahunter as run_mh

    # Test command using venv python
    test_command = f"{venv_python} -m pytest {test_file} -v"

    success = run_mh(
        source_file=source_file,
        test_file=test_file,
        test_command=test_command,
        model=model,
        venv_path=venv_path,
    )

    if not success:
        return False

    # Parse mutahunter results
    print("\nParsing mutahunter results...")
    mutant_entries = parse_mutahunter_results(package_dir, source_file)

    # Cache parsed results
    cache_dir = package_dir / ".agentic_testing_cache"
    cache_dir.mkdir(exist_ok=True)
    results_cache = cache_dir / "mutahunter_parsed.json"
    results_cache.write_text(json.dumps(mutant_entries, indent=2))
    print(f"Parsed results cached to {results_cache}")

    return True


def run_mutmut(package_dir: Path, source_file: str = "cat_finder.py") -> bool:
    """Run mutmut to generate mutations."""
    print("\n" + "=" * 80)
    print("STEP 1: MUTATION GENERATION (Rule-Based)")
    print("=" * 80)
    print(f"Engine: mutmut")
    print(f"Source: {source_file}")
    print("=" * 80)

    # Use venv Python if available
    venv_python = Path(__file__).parent / "venv" / "bin" / "python"
    python_cmd = str(venv_python) if venv_python.exists() else sys.executable

    # Use the custom run_mutmut.py wrapper (from agentic_testing folder)
    mutmut_wrapper = Path(__file__).parent / "run_mutmut.py"

    cmd = [
        python_cmd,
        str(mutmut_wrapper),
        "run",
        "--paths-to-mutate",
        source_file,
        "--test-time-base=10.0",
    ]

    try:
        result = subprocess.run(cmd, cwd=package_dir, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        # Generate results file
        results_cmd = [python_cmd, str(mutmut_wrapper), "results"]
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




def triage_mutahunter_entries(
    mutant_entries: list[dict], package_dir: Path, api_key: str
) -> list[dict]:
    """Triage mutahunter mutant entries with LLM."""
    triaged_mutants = []

    for i, entry in enumerate(mutant_entries):
        mutant_id = entry.get("mutant_id", "unknown")
        print(f"\n  [{i+1}/{len(mutant_entries)}] Triaging {mutant_id}...")

        # Get source snippet
        source_file = entry.get("source_file")
        line_no = entry.get("line_no")
        source_snippet = ""

        if source_file and line_no:
            source_path = package_dir / source_file
            if source_path.exists():
                source_snippet = get_source_snippet(source_path, line_no)

        # Read test files (use default for now)
        test_files_content = read_test_file_contents(
            package_dir, ["test_cat_finder.py"]
        )

        # Truncate long test files
        test_content_str = ""
        for path, content in test_files_content.items():
            lines = content.splitlines()
            if len(lines) > 120:
                content = "\n".join(lines[:120]) + "\n... (truncated)"
            test_content_str += f"--- {path} ---\n{content}\n\n"

        # Build triage entry
        triage_entry = {
            "mutant_id": mutant_id,
            "mangled_name": entry.get("mangled_name", ""),
            "diff": entry.get("diff", ""),
            "source_file": source_file,
            "source_snippet": source_snippet,
            "tests_that_run": ["test_cat_finder.py"],
            "test_files_content": test_content_str.strip(),
            "mutant_file": entry.get("mutant_file", ""),  # IMPORTANT: for verification
            "line_no": entry.get("line_no"),
        }

        # Build prompt and call LLM
        prompt = build_llm_prompt(triage_entry)
        analysis = fetch_llm_analysis(prompt, api_key)
        triage_entry["llm_analysis"] = analysis

        should_write = analysis.get("should_write_test", False)
        reason = analysis.get("reason", "")

        print(f"    should_write_test: {should_write}")
        print(f"    reason: {reason}")

        if should_write:
            triaged_mutants.append(triage_entry)

    print(f"\n{len(triaged_mutants)} mutant(s) need tests (after triage)")
    return triaged_mutants


def triage_mutants(
    package_dir: Path,
    api_key: str,
    limit: int | None = None,
    mutation_engine: str = "mutmut",
) -> list[dict]:
    """
    Run triage on survived mutants using LLM.

    This reuses the logic from analyze_survived_mutants.py.
    """
    print("\n" + "=" * 80)
    print("STEP 2: LLM TRIAGE")
    print("=" * 80)

    cache_dir = package_dir / ".agentic_testing_cache"
    cache_dir.mkdir(exist_ok=True)

    # Load mutants based on engine
    if mutation_engine == "mutahunter":
        # Load parsed mutahunter results
        mutahunter_cache = cache_dir / "mutahunter_parsed.json"
        if not mutahunter_cache.exists():
            print("Error: No mutahunter results found. Run with --skip-mutmut=false first.")
            return []

        mutant_entries = json.loads(mutahunter_cache.read_text())
        print(f"Found {len(mutant_entries)} survived mutant(s) from mutahunter")

        if limit:
            mutant_entries = mutant_entries[:limit]
            print(f"Limited to first {limit} mutant(s)")

        # Process mutahunter entries (already have diff and source info)
        return triage_mutahunter_entries(mutant_entries, package_dir, api_key)

    else:
        # Original mutmut logic
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

        print("Pull request created successfully")
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
        help="Skip running mutation generation (use existing results)",
    )
    parser.add_argument(
        "--skip-triage",
        action="store_true",
        help="Skip triage step (use existing triage results from triage.json)",
    )
    parser.add_argument(
        "--mutation-engine",
        choices=["mutmut", "mutahunter"],
        default="mutmut",
        help="Mutation engine: mutmut (rule-based, fast) or mutahunter (LLM-powered, semantic)",
    )
    parser.add_argument(
        "--source-file",
        default="cat_finder.py",
        help="Source file to mutate (default: cat_finder.py)",
    )
    parser.add_argument(
        "--test-file",
        default="test_cat_finder.py",
        help="Test file (default: test_cat_finder.py)",
    )
    args = parser.parse_args()

    # Get package directory
    package_dir = Path(__file__).resolve().parent.parent

    # Get API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set", file=sys.stderr)
        return 1

    print("=" * 80)
    print("AGENTIC TESTING LOOP".center(80))
    print("Inspired by Meta's ACH System".center(80))
    print("=" * 80)

    # Step 1: Run mutation engine (unless skipped)
    if not args.skip_mutmut:
        if args.mutation_engine == "mutmut":
            if not run_mutmut(package_dir, args.source_file):
                return 1
        elif args.mutation_engine == "mutahunter":
            if not run_mutahunter(package_dir, args.source_file, args.test_file):
                return 1
        else:
            print(f"Unknown mutation engine: {args.mutation_engine}", file=sys.stderr)
            return 1
    else:
        print(f"Skipping {args.mutation_engine} run (using existing results)")

    # Step 2: Triage mutants (unless skipped)
    triage_results = []
    cache_dir = package_dir / ".agentic_testing_cache"
    cache_dir.mkdir(exist_ok=True)
    triage_cache_path = cache_dir / "triage.json"

    if args.skip_triage and triage_cache_path.exists():
        print(f"Loading triage results from {triage_cache_path}")
        triage_results = json.loads(triage_cache_path.read_text())
    else:
        triage_results = triage_mutants(
            package_dir, api_key, args.limit, args.mutation_engine
        )

        # Cache triage results
        triage_cache_path.parent.mkdir(parents=True, exist_ok=True)
        triage_cache_path.write_text(json.dumps(triage_results, indent=2))
        print(f"\nTriage results cached to {triage_cache_path}")

    if not triage_results:
        print("\nNo mutants need tests. Exiting.")
        return 0

    # Step 3: Run agentic loop
    print("\n" + "=" * 80)
    print("STEP 3: TEST GENERATION & VERIFICATION")
    print("=" * 80)
    print(f"Mode: {'INTERACTIVE (approval required)' if not args.auto else 'AUTOMATED'}")
    print(f"Processing: {len(triage_results)} mutant(s)")
    print("=" * 80)

    summary = run_agent_loop(
        triage_results=triage_results,
        package_dir=package_dir,
        api_key=api_key,
        interactive=not args.auto,
        dry_run=args.dry_run,
        limit=args.limit,
        mutation_engine=args.mutation_engine,
    )

    # Step 4: Create PR if auto mode and not dry run
    if args.auto and not args.dry_run and summary["success"] > 0:
        create_pr(package_dir, summary)

    print("\n\nAgentic testing loop complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
