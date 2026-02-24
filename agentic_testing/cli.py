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
import concurrent.futures
import json
import os
import re
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
from .phase_logger import PhaseLogger


def run_mutahunter(
    package_dir: Path,
    source_file: str = "cat_finder.py",
    test_file: str = "test_cat_finder.py",
    model: str = "gpt-5-mini",
    phase_logger: "PhaseLogger | None" = None,
    limit: int | None = None,
) -> bool:
    """Run mutahunter to generate LLM-powered mutations."""
    print("\n" + "=" * 80)
    print("STEP 1: MUTATION GENERATION (LLM-Powered)")
    print("=" * 80)
    print(f"Engine: mutahunter")
    print(f"Source: {source_file}")
    print(f"Model:  {model}")
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
    max_survived = min(limit, 30) if limit else 30
    mutant_entries = parse_mutahunter_results(package_dir, source_file, max_survived=max_survived, phase_logger=phase_logger)

    # Cache parsed results
    cache_dir = package_dir / ".agentic_testing_cache"
    cache_dir.mkdir(exist_ok=True)
    results_cache = cache_dir / "mutahunter_parsed.json"
    results_cache.write_text(json.dumps(mutant_entries, indent=2))
    print(f"Parsed results cached to {results_cache}")

    return True


def run_mutmut(package_dir: Path, source_file: str = "cat_finder.py", phase_logger: "PhaseLogger | None" = None) -> bool:
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

    # mutmut v3 reads config from setup.cfg (paths_to_mutate, runner, etc.)
    # No CLI flags needed -- just "run"
    cmd = [
        python_cmd,
        str(mutmut_wrapper),
        "run",
    ]

    try:
        result = subprocess.run(cmd, cwd=package_dir, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if phase_logger:
            phase_logger.log_mutation("mutmut", source_file, result.stdout + (result.stderr or ""))

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
    mutant_entries: list[dict], package_dir: Path, api_key: str,
    phase_logger: "PhaseLogger | None" = None,
) -> list[dict]:
    """Triage mutahunter mutant entries with LLM (parallel)."""
    # Phase 1: Build all triage entries and prompts locally (fast)
    triage_entries = []
    prompts = []

    # Read test files once (shared across all entries)
    test_files_content = read_test_file_contents(
        package_dir, ["test_cat_finder.py"]
    )
    test_content_str = ""
    for path, content in test_files_content.items():
        lines = content.splitlines()
        if len(lines) > 120:
            content = "\n".join(lines[:120]) + "\n... (truncated)"
        test_content_str += f"--- {path} ---\n{content}\n\n"
    test_content_str = test_content_str.strip()

    for entry in mutant_entries:
        mutant_id = entry.get("mutant_id", "unknown")
        source_file = entry.get("source_file")
        line_no = entry.get("line_no")
        source_snippet = ""

        if source_file and line_no:
            source_path = package_dir / source_file
            if source_path.exists():
                source_snippet = get_source_snippet(source_path, line_no)

        triage_entry = {
            "mutant_id": mutant_id,
            "mangled_name": entry.get("mangled_name", ""),
            "diff": entry.get("diff", ""),
            "source_file": source_file,
            "source_snippet": source_snippet,
            "tests_that_run": ["test_cat_finder.py"],
            "test_files_content": test_content_str,
            "mutant_file": entry.get("mutant_file", ""),
            "line_no": entry.get("line_no"),
        }

        prompt = build_llm_prompt(triage_entry)
        if phase_logger:
            phase_logger.log_triage_request(mutant_id, prompt)

        triage_entries.append(triage_entry)
        prompts.append(prompt)

    # Phase 2: Parallel LLM calls
    print(f"\n  Triaging {len(triage_entries)} mutant(s) in parallel (max_workers=5)...")
    triaged_mutants = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_idx = {
            executor.submit(fetch_llm_analysis, prompt, api_key): idx
            for idx, prompt in enumerate(prompts)
        }

        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            triage_entry = triage_entries[idx]
            mutant_id = triage_entry["mutant_id"]

            analysis = future.result()
            triage_entry["llm_analysis"] = analysis

            if phase_logger:
                phase_logger.log_triage_response(mutant_id, analysis)

            should_write = analysis.get("should_write_test", False)
            reason = analysis.get("reason", "")

            print(f"  [{idx+1}/{len(triage_entries)}] {mutant_id}: should_write={should_write} | {reason}")

            if should_write:
                triaged_mutants.append(triage_entry)

    print(f"\n{len(triaged_mutants)} mutant(s) need tests (after triage)")
    return triaged_mutants


