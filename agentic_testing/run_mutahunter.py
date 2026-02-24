#!/usr/bin/env python3
"""
Wrapper to run mutahunter for LLM-powered mutation generation.

Requires Python 3.11 and the venv setup.
"""

import os
import subprocess
import sys
from pathlib import Path


def run_mutahunter(
    source_file: str,
    test_file: str,
    test_command: str,
    model: str = "gpt-4o-mini",
    venv_path: Path = None,
) -> bool:
    """
    Run mutahunter to generate LLM-powered mutations.

    Args:
        source_file: Path to source code file to mutate
        test_file: Path to test file
        test_command: Command to run tests (e.g., "python -m pytest test.py -v")
        model: LLM model for mutation generation
        venv_path: Path to venv (auto-detected if None)

    Returns:
        True if successful
    """
    # Auto-detect venv
    if venv_path is None:
        venv_path = Path(__file__).parent / "venv"

    mutahunter_bin = venv_path / "bin" / "mutahunter"

    if not mutahunter_bin.exists():
        print(f"Error: mutahunter not found at {mutahunter_bin}", file=sys.stderr)
        print("\nTo install mutahunter in venv:", file=sys.stderr)
        print(f"  {venv_path}/bin/pip install git+https://github.com/codeintegrity-ai/mutahunter.git", file=sys.stderr)
        return False

    cmd = [
        str(mutahunter_bin),
        "run",
        "--source-path", source_file,
        "--test-path", test_file,
        "--test-command", test_command,
        "--model", model,
    ]

    try:
        # gpt-5-mini only supports temperature=1; tell litellm to drop unsupported params
        env = {**os.environ, "LITELLM_DROP_PARAMS": "true"}

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes for LLM calls
            env=env,
        )

        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print("Error: mutahunter timed out after 10 minutes", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error running mutahunter: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run mutahunter for LLM-powered mutations")
    parser.add_argument("--source", required=True, help="Source file to mutate")
    parser.add_argument("--test", required=True, help="Test file")
    parser.add_argument("--test-command", required=True, help="Command to run tests")
    parser.add_argument("--model", default="gpt-5-mini", help="LLM model")
    parser.add_argument("--venv", type=Path, help="Path to venv (auto-detected if omitted)")

    args = parser.parse_args()

    success = run_mutahunter(
        args.source,
        args.test,
        args.test_command,
        args.model,
        args.venv,
    )
    sys.exit(0 if success else 1)
