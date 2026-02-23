#!/usr/bin/env python3
"""
Wrapper to run mutahunter for LLM-powered mutation generation.

MutaHunter generates more realistic, context-aware mutations than rule-based tools.
See: https://github.com/codeintegrity-ai/mutahunter
"""

import subprocess
import sys
from pathlib import Path


def run_mutahunter(
    source_file: str,
    test_file: str,
    output_dir: str = ".agentic_testing_cache",
    model: str = "gpt-4o-mini",
) -> bool:
    """
    Run mutahunter to generate mutations.

    Args:
        source_file: Path to source code file to mutate
        test_file: Path to test file
        output_dir: Directory for mutahunter output
        model: LLM model for mutation generation

    Returns:
        True if successful
    """
    cmd = [
        "mutahunter",
        "run",
        "--source", source_file,
        "--tests", test_file,
        "--output", output_dir,
        "--model", model,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes for LLM calls
        )

        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print("Error: mutahunter timed out after 10 minutes", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("Error: mutahunter not installed. Install with: pip install mutahunter", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error running mutahunter: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # Example usage
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="Source file to mutate")
    parser.add_argument("--tests", required=True, help="Test file")
    parser.add_argument("--output", default=".agentic_testing_cache", help="Output directory")
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM model")

    args = parser.parse_args()

    success = run_mutahunter(args.source, args.tests, args.output, args.model)
    sys.exit(0 if success else 1)
