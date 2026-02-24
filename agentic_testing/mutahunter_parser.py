#!/usr/bin/env python3
"""
Parser for mutahunter output format.

Mutahunter creates individual mutant files in logs/_latest/mutants/
and logs test results in logs/_latest/debug.log.

This parser extracts survived mutants and converts them to a format
compatible with the triage pipeline.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _test_mutant_against_suite(
    mutant_file: Path, source_file: Path, test_file: Path, package_dir: Path
) -> bool:
    """Run the test suite with a mutant swapped in. Returns True if the mutant is killed."""
    # Back up original
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as backup:
        backup_path = Path(backup.name)
        backup.write(source_file.read_text())

    try:
        shutil.copy2(mutant_file, source_file)

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-x", "-q"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        return result.returncode != 0  # non-zero = tests failed = mutant killed

    except Exception:
        return False  # assume survived on error
    finally:
        shutil.copy2(backup_path, source_file)
        backup_path.unlink()


def parse_mutahunter_results(
    package_dir: Path, source_file: str = "cat_finder.py", max_survived: int = 30,
    phase_logger=None,
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
    if phase_logger:
        phase_logger.log_parsing(f"Found {len(mutant_files)} mutant file(s) in {mutants_dir}")

    # Parse debug.log to determine which mutants survived
    survived_mutants = set()
    killed_mutants = set()
    tested_mutants = set()

    if debug_log.exists():
        log_content = debug_log.read_text()
        lines = log_content.splitlines()

        # Mutahunter log format:
        #   INFO: 'pytest ...' - '/path/to/mutants/HASH_cat_finder.py'
        #   INFO: Mutant killed / Mutant survived
        current_mutant = None

        for line in lines:
            # Match the test command line that contains the mutant file path
            path_match = re.search(r"INFO:.*mutants/([a-f0-9]+)_", line)
            if path_match:
                current_mutant = path_match.group(1)
                continue

            if current_mutant:
                if "Mutant killed" in line:
                    killed_mutants.add(current_mutant)
                    tested_mutants.add(current_mutant)
                    current_mutant = None
                elif "Mutant survived" in line:
                    survived_mutants.add(current_mutant)
                    tested_mutants.add(current_mutant)
                    current_mutant = None

    # For mutant files that were generated but never tested (e.g. mutahunter
    # crashed partway through), run tests against them now to determine status.
    all_hashes = {f.stem.split("_")[0] for f in mutant_files}
    untested = all_hashes - tested_mutants

    if untested:
        print(f"  Tested by mutahunter: {len(tested_mutants)}")
        print(f"  Untested: {len(untested)} -- running tests now...")
        total_survived_so_far = len(survived_mutants)
        if total_survived_so_far >= max_survived:
            print(f"  Already have {total_survived_so_far} survived mutants (>= {max_survived}), skipping untested")
        else:
            test_file_path = package_dir / "test_cat_finder.py"
            untested_killed = 0
            untested_survived = 0
            skipped = 0

            for i, mhash in enumerate(sorted(untested), 1):
                # Find the mutant file for this hash
                matching = [f for f in mutant_files if f.stem.split("_")[0] == mhash]
                if not matching:
                    continue
                mfile = matching[0]

                killed = _test_mutant_against_suite(mfile, package_dir / source_file, test_file_path, package_dir)
                if killed:
                    killed_mutants.add(mhash)
                    untested_killed += 1
                else:
                    survived_mutants.add(mhash)
                    untested_survived += 1

                if i % 20 == 0:
                    print(f"    ... tested {i}/{len(untested)} (killed: {untested_killed}, survived: {untested_survived})")

                if len(survived_mutants) >= max_survived:
                    skipped = len(untested) - i
                    print(f"  Reached {max_survived} survived mutants, skipping remaining {skipped} untested")
                    break

            msg = f"  Untested results: {untested_killed} killed, {untested_survived} survived{f', {skipped} skipped' if skipped else ''}"
            print(msg)
            if phase_logger:
                phase_logger.log_parsing(msg)

    if not tested_mutants and not untested:
        print("Warning: No mutant files or log entries found")

    print(f"  Survived: {len(survived_mutants)}")
    print(f"  Killed:   {len(killed_mutants)}")

    # Build mutant entries
    mutant_entries = []
    original_source = package_dir / source_file

    if not original_source.exists():
        print(f"Warning: Original source not found: {original_source}")
        return []

    original_content = original_source.read_text()

    syntax_errors = 0
    for mutant_file in mutant_files:
        mutant_hash = mutant_file.stem.split("_")[0]

        # Only include survived mutants
        if mutant_hash not in survived_mutants:
            continue

        mutant_content = mutant_file.read_text()

        # Syntax check: compile the mutant to catch invalid mutations
        try:
            compile(mutant_content, str(mutant_file), "exec")
        except SyntaxError:
            syntax_errors += 1
            # Remove the broken mutant file so it doesn't waste time in future runs
            mutant_file.unlink()
            continue

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

    if syntax_errors:
        print(f"  Syntax errors: {syntax_errors} (deleted)")
    print(f"Returning {len(mutant_entries)} survived mutant(s) for triage")
    if phase_logger:
        phase_logger.log_parsing(
            f"Final: {len(killed_mutants)} killed, {len(survived_mutants)} survived, "
            f"{syntax_errors} syntax errors, {len(mutant_entries)} sent to triage"
        )
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
