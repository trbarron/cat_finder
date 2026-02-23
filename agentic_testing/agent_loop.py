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
from pathlib import Path
from typing import Any

from .test_generator import generate_test_code
from .test_applier import apply_test
from .verifier import verify_test_kills_mutant
from .test_fixer import fix_failed_test
from .error_extractor import extract_pytest_error


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
    max_iterations: int = 3,
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
            if iteration == max_iterations:
                return {
                    "mutant_id": mutant_id,
                    "status": "error",
                    "reason": f"Failed to apply test after {max_iterations} attempts: {apply_msg}",
                }
            print(f"   ERROR: {apply_msg}")
            continue

        print(f"   {apply_msg}")

        # Step 4: Verify test
        if not dry_run:
            step_num += 1
            print(f"\n{step_num}. Verifying test{iteration_suffix}...")
            verify_success, verify_msg = verify_test_kills_mutant(
                mutant_id, test_file_path, package_dir
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
                print(f"   ✗ Test failed")
                # Extract error for next iteration
                error_info = extract_pytest_error(verify_msg)
                error_message = error_info.get("error_message", verify_msg)
                print(f"   Error: {error_message[:200]}...")

                if iteration == max_iterations:
                    return {
                        "mutant_id": mutant_id,
                        "status": "verification_failed",
                        "reason": f"Test failed verification after {max_iterations} attempts",
                        "test_applied": True,
                        "last_error": error_message,
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

    print(f"\n{'='*80}")
    print(f"Agentic Testing Loop - Processing {len(triage_results)} mutant(s)")
    print(f"Mode: {'INTERACTIVE' if interactive else 'AUTO'}")
    print(f"{'='*80}")

    results = []
    for i, mutant_entry in enumerate(triage_results):
        print(f"\n\n[{i+1}/{len(triage_results)}]")

        result = process_mutant(
            mutant_entry, package_dir, api_key, interactive, dry_run
        )
        results.append(result)

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

    return {
        "total": len(results),
        "success": success_count,
        "error": error_count,
        "skipped": skipped_count,
        "rejected": rejected_count,
        "results": results,
    }