def triage_mutants(
    package_dir: Path,
    api_key: str,
    limit: int | None = None,
    mutation_engine: str = "mutmut",
    phase_logger: "PhaseLogger | None" = None,
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
            print("Error: No mutahunter results found. Run with --skip-mutation=false first.")
            return []

        mutant_entries = json.loads(mutahunter_cache.read_text())
        print(f"Found {len(mutant_entries)} survived mutant(s) from mutahunter")

        if limit:
            mutant_entries = mutant_entries[:limit]
            print(f"Limited to first {limit} mutant(s)")

        # Process mutahunter entries (already have diff and source info)
        return triage_mutahunter_entries(mutant_entries, package_dir, api_key, phase_logger=phase_logger)

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

    # Phase 1: Build all entries locally (includes run_mutmut_show which is local)
    entries = []
    prompts = []

    for i, mutant_id in enumerate(survived):
        print(f"\n  [{i+1}/{len(survived)}] Preparing {mutant_id}...")

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

        prompt = build_llm_prompt(entry)
        if phase_logger:
            phase_logger.log_triage_request(mutant_id, prompt)

        entries.append(entry)
        prompts.append(prompt)

    # Phase 2: Parallel LLM calls
    print(f"\n  Triaging {len(entries)} mutant(s) in parallel (max_workers=5)...")
    triaged_mutants = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_idx = {
            executor.submit(fetch_llm_analysis, prompt, api_key): idx
            for idx, prompt in enumerate(prompts)
        }

        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            entry = entries[idx]
            mutant_id = entry["mutant_id"]

            analysis = future.result()
            entry["llm_analysis"] = analysis

            if phase_logger:
                phase_logger.log_triage_response(mutant_id, analysis)

            should_write = analysis.get("should_write_test", False)
            reason = analysis.get("reason", "")

            print(f"  [{idx+1}/{len(entries)}] {mutant_id}: should_write={should_write} | {reason}")

            if should_write:
                triaged_mutants.append(entry)

    print(f"\n{len(triaged_mutants)} mutant(s) need tests (after triage)")
    return triaged_mutants


def create_pr(package_dir: Path, summary: dict, mutation_engine: str = "mutmut") -> bool:
    """Create a pull request with the generated tests."""
    print("\n\nCreating pull request...")
    print("=" * 80)

    try:
        from datetime import datetime

        # Create unique branch name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        branch_name = f"agentic-testing-{timestamp}"

        print(f"Creating new branch: {branch_name}")

        # Create and checkout new branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name], cwd=package_dir, check=True
        )

        # Stage changes
        subprocess.run(
            ["git", "add", "test_cat_finder.py"], cwd=package_dir, check=True
        )

        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=package_dir,
            capture_output=True,
        )

        if result.returncode == 0:
            print("No changes to commit")
            return False

        # Create commit
        already_killed_count = sum(1 for r in summary.get('results', [])
                                    if r.get('status') == 'already_killed')

        commit_msg = f"""Add tests from agentic testing loop

Generated {summary['success']} new test(s) to kill survived mutants.

Stats:
- Success: {summary['success']}
- Already killed: {already_killed_count}
- Errors: {summary['error']}
- Skipped: {summary['skipped']}
- Mutation engine: {mutation_engine}

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"""

        subprocess.run(
            ["git", "commit", "-m", commit_msg], cwd=package_dir, check=True
        )

        print(f"Pushing branch to remote...")

        # Push to remote
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name], cwd=package_dir, check=True
        )

        # Create PR using gh
        pr_body = f"""## Summary
Automatically generated tests to kill survived mutants from mutation testing.

## Results
- Tests generated: {summary['success']}
- Already killed (by previous tests): {already_killed_count}
- Errors: {summary['error']}
- Skipped: {summary['skipped']}
- Mutation engine: {mutation_engine}

## Test Coverage
These tests were generated by:
1. Running mutation testing with {mutation_engine}
2. Triaging mutations with LLM (filtering cosmetic changes)
3. Generating targeted tests for critical mutations
4. Verifying tests kill the mutants

Each test is designed to catch a specific mutation that would otherwise go undetected.
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


def build_pr_body(result: dict, mutation_engine: str = "mutmut") -> str:
    """Build a rich markdown PR body for a single mutant kill."""
    mutant_id = result.get("mutant_id", "unknown")
    test_method = result.get("test_method_name", "unknown")
    source_file = result.get("source_file", "unknown")
    line_no = result.get("line_no", "?")
    explanation = result.get("explanation", "")
    diff = result.get("diff", "")
    iterations = result.get("iterations", 1)

    body = f"""## Summary
