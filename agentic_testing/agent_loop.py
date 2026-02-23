#!/usr/bin/env python3
"""
Main agentic loop for LLM-powered mutation testing.

Inspired by Meta's ACH (Automated Compliance Hardening):
1. Run mutmut → identify survived mutants
2. LLM triages mutants → filters out non-critical ones
3. LLM generates test code for critical mutations
4. Apply tests to test files
5. Verify tests work
6. Iterate
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .test_generator import generate_test_code
from .test_applier import apply_test
from .verifier import verify_test_kills_mutant
from .test_fixer import fix_failed_test
from .error_extractor import extract_pytest_error
import subprocess
import sys


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
        msg = f"""
{'='*80}
FINAL SUMMARY
{'='*80}
Total processed: {summary['total']}
  Success:  {summary['success']}
  Errors:   {summary['error']}
  Skipped:  {summary['skipped']}
  Rejected: {summary['rejected']}
"""
        self.log(msg)

        print(f"\nLog files written to:")
        print(f"  Full log: {self.log_file}")
        print(f"  Generated tests: {self.generated_tests_file}")


def load_triage_results(report_path: Path) -> list[dict[str, Any]]:
    """
    Parse the triage report (from analyze_survived_mutants.py --call-llm)
    and extract mutants that need tests.

    For now, we'll work with the raw analysis data structure.
    In practice, we'll need to load the JSON-formatted results.

    Returns:
        List of mutant entries that should_write_test is True
    """
    # This would load from a JSON export of the triage results
    # For now, returning empty list as placeholder
    return []


def process_mutant(
    mutant_entry: dict[str, Any],
    package_dir: Path,
    api_key: str,
    interactive: bool = True,
    dry_run: bool = False,
    max_iterations: int = 5,
    logger: AgentLogger = None,
    mutation_engine: str = "mutmut",
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

    # Get source snippet for better context
    source_snippet = mutant_entry.get("source_snippet", "")
    diff = mutant_entry.get("diff", "")
    full_source_context = f"{diff}\n\n{source_snippet}" if diff and source_snippet else (source_snippet or diff or "")

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

            test_result = generate_test_code(
                agent_prompt, test_file_path, existing_test_content, api_key, full_source_context
            )
        else:
            # Iteration > 1: Fix the failed test
            print(f"\n1. Fixing test{iteration_suffix}...")
            test_result = fix_failed_test(
                test_code,
                error_message,
                test_class,
                test_method_name,
                full_source_context,
                agent_prompt,
                api_key,
            )

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
                return {
                    "mutant_id": mutant_id,
                    "status": "success",
                    "test_class": test_class,
                    "test_method_name": test_method_name,
                    "explanation": explanation,
                    "iterations": iteration,
                }
            else:
                print(f"   ✗ Verification failed")
                # Extract error for next iteration
                error_info = extract_pytest_error(verify_msg)
                error_message = error_info.get("error_message", verify_msg)

                # Show concise error
                if "FAILED against original" in verify_msg:
                    print(f"   Reason: Test fails with original code")
                elif "PASSED" in verify_msg and "mutant" in verify_msg:
                    print(f"   Reason: Mutant survived (test passes with mutant)")
                else:
                    print(f"   Error: {error_message[:100]}...")

                if iteration == max_iterations:
                    # Exhausted all attempts - roll back the test
                    print(f"\n   ✗ Exhausted {max_iterations} attempts. Rolling back test...")

                    from .test_applier import remove_test

                    rollback_success, rollback_msg = remove_test(
                        test_file_path, test_class, test_method_name
                    )

                    if rollback_success:
                        print(f"   ✓ Test rolled back successfully")
                    else:
                        print(f"   ✗ Failed to roll back test: {rollback_msg}")

                    return {
                        "mutant_id": mutant_id,
                        "status": "verification_failed",
                        "reason": f"Test failed verification after {max_iterations} attempts (rolled back)",
                        "test_applied": False,  # Rolled back
                        "last_error": error_message,
                        "rollback_success": rollback_success,
                    }

                print(f"   → Attempting to fix the test...")
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
    print("\n→ Running sanity check: verifying all tests pass...")
    tests_pass, test_output = check_tests_pass(package_dir)

    if not tests_pass:
        print("✗ SANITY CHECK FAILED: Tests are already broken!")
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

    print("✓ Sanity check passed - all tests passing\n")

    results = []
    for i, mutant_entry in enumerate(triage_results):
        mutant_id = mutant_entry.get("mutant_id", "unknown")
        logger.log_mutant_start(mutant_id, i + 1, len(triage_results))

        # Check if this mutant is already killed by existing tests
        print(f"→ Checking if mutant is already killed by existing tests...")
        mutant_file_path = mutant_entry.get("mutant_file") if mutation_engine == "mutahunter" else None

        if mutation_engine == "mutahunter" and mutant_file_path:
            from .verifier import _verify_mutahunter_mutant
            test_file_path = package_dir / "test_cat_finder.py"

            # Run existing tests against this mutant
            already_killed, msg = _verify_mutahunter_mutant(
                mutant_id, mutant_file_path, test_file_path, package_dir
            )

            if already_killed:
                print(f"✓ Mutant already killed by existing tests - skipping")
                results.append({
                    "mutant_id": mutant_id,
                    "status": "already_killed",
                    "reason": "Mutant already killed by existing tests",
                })
                logger.log_result(mutant_id, "already_killed", "Killed by previous test")
                continue

        result = process_mutant(
            mutant_entry,
            package_dir,
            api_key,
            interactive,
            dry_run,
            logger=logger,
            mutation_engine=mutation_engine,
        )
        results.append(result)

        # Log result
        logger.log_result(
            mutant_id, result["status"], result.get("reason", result.get("explanation", ""))
        )

        # Verify tests still pass after processing this mutant (especially after rollback)
        if result["status"] in ["verification_failed", "error"]:
            print(f"\n   → Verifying tests still pass after rollback...")
            tests_pass, _ = check_tests_pass(package_dir)

            if not tests_pass:
                print(f"   ✗ ERROR: Tests are broken after processing {mutant_id}!")
                print(f"   This means rollback failed or a broken test wasn't rolled back.")
                print(f"   Stopping to prevent further damage.")
                break
            else:
                print(f"   ✓ Tests still pass - rollback successful")

        if result["status"] == "quit":
            print("\nUser requested quit. Stopping.")
            break

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = sum(1 for r in results if r["status"] == "error")
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
    rejected_count = sum(1 for r in results if r["status"] == "rejected")

    total = len(results)
    print(f"Processed:  {total}")
    print(f"  Success:  {success_count:3d} ({success_count*100//total if total else 0}%)")
    print(f"  Errors:   {error_count:3d}")
    print(f"  Skipped:  {skipped_count:3d}")
    print(f"  Rejected: {rejected_count:3d}")

    summary = {
        "total": len(results),
        "success": success_count,
        "error": error_count,
        "skipped": skipped_count,
        "rejected": rejected_count,
        "results": results,
    }

    # Log summary
    logger.log_summary(summary)

    return summary
