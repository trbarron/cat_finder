#!/usr/bin/env python3
"""
Verify that generated tests actually kill specific mutants.
"""

import shutil
import subprocess
import sys
import tempfile
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
    mutation_engine: str = "mutmut",
    mutant_file_path: str | None = None,
) -> tuple[bool, str]:
    """
    Verify that a newly added test kills a specific mutant.

    Proper verification:
    1. Run test against ORIGINAL code → should PASS
    2. Run test against MUTANT code → should FAIL (mutant killed!)

    Args:
        mutant_id: The mutant ID
        test_file_path: Path to the test file that was modified
        package_dir: Root directory of the package
        mutmut_bin: Path to mutmut binary (optional, for mutmut engine)
        mutation_engine: "mutmut" or "mutahunter"
        mutant_file_path: Path to mutant file (for mutahunter)

    Returns:
        (success, message)
    """
    # Step 1: Verify new test passes with ORIGINAL code
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file_path), "-v", "-x"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        full_output = f"{result.stdout}\n{result.stderr}"

        if result.returncode != 0:
            # Test failed against original code - this is bad!
            return (False, f"Test FAILED against original code (should pass):\n{full_output}")

    except Exception as e:
        return (False, f"Error running pytest on original: {e}")

    # Step 2: Verify test FAILS with MUTANT code

    if mutation_engine == "mutahunter":
        return _verify_mutahunter_mutant(mutant_id, mutant_file_path, test_file_path, package_dir)
    else:
        return _verify_mutmut_mutant(mutant_id, test_file_path, package_dir, mutmut_bin)


def _verify_mutahunter_mutant(
    mutant_id: str,
    mutant_file_path: str | None,
    test_file_path: Path,
    package_dir: Path,
) -> tuple[bool, str]:
    """Verify mutahunter mutant by temporarily replacing source file."""
    if not mutant_file_path:
        error_msg = f"ERROR: No mutant file path provided for {mutant_id}"
        print(f"      {error_msg}")
        return (False, error_msg)

    mutant_path = Path(mutant_file_path)
    if not mutant_path.exists():
        error_msg = f"ERROR: Mutant file not found: {mutant_path}"
        print(f"      {error_msg}")
        return (False, error_msg)

    # Extract source file name from mutant filename (e.g., "hash_cat_finder.py" -> "cat_finder.py")
    source_filename = "_".join(mutant_path.name.split("_")[1:])
    source_path = package_dir / source_filename

    if not source_path.exists():
        return (False, f"Source file not found: {source_path}")

    # Create backup
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as backup:
        backup_path = Path(backup.name)
        backup.write(source_path.read_text())

    try:
        # Replace source with mutant
        shutil.copy2(mutant_path, source_path)

        # Run test against mutant
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file_path), "-v", "-x"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Restore original
        shutil.copy2(backup_path, source_path)
        backup_path.unlink()

        if result.returncode == 0:
            # Test passed with mutant - mutant survived!
            return (False, f"Test PASSED with mutant (should fail). Mutant SURVIVED:\n{result.stdout[:500]}")

        # Test failed with mutant - mutant killed!
        return (True, "✓ MUTANT KILLED!")

    except Exception as e:
        # Restore original in case of error
        error_msg = f"Error during mutant verification: {e}"
        print(f"      ERROR: {error_msg}")
        if backup_path.exists():
            try:
                shutil.copy2(backup_path, source_path)
                backup_path.unlink()
                print(f"      → Original file restored after error")
            except:
                pass
        return (False, error_msg)


def _verify_mutmut_mutant(
    mutant_id: str,
    test_file_path: Path,
    package_dir: Path,
    mutmut_bin: str | None = None,
) -> tuple[bool, str]:
    """Verify mutmut mutant by applying it and running tests."""
    if mutmut_bin is None:
        # Try to find mutmut in venv
        venv_mutmut = package_dir / "../../.venv/bin/mutmut"
        if venv_mutmut.exists():
            mutmut_bin = str(venv_mutmut.resolve())
        else:
            mutmut_bin = "mutmut"

    try:
        # Apply the mutant
        apply_result = subprocess.run(
            [mutmut_bin, "apply", mutant_id],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if apply_result.returncode != 0:
            return (False, f"Failed to apply mutant {mutant_id}: {apply_result.stderr}")

        print(f"      → Applied mutant: {mutant_id}")

        # Run test against mutant
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file_path), "-v", "-x"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        test_failed = result.returncode != 0

        # Restore original (mutmut stores backup)
        subprocess.run(
            [mutmut_bin, "apply", "--restore"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if not test_failed:
            # Test passed with mutant - mutant survived!
            return (False, f"Test PASSED against mutant (should fail). Mutant SURVIVED:\n{result.stdout}")

        # Test failed with mutant - mutant killed!
        print("      → Test fails with mutant code ✓ MUTANT KILLED!")
        return (True, f"✓ Test passes with original, fails with mutant. MUTANT KILLED!")

    except Exception as e:
        # Try to restore original
        try:
            subprocess.run(
                [mutmut_bin, "apply", "--restore"],
                cwd=package_dir,
                capture_output=True,
                timeout=30,
            )
        except:
            pass
        return (False, f"Error during mutant verification: {e}")


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