Kill mutant `{mutant_id}` with `{test_method}`.

## Mutation details
- **Source:** `{source_file}:{line_no}`
- **Mutant ID:** `{mutant_id}`
- **Engine:** {mutation_engine}
- **Iterations to fix:** {iterations}

### Mutation diff
```diff
{diff if diff else "(no diff available)"}
```

## Why this test exists
{explanation if explanation else "(no explanation available)"}

---
Generated by the agentic testing pipeline thing.
"""
    return body


def create_individual_prs(
    package_dir: Path,
    summary: dict,
    mutation_engine: str = "mutmut",
    base_branch: str = "dev",
    test_file: str = "test_cat_finder.py",
) -> list[dict]:
    """Create one PR per successfully killed mutant."""
    from datetime import datetime
    from .test_applier import insert_test_method

    successful = [r for r in summary.get("results", []) if r.get("status") == "success"]
    if not successful:
        print("No successful mutant kills to create PRs for.")
        return []

    print(f"\nCreating individual PRs for {len(successful)} mutant kill(s)...")
    print("=" * 80)

    # Save current state
    orig_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=package_dir, capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Stash uncommitted changes (the accumulated test file)
    stash_result = subprocess.run(
        ["git", "stash", "push", "-m", "agentic-testing-individual-prs"],
        cwd=package_dir, capture_output=True, text=True,
    )
    did_stash = "No local changes" not in stash_result.stdout

    # Get the base branch's version of the test file
    try:
        base_test_content = subprocess.run(
            ["git", "show", f"{base_branch}:{test_file}"],
            cwd=package_dir, capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        print(f"Error: could not read {test_file} from {base_branch}", file=sys.stderr)
        if did_stash:
            subprocess.run(["git", "stash", "pop"], cwd=package_dir)
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pr_results = []

    for result in successful:
        mutant_id = result.get("mutant_id", "unknown")
        test_class = result.get("test_class")
        test_code = result.get("test_code")
        test_method = result.get("test_method_name", "unknown")
        source_file = result.get("source_file", "unknown")
        line_no = result.get("line_no", "?")

        if not test_class or not test_code:
            pr_results.append({
                "mutant_id": mutant_id, "pr_created": False,
                "pr_url": None, "error": "Missing test_class or test_code",
            })
            continue

        # Sanitize mutant_id for branch name
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(mutant_id))[:50]
        branch_name = f"agentic-test/{safe_id}-{timestamp}"

        try:
            # Create branch from base
            subprocess.run(
                ["git", "checkout", "-b", branch_name, base_branch],
                cwd=package_dir, capture_output=True, text=True, check=True,
            )

            # Apply single test to the base branch's test file
            success, new_content, error = insert_test_method(
                base_test_content, test_class, test_code,
            )
            if not success:
                raise RuntimeError(f"insert_test_method failed: {error}")

            (package_dir / test_file).write_text(new_content)

            # Commit
            subprocess.run(
                ["git", "add", test_file], cwd=package_dir, check=True,
            )

            commit_msg = f"test: kill mutant {mutant_id} in {source_file}:{line_no}"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=package_dir, capture_output=True, text=True, check=True,
            )

            # Push
            subprocess.run(
                ["git", "push", "-u", "origin", branch_name],
                cwd=package_dir, capture_output=True, text=True, check=True,
            )

            # Create PR
            pr_title = f"test: kill mutant in {source_file}:{line_no} ({test_method})"
            if len(pr_title) > 72:
                pr_title = pr_title[:69] + "..."

            pr_body = build_pr_body(result, mutation_engine)

            gh_result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--title", pr_title,
                    "--body", pr_body,
                    "--base", base_branch,
                ],
                cwd=package_dir, capture_output=True, text=True, check=True,
            )

            pr_url = gh_result.stdout.strip()
            print(f"  PR created for {mutant_id}: {pr_url}")
            pr_results.append({
                "mutant_id": mutant_id, "pr_created": True,
                "pr_url": pr_url, "error": None,
            })

        except Exception as e:
            print(f"  Error creating PR for {mutant_id}: {e}", file=sys.stderr)
            pr_results.append({
                "mutant_id": mutant_id, "pr_created": False,
                "pr_url": None, "error": str(e),
            })
            # Clean up the failed branch (best effort)
            subprocess.run(
                ["git", "checkout", orig_branch],
                cwd=package_dir, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=package_dir, capture_output=True, text=True,
            )
            continue

    # Return to original branch
    subprocess.run(
        ["git", "checkout", orig_branch],
        cwd=package_dir, capture_output=True, text=True,
    )
    if did_stash:
        subprocess.run(["git", "stash", "pop"], cwd=package_dir)

    return pr_results


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
        "--skip-mutation",
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
    parser.add_argument(
        "--batched-pr",
        action="store_true",
        help="Create a single batched PR instead of one PR per killed mutant",
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

    # Create phase logger
    cache_dir = package_dir / ".agentic_testing_cache"
    cache_dir.mkdir(exist_ok=True)
    phase_logger = PhaseLogger(cache_dir)
    print(f"Logging to: {phase_logger.run_dir}")

    # Step 1: Run mutation engine (unless skipped)
    if not args.skip_mutation:
        if args.mutation_engine == "mutmut":
            if not run_mutmut(package_dir, args.source_file, phase_logger=phase_logger):
                return 1
        elif args.mutation_engine == "mutahunter":
            if not run_mutahunter(package_dir, args.source_file, args.test_file, phase_logger=phase_logger, limit=args.limit):
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
            package_dir, api_key, args.limit, args.mutation_engine,
            phase_logger=phase_logger,
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
        phase_logger=phase_logger,
    )

    # Step 4: Create PR(s) if auto mode and not dry run
    if args.auto and not args.dry_run and summary["success"] > 0:
        if args.batched_pr:
            create_pr(package_dir, summary, args.mutation_engine)
        else:
            pr_results = create_individual_prs(
                package_dir, summary,
                mutation_engine=args.mutation_engine,
                test_file=args.test_file,
            )
            created = sum(1 for r in pr_results if r["pr_created"])
            failed = len(pr_results) - created
            print(f"\nIndividual PRs: {created} created, {failed} failed")
            for r in pr_results:
                status = r["pr_url"] if r["pr_created"] else f"FAILED: {r['error']}"
                print(f"  {r['mutant_id']}: {status}")

    print("\n\nAgentic testing loop complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
