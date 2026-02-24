#!/usr/bin/env python3
"""
Main agentic loop for LLM-powered mutation testing.

Inspired by Meta's ACH (Automated Compliance Hardening):
1. Run mutmut to identify survived mutants
2. LLM triages mutants and filters out non-critical ones
3. LLM generates test code for critical mutations
4. Apply tests to test files
5. Verify tests work
6. Iterate
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .test_generator import generate_test_code
from .test_applier import apply_test
from .verifier import verify_test_kills_mutant
from .test_fixer import fix_failed_test
from .error_extractor import extract_pytest_error
from .phase_logger import PhaseLogger


def check_tests_pass(package_dir: Path, test_file: str = "test_cat_finder.py") -> tuple[bool, str]:
    """
    Run pytest to verify all tests pass.

    Returns:
        (all_passed, output)
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", test_file, "-v", "--tb=short"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )

        all_passed = result.returncode == 0
        output = f"{result.stdout}\n{result.stderr}"

        return (all_passed, output)

    except subprocess.TimeoutExpired:
        return (False, "Timeout running tests (>120s)")
    except Exception as e:
        return (False, f"Error running tests: {e}")


class AgentLogger:
    """Logger for agentic testing loop that saves all generated tests and results."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Create timestamped log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"agentic_loop_{timestamp}.log"
        self.generated_tests_file = self.log_dir / f"generated_tests_{timestamp}.py"

        # Initialize files
        self._write_header()

    def _write_header(self):
        """Write header to log file."""
        with open(self.log_file, "w") as f:
            f.write("=" * 80 + "\n")
            f.write("AGENTIC TESTING LOOP LOG\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

        with open(self.generated_tests_file, "w") as f:
            f.write("# Generated tests from agentic testing loop\n")
            f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    def log(self, message: str):
        """Write message to log file."""
        with open(self.log_file, "a") as f:
            f.write(message + "\n")

    def log_mutant_start(self, mutant_id: str, index: int, total: int):
        """Log start of processing a mutant."""
        msg = f"\n{'='*80}\n[{index}/{total}] Processing Mutant: {mutant_id}\n{'='*80}\n"
        self.log(msg)
        print(msg)

    def log_test_generated(
        self, mutant_id: str, test_class: str, test_method: str, test_code: str, explanation: str
    ):
        """Log a generated test."""
        msg = f"""
Test Generated:
  Mutant ID: {mutant_id}
  Class: {test_class}
  Method: {test_method}
  Explanation: {explanation}

Code:
{test_code}
"""
        self.log(msg)

        # Also save to generated_tests file
        with open(self.generated_tests_file, "a") as f:
            f.write(f"\n# Mutant: {mutant_id}\n")
            f.write(f"# Explanation: {explanation}\n")
            f.write(test_code + "\n\n")

    def log_result(self, mutant_id: str, status: str, details: str = ""):
        """Log the result of processing a mutant."""
        msg = f"""
Result for {mutant_id}:
  Status: {status}
  Details: {details}
