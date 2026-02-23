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

    print(f"\n{'='*80}")
    print(f"Processing mutant: {mutant_id}")
    print(f"{'='*80}")

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

    # Step 1: Generate test code
    print(f"\n1. Generating test code...")
    print(f"   Agent prompt: {agent_prompt[:100]}...")

    # Get source snippet for better context
    source_snippet = mutant_entry.get("source_snippet", "")
    diff = mutant_entry.get("diff", "")

    # Combine diff and source snippet for full context
    full_source_context = f"{diff}\n\n{source_snippet}" if diff and source_snippet else (source_snippet or diff or "")

    test_result = generate_test_code(
        agent_prompt, test_file_path, existing_test_content, api_key, full_source_context
    )

    if not test_result.get("success"):
        error_msg = test_result.get('error', 'Unknown error')
        print(f"\n   ERROR: Test generation failed: {error_msg}")
        return {
            "mutant_id": mutant_id,
            "status": "error",
            "reason": f"Test generation failed: {error_msg}",
        }

    test_class = test_result["test_class"]
    test_method_name = test_result["test_method_name"]
    test_code = test_result["test_code"]
    explanation = test_result["explanation"]

    print(f"\n2. Generated test:")
    print(f"   Class: {test_class}")
    print(f"   Method: {test_method_name}")
    print(f"   Explanation: {explanation}")
    print(f"\n   Code:")
    print("   " + "\n   ".join(test_code.split("\n")))

    # Step 2: Interactive approval
    if interactive and not dry_run:
        print(f"\n3. Approve this test? [y/n/skip/quit]: ", end="")
        response = input().strip().lower()

        if response == "quit" or response == "q":
            return {"mutant_id": mutant_id, "status": "quit", "reason": "User quit"}
        elif response == "skip" or response == "s":
            return {"mutant_id": mutant_id, "status": "skipped", "reason": "User skipped"}
        elif response != "y" and response != "yes":
            return {"mutant_id": mutant_id, "status": "rejected", "reason": "User rejected"}

    # Step 3: Apply test
    print(f"\n4. Applying test to {test_file_path}...")
    apply_success, apply_msg = apply_test(
        test_file_path, test_class, test_code, dry_run=dry_run
    )

    if not apply_success:
        return {
            "mutant_id": mutant_id,
            "status": "error",
            "reason": f"Failed to apply test: {apply_msg}",
        }

    print(f"   {apply_msg}")

    # Step 4: Verify test
    if not dry_run:
        print(f"\n5. Verifying test...")
        verify_success, verify_msg = verify_test_kills_mutant(
            mutant_id, test_file_path, package_dir
        )

        print(f"   {verify_msg}")

        if not verify_success:
            return {
                "mutant_id": mutant_id,
                "status": "verification_failed",
                "reason": verify_msg,
                "test_applied": True,
            }

    return {
        "mutant_id": mutant_id,
        "status": "success",
        "test_class": test_class,
        "test_method_name": test_method_name,
        "explanation": explanation,
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
    print(f"\n\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = sum(1 for r in results if r["status"] == "error")
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
    rejected_count = sum(1 for r in results if r["status"] == "rejected")

    print(f"Total processed: {len(results)}")
    print(f"  Success: {success_count}")
    print(f"  Errors: {error_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Rejected: {rejected_count}")

    return {
        "total": len(results),
        "success": success_count,
        "error": error_count,
        "skipped": skipped_count,
        "rejected": rejected_count,
        "results": results,
    }
