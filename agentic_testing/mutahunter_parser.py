#!/usr/bin/env python3
"""
Parser for mutahunter output format.

Mutahunter creates individual mutant files in logs/_latest/mutants/
and logs test results in logs/_latest/debug.log.

This parser extracts survived mutants and converts them to a format
compatible with the triage pipeline.
"""

import re
import subprocess
from pathlib import Path
from typing import Any


def parse_mutahunter_results(
    package_dir: Path, source_file: str = "cat_finder.py"
) -> list[dict[str, Any]]:
    """
    Parse mutahunter results and extract survived mutants.

    Args:
        package_dir: Root package directory
        source_file: Original source file that was mutated

    Returns:
        List of mutant entries with structure:
        {
            'mutant_id': 'hash_sourcefile',
            'mutant_file': Path to mutant file,
            'diff': Unified diff,
            'status': 'survived' or 'killed',
            'source_file': Original source file,
            'line_no': Line number (if extractable),
        }
    """
    logs_dir = package_dir / "logs" / "_latest"
    mutants_dir = logs_dir / "mutants"
    debug_log = logs_dir / "debug.log"

    if not mutants_dir.exists():
        print(f"No mutants directory found at {mutants_dir}")
        return []

    # Get all mutant files
    mutant_files = sorted(mutants_dir.glob(f"*_{source_file}"))

    if not mutant_files:
        print(f"No mutant files found in {mutants_dir}")
        return []

    print(f"Found {len(mutant_files)} mutant file(s)")

    # Parse debug.log to determine which mutants survived
    survived_mutants = set()
    killed_mutants = set()

    if debug_log.exists():
        log_content = debug_log.read_text()

        # Extract test results for each mutant
        # Mutahunter logs lines like: "Testing mutant: 009e1b04_cat_finder.py"
        # Followed by: "PASSED" or "FAILED"
        lines = log_content.splitlines()
        current_mutant = None

        for line in lines:
            # Look for mutant testing lines
            match = re.search(r"Testing mutant[:\s]+([a-f0-9]+)_", line)
            if match:
                current_mutant = match.group(1)
                continue

            # Check test results
            if current_mutant:
                # If tests pass with mutation, the mutant survived
                if "PASSED" in line or "passed" in line.lower():
                    # Check if it's the final result (not individual test passes)
                    if re.search(r"\d+\s+passed", line, re.IGNORECASE):
                        survived_mutants.add(current_mutant)
                        current_mutant = None
                elif "FAILED" in line or "failed" in line.lower():
                    if re.search(r"\d+\s+failed", line, re.IGNORECASE):
                        killed_mutants.add(current_mutant)
                        current_mutant = None

    # If we can't determine from logs, assume all survived (conservative approach)
    if not survived_mutants and not killed_mutants:
        print("Warning: Could not determine mutant status from logs")
        print("Assuming all mutants survived (conservative approach)")
        survived_mutants = {f.stem.split("_")[0] for f in mutant_files}

    print(f"  Survived: {len(survived_mutants)}")
    print(f"  Killed:   {len(killed_mutants)}")

    # Build mutant entries
    mutant_entries = []
    original_source = package_dir / source_file

    if not original_source.exists():
        print(f"Warning: Original source not found: {original_source}")
        return []

    original_content = original_source.read_text()

    for mutant_file in mutant_files:
        mutant_hash = mutant_file.stem.split("_")[0]

        # Only include survived mutants
        if mutant_hash not in survived_mutants:
            continue

        mutant_content = mutant_file.read_text()

        # Generate diff
        diff = generate_diff(original_content, mutant_content, source_file, mutant_hash)

        # Try to extract line number from diff
        line_no = extract_line_number_from_diff(diff)

        entry = {
            "mutant_id": f"{mutant_hash}_{source_file}",
            "mutant_file": str(mutant_file),
            "diff": diff,
            "status": "survived",
            "source_file": source_file,
            "line_no": line_no,
            "mangled_name": mutant_hash,  # For compatibility with triage
        }

        mutant_entries.append(entry)

    print(f"Returning {len(mutant_entries)} survived mutant(s) for triage")
    return mutant_entries


def generate_diff(original: str, mutated: str, filename: str, mutant_id: str) -> str:
    """Generate a unified diff between original and mutated content."""
    from difflib import unified_diff

    original_lines = original.splitlines(keepends=True)
    mutated_lines = mutated.splitlines(keepends=True)

    diff_lines = unified_diff(
        original_lines,
        mutated_lines,
        fromfile=f"original/{filename}",
        tofile=f"mutant/{mutant_id}_{filename}",
        lineterm="",
    )

    return "".join(diff_lines)


def extract_line_number_from_diff(diff: str) -> int | None:
    """Extract the first changed line number from a unified diff."""
    # Look for @@ -X,Y +A,B @@ pattern
    match = re.search(r"@@\s+-(\d+)", diff)
    if match:
        return int(match.group(1))
    return None