"""
        self.log(msg)

    def log_summary(self, summary: dict):
        """Log final summary."""
        effective_kills = summary.get('success', 0) + summary.get('already_killed', 0)
        total = summary['total']
        msg = f"""
{'='*80}
FINAL SUMMARY
{'='*80}
Total processed:      {total}
  Already killed:     {summary.get('already_killed', 0)}
  New tests written:  {summary.get('success', 0)}
  Verification failed:{summary.get('verification_failed', 0)}
  Errors:             {summary.get('error', 0)}
  Skipped:            {summary.get('skipped', 0)}
  Rejected:           {summary.get('rejected', 0)}
  ---
  Effective kill rate: {effective_kills}/{total} ({effective_kills*100//total if total else 0}%)
"""
        self.log(msg)

        print(f"\nLog files written to:")
        print(f"  Full log: {self.log_file}")
        print(f"  Generated tests: {self.generated_tests_file}")


def process_mutant(
    mutant_entry: dict[str, Any],
    package_dir: Path,
    api_key: str,
    interactive: bool = True,
    dry_run: bool = False,
    max_iterations: int = 5,
    logger: AgentLogger = None,
    mutation_engine: str = "mutmut",
    phase_logger: PhaseLogger | None = None,
) -> dict[str, Any]:
    """
    Process a single mutant: generate test, apply, verify.

    Args:
        mutant_entry: Mutant data including agent_prompt from triage
        package_dir: Root package directory
        api_key: OpenAI API key
        interactive: If True, ask for user approval
        dry_run: If True, don't actually modify files

    Returns:
        dict with status and details
    """
    mutant_id = mutant_entry.get("mutant_id", "unknown")
    agent_prompt = mutant_entry.get("llm_analysis", {}).get("agent_prompt", "")
    source_file = mutant_entry.get("source_file", "")
    line_no = mutant_entry.get("line_no")

    if not agent_prompt:
        return {
            "mutant_id": mutant_id,
            "status": "skipped",
            "reason": "No agent_prompt available",
        }

    print(f"\n{'-'*80}")
    print(f"Mutant: {mutant_id}")
    if source_file:
        print(f"Source: {source_file}:{line_no if line_no else '?'}")
    print(f"{'-'*80}")

    # Extract test file path from agent_prompt (heuristic)
    test_file_name = "test_cat_finder.py"  # Default
    if "test_" in agent_prompt.lower():
        # Try to extract test file name
        import re

        match = re.search(r"test_\w+\.py", agent_prompt)
        if match:
            test_file_name = match.group(0)

    test_file_path = package_dir / test_file_name

    if not test_file_path.exists():
        return {
            "mutant_id": mutant_id,
            "status": "error",
            "reason": f"Test file not found: {test_file_path}",
        }

    existing_test_content = test_file_path.read_text()

    # Get source context for LLM
    source_snippet = mutant_entry.get("source_snippet", "")
    diff = mutant_entry.get("diff", "")
    full_source_context = f"{diff}\n\n{source_snippet}" if diff and source_snippet else (source_snippet or diff or "")

    # Read full source file for richer LLM context
    source_file_name = mutant_entry.get("source_file", "cat_finder.py")
    source_file_path = package_dir / source_file_name
    full_source = source_file_path.read_text() if source_file_path.exists() else ""

    # Iteration loop: try to generate and fix test up to max_iterations times
    test_class = None
    test_method_name = None
    test_code = None
    explanation = None
    error_message = ""  # Initialize to avoid UnboundLocalError

    for iteration in range(1, max_iterations + 1):
        iteration_suffix = f" (attempt {iteration}/{max_iterations})" if iteration > 1 else ""

        # Step 1: Generate or fix test code
        if iteration == 1:
            print(f"\n1. Generating test code...")
            print(f"   Agent prompt: {agent_prompt[:100]}...")

            if phase_logger:
                phase_logger.log_generation_request(mutant_id, agent_prompt)

            test_result = generate_test_code(
                agent_prompt, test_file_path, existing_test_content, api_key, full_source_context,
                full_source=full_source,
                mutation_diff=diff,
            )

            if phase_logger:
                phase_logger.log_generation_response(mutant_id, test_result)
        else:
            # Iteration > 1: Fix the failed test
            print(f"\n1. Fixing test{iteration_suffix}...")
            if phase_logger:
                phase_logger.log_fixing_attempt(mutant_id, iteration, error_message)

            test_result = fix_failed_test(
                test_code,
                error_message,
                test_class,
                test_method_name,
                full_source_context,
                agent_prompt,
                api_key,
                mutation_diff=diff,
            )

            if phase_logger:
                phase_logger.log_fixing_response(mutant_id, iteration, test_result)

        if not test_result.get("success"):
            error_msg = test_result.get('error', 'Unknown error')
            print(f"\n   ERROR: Test {'generation' if iteration == 1 else 'fix'} failed: {error_msg}")
            if iteration == max_iterations:
                return {
                    "mutant_id": mutant_id,
                    "status": "error",
                    "reason": f"Test {'generation' if iteration == 1 else 'fix'} failed after {max_iterations} attempts: {error_msg}",
                }
            continue

        test_class = test_result.get("test_class", test_class)
        test_method_name = test_result.get("test_method_name", test_method_name)
        test_code = test_result["test_code"]
        explanation = test_result.get("explanation", explanation)

        print(f"\n2. {'Generated' if iteration == 1 else 'Fixed'} test{iteration_suffix}:")
        print(f"   Class: {test_class}")
        print(f"   Method: {test_method_name}")
        print(f"   Explanation: {explanation}")
        if iteration > 1 and "changes" in test_result:
            print(f"   Changes: {test_result['changes']}")
        print(f"\n   Code:")
        print("   " + "\n   ".join(test_code.split("\n")))

        # Log generated test
        if logger and iteration == 1:
            logger.log_test_generated(mutant_id, test_class, test_method_name, test_code, explanation)

        # Step 2: Interactive approval (only on first iteration or if user wants to approve fixes)
        if interactive and not dry_run and iteration == 1:
            print(f"\n3. Approve this test? [y/n/skip/quit]: ", end="")
            response = input().strip().lower()

            if response == "quit" or response == "q":
                return {"mutant_id": mutant_id, "status": "quit", "reason": "User quit"}
            elif response == "skip" or response == "s":
                return {"mutant_id": mutant_id, "status": "skipped", "reason": "User skipped"}
            elif response != "y" and response != "yes":
                return {"mutant_id": mutant_id, "status": "rejected", "reason": "User rejected"}

        # Step 3: Apply test
        step_num = 4 if (interactive and not dry_run and iteration == 1) else 3
        print(f"\n{step_num}. Applying test to {test_file_path}...")

        # If iteration > 1, replace the existing test
        apply_success, apply_msg = apply_test(
            test_file_path,
            test_class,
            test_code,
            dry_run=dry_run,
            replace=(iteration > 1),
            method_name=test_method_name if iteration > 1 else None,
        )

        if not apply_success:
            error_message = apply_msg  # Save for next iteration
            print(f"   ERROR: {apply_msg}")

            if iteration == max_iterations:
                return {
                    "mutant_id": mutant_id,
                    "status": "error",
                    "reason": f"Failed to apply test after {max_iterations} attempts: {apply_msg}",
                }
            continue

        print(f"   {apply_msg}")

        # Step 4: Verify test
        if not dry_run:
            step_num += 1
            print(f"\n{step_num}. Verifying test{iteration_suffix}...")

            # Get mutant file path for mutahunter
            mutant_file_path = mutant_entry.get("mutant_file") if mutation_engine == "mutahunter" else None

            verify_success, verify_msg = verify_test_kills_mutant(
                mutant_id,
                test_file_path,
                package_dir,
                mutation_engine=mutation_engine,
                mutant_file_path=mutant_file_path,
            )

            if verify_success:
                print(f"   PASS: {verify_msg}")
                if phase_logger:
                    phase_logger.log_verification(mutant_id, "mutant", True, verify_msg)
                return {
                    "mutant_id": mutant_id,
                    "status": "success",
                    "test_class": test_class,
                    "test_method_name": test_method_name,
                    "explanation": explanation,
                    "iterations": iteration,
                }
            else:
                print(f"   FAIL: Verification failed")
                if phase_logger:
                    phase_logger.log_verification(mutant_id, "mutant", False, verify_msg)
                # Extract error for next iteration - pass full trace for better LLM context
                error_info = extract_pytest_error(verify_msg)
                full_trace = error_info.get("full_trace", "")
                short_error = error_info.get("error_message", "")
                error_message = full_trace if full_trace else (short_error if short_error else verify_msg)

                # Show concise error
                if "FAILED against original" in verify_msg:
                    print(f"   Reason: Test fails with original code")
                elif "PASSED" in verify_msg and "mutant" in verify_msg:
                    print(f"   Reason: Mutant survived (test passes with mutant)")
                else:
                    print(f"   Error: {error_message[:100]}...")

                if iteration == max_iterations:
                    # Exhausted all attempts - roll back the test
                    print(f"\n   FAIL: Exhausted {max_iterations} attempts. Rolling back test...")

                    from .test_applier import remove_test

                    rollback_success, rollback_msg = remove_test(
                        test_file_path, test_class, test_method_name
                    )

                    if rollback_success:
                        print(f"   OK: Test rolled back successfully")
                    else:
                        print(f"   FAIL: Failed to roll back test: {rollback_msg}")

                    return {
                        "mutant_id": mutant_id,
                        "status": "verification_failed",
                        "reason": f"Test failed verification after {max_iterations} attempts (rolled back)",
                        "test_applied": False,  # Rolled back
                        "last_error": error_message,
                        "rollback_success": rollback_success,
                    }

                print(f"   Attempting to fix the test...")
        else:
            # Dry run - consider it a success
            return {
                "mutant_id": mutant_id,
                "status": "success",
                "test_class": test_class,
                "test_method_name": test_method_name,
                "explanation": explanation,
                "iterations": iteration,
            }

    # Should not reach here, but just in case
    return {
        "mutant_id": mutant_id,
        "status": "error",
        "reason": f"Unexpected: exhausted {max_iterations} iterations",
    }


def run_agent_loop(
    triage_results: list[dict[str, Any]],
    package_dir: Path,
    api_key: str,
    interactive: bool = True,
    dry_run: bool = False,
    limit: int | None = None,
    mutation_engine: str = "mutmut",
    phase_logger: PhaseLogger | None = None,
) -> dict[str, Any]:
    """
    Main agentic loop.

    Args:
        triage_results: List of triaged mutants (should_write_test: true)
        package_dir: Root package directory
        api_key: OpenAI API key
        interactive: If True, ask for approval for each test
        dry_run: If True, don't actually modify files
        limit: If set, only process first N mutants

    Returns:
        Summary statistics
    """
    if limit:
        triage_results = triage_results[:limit]

    # Create logger
    log_dir = package_dir / ".agentic_testing_cache" / "logs"
    logger = AgentLogger(log_dir)

    print(f"\n{'='*80}")
    print(f"Agentic Testing Loop - Processing {len(triage_results)} mutant(s)")
    print(f"Mode: {'INTERACTIVE' if interactive else 'AUTO'}")
    print(f"Logging to: {logger.log_file.name}")
    print(f"{'='*80}")

    # Sanity check: verify tests pass before we start
    print("\nRunning sanity check: verifying all tests pass...")
    tests_pass, test_output = check_tests_pass(package_dir)

    if not tests_pass:
        print("FAIL: SANITY CHECK FAILED: Tests are already broken!")
        print("\nTest output (last 500 chars):")
        print(test_output[-500:])
        print("\nPlease fix the tests before running the agentic loop.")
        return {
            "total": 0,
            "success": 0,
            "error": 0,
            "skipped": 0,
            "rejected": 0,
            "results": [],
            "sanity_check_failed": True,
        }

    print("OK: Sanity check passed - all tests passing\n")

    # Bulk pre-filter: check which mutants are already killed by existing tests
    # This avoids wasting time on mutants the current test suite already handles
    already_killed_results = []
    remaining_triage = []

    if mutation_engine == "mutahunter":
        from .verifier import _verify_mutahunter_mutant
        test_file_path = package_dir / "test_cat_finder.py"

        print(f"Pre-filtering: checking {len(triage_results)} mutant(s) against existing tests...")
        for entry in triage_results:
            mid = entry.get("mutant_id", "unknown")
            mfile = entry.get("mutant_file")
            if mfile:
                killed, _ = _verify_mutahunter_mutant(mid, mfile, test_file_path, package_dir)
                if killed:
                    already_killed_results.append({
                        "mutant_id": mid,
                        "status": "already_killed",
                        "reason": "Killed by existing tests (pre-filter)",
                    })
                    logger.log_result(mid, "already_killed", "Killed by existing tests (pre-filter)")
                    if phase_logger:
                        phase_logger.log_prefilter(mid, True)
                    continue
            remaining_triage.append(entry)
            if phase_logger:
                phase_logger.log_prefilter(mid, False)

        filtered = len(already_killed_results)
        print(f"Pre-filter complete: {filtered} already killed, {len(remaining_triage)} remaining\n")
        if phase_logger:
            phase_logger.log_prefilter_summary(filtered, len(remaining_triage))

        if remaining_triage == [] and filtered > 0:
            print("WARNING: ALL mutants are already killed by existing tests.")
            print("The mutant list is likely stale (generated from an older test suite).")
            print("Re-run the full pipeline without --skip-mutation to generate fresh mutations.\n")
    else:
        remaining_triage = triage_results

    results = list(already_killed_results)
    for i, mutant_entry in enumerate(remaining_triage):
        mutant_id = mutant_entry.get("mutant_id", "unknown")
        logger.log_mutant_start(mutant_id, i + 1, len(remaining_triage))

        # Sanity check before each mutant: verify tests still pass
        print(f"Sanity check: verifying all tests pass...")
        tests_pass, test_output = check_tests_pass(package_dir)

        if not tests_pass:
            print(f"FAIL: Sanity check failed before processing {mutant_id}!")
            print("Tests are broken, likely from a previous mutation.")
            print("\nStopping to prevent further damage.")
            results.append({
                "mutant_id": mutant_id,
                "status": "sanity_check_failed",
                "reason": "Tests broken before processing this mutant",
            })
            break

        print(f"OK: All tests pass")

        # Re-check this mutant in case a test written earlier in this run now kills it
        mutant_file_path = mutant_entry.get("mutant_file") if mutation_engine == "mutahunter" else None

        if mutation_engine == "mutahunter" and mutant_file_path:
            test_file_path = package_dir / "test_cat_finder.py"
            already_killed, msg = _verify_mutahunter_mutant(
                mutant_id, mutant_file_path, test_file_path, package_dir
            )

            if already_killed:
                print(f"OK: Mutant now killed by a test written earlier in this run - skipping")
                results.append({
                    "mutant_id": mutant_id,
                    "status": "already_killed",
                    "reason": "Killed by test written earlier in this run",
                })
                logger.log_result(mutant_id, "already_killed", "Killed by test written earlier in this run")
                continue

        result = process_mutant(
            mutant_entry,
            package_dir,
            api_key,
            interactive,
            dry_run,
            logger=logger,
            mutation_engine=mutation_engine,
            phase_logger=phase_logger,
        )
        results.append(result)

        # Log result
        logger.log_result(
            mutant_id, result["status"], result.get("reason", result.get("explanation", ""))
        )

        if result["status"] == "quit":
            print("\nUser requested quit. Stopping.")
            break

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    success_count = sum(1 for r in results if r["status"] == "success")
    already_killed_count = sum(1 for r in results if r["status"] == "already_killed")
    verification_failed_count = sum(1 for r in results if r["status"] == "verification_failed")
    error_count = sum(1 for r in results if r["status"] == "error")
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
    rejected_count = sum(1 for r in results if r["status"] == "rejected")

    total = len(results)
    effective_kills = success_count + already_killed_count
    print(f"Processed:            {total}")
    print(f"  Already killed:     {already_killed_count:3d}")
    print(f"  New tests written:  {success_count:3d}")
    print(f"  Verification failed:{verification_failed_count:3d}")
    print(f"  Errors:             {error_count:3d}")
    print(f"  Skipped:            {skipped_count:3d}")
    print(f"  Rejected:           {rejected_count:3d}")
    print(f"  ---")
    print(f"  Effective kill rate: {effective_kills}/{total} ({effective_kills*100//total if total else 0}%)")

    summary = {
        "total": len(results),
        "success": success_count,
        "already_killed": already_killed_count,
        "verification_failed": verification_failed_count,
        "error": error_count,
        "skipped": skipped_count,
        "rejected": rejected_count,
        "results": results,
    }

    # Log summary
    logger.log_summary(summary)
    if phase_logger:
        phase_logger.log_summary(summary)

    return summary
