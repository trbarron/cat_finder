#!/usr/bin/env python3
"""
Verify that generated tests actually kill specific mutants.
"""

import subprocess
import sys
from pathlib import Path


def run_mutmut_on_mutant(
    mutant_id: str, package_dir: Path, mutmut_bin: str | None = None
) -> tuple[bool, str]:
    """
    Run tests against a specific mutant to verify it's killed.

    Args:
        mutant_id: The mutant ID to test
        package_dir: Root directory of the package
        mutmut_bin: Path to mutmut binary (optional)

    Returns:
        (killed, output) where killed is True if mutant was killed
    """
    if mutmut_bin is None:
        # Try to find mutmut in venv
        venv_mutmut = package_dir / "../../.venv/bin/mutmut"
        if venv_mutmut.exists():
            mutmut_bin = str(venv_mutmut.resolve())
        else:
            mutmut_bin = "mutmut"

    cmd = [mutmut_bin, "run", "--test-time-base=10.0", "--paths-to-mutate", "cat_finder.py"]

    try:
        # Run mutmut and capture output
        result = subprocess.run(
            cmd,
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes max
        )

        output = result.stdout + result.stderr

        # Check if the specific mutant was killed
        # We need to check the mutmut results to see if this specific mutant survived
        results_path = package_dir / "mutation_testing" / "mutmut_results.txt"

        if results_path.exists():
            results_content = results_path.read_text()
            # Check if mutant_id is marked as killed
            if f"{mutant_id}: killed" in results_content:
                return (True, output)
            elif f"{mutant_id}: survived" in results_content:
                return (False, output)
            else:
                # Mutant not found in results, assume it wasn't tested
                return (False, f"Mutant {mutant_id} not found in results")

        return (False, "Results file not found")

    except subprocess.TimeoutExpired:
        return (False, "Timeout running mutmut")
    except Exception as e:
        return (False, f"Error running mutmut: {e}")


def verify_test_kills_mutant(
    mutant_id: str,
    test_file_path: Path,
    package_dir: Path,
    mutmut_bin: str | None = None,
) -> tuple[bool, str]:
    """
    Verify that a newly added test kills a specific mutant.

    This is a simpler approach: just run the tests and check if they pass/fail.

    Args:
        mutant_id: The mutant ID
        test_file_path: Path to the test file that was modified
        package_dir: Root directory of the package
        mutmut_bin: Path to mutmut binary (optional)

    Returns:
        (success, message)
    """
    # For now, we'll use a simple verification:
    # 1. Run pytest on the test file to ensure new test passes
    # 2. Run mutmut on just that mutant to verify it's killed

    # Step 1: Verify new test passes
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file_path), "-v"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        full_output = f"{result.stdout}\n{result.stderr}"

        if result.returncode != 0:
            # Return full output for error extraction
            return (False, full_output)

    except Exception as e:
        return (False, f"Error running pytest: {e}")

    # Step 2: For full verification, we'd need to run mutmut again
    # For now, we'll just report success if the test passes
    # TODO: Implement full mutmut verification in future iteration

    return (
        True,
        f"Test passes. Note: Full mutmut verification not yet implemented. "
        f"Run mutmut manually to confirm {mutant_id} is killed.",
    )


if __name__ == "__main__":
    # Example usage
    package_dir = Path(__file__).resolve().parent.parent
    result, msg = verify_test_kills_mutant(
        "cat_finder.get_label__mutmut_1",
        package_dir / "test_cat_finder.py",
        package_dir,
    )
    print(f"Success: {result}")
    print(f"Message: {msg}")
