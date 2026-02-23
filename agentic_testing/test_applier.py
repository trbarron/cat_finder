#!/usr/bin/env python3
"""
Apply generated test code to test files.
"""

import re
from pathlib import Path


def find_test_class(content: str, class_name: str) -> tuple[int, int] | None:
    """
    Find the start and end line numbers of a test class.

    Returns:
        (start_line, end_line) as 0-indexed line numbers, or None if not found
    """
    lines = content.split("\n")

    # Find class definition
    class_pattern = re.compile(rf"^class {re.escape(class_name)}\(.*\):")
    start_line = None

    for i, line in enumerate(lines):
        if class_pattern.match(line):
            start_line = i
            break

    if start_line is None:
        return None

    # Find end of class (next class definition or end of file)
    end_line = len(lines)
    base_indent = len(lines[start_line]) - len(lines[start_line].lstrip())

    for i in range(start_line + 1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            continue
        current_indent = len(line) - len(line.lstrip())
        # If we hit something at the same or lower indentation, class has ended
        if current_indent <= base_indent and line.strip():
            end_line = i
            break

    return (start_line, end_line)


def insert_test_method(
    content: str, class_name: str, test_method_code: str
) -> tuple[bool, str, str]:
    """
    Insert a test method into a test class.

    Args:
        content: Full content of the test file
        class_name: Name of the test class to insert into
        test_method_code: The test method code (with proper indentation)

    Returns:
        (success, new_content, error_message)
    """
    class_location = find_test_class(content, class_name)
    if class_location is None:
        return (False, content, f"Could not find class {class_name}")

    start_line, end_line = class_location
    lines = content.split("\n")

    # Insert the new method at the end of the class
    # Find the last non-empty line in the class
    insert_line = end_line
    for i in range(end_line - 1, start_line, -1):
        if lines[i].strip():
            insert_line = i + 1
            break

    # Add blank line before the new method if needed
    if insert_line > 0 and lines[insert_line - 1].strip():
        new_lines = (
            lines[:insert_line] + [""] + [test_method_code] + lines[insert_line:]
        )
    else:
        new_lines = lines[:insert_line] + [test_method_code] + lines[insert_line:]

    return (True, "\n".join(new_lines), "")


def apply_test(
    test_file_path: Path,
    test_class: str,
    test_method_code: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Apply a generated test to a test file.

    Args:
        test_file_path: Path to the test file
        test_class: Name of the test class to add the method to
        test_method_code: The generated test method code
        dry_run: If True, don't actually write the file

    Returns:
        (success, message)
    """
    if not test_file_path.exists():
        return (False, f"Test file not found: {test_file_path}")

    try:
        content = test_file_path.read_text()
    except Exception as e:
        return (False, f"Error reading test file: {e}")

    success, new_content, error = insert_test_method(content, test_class, test_method_code)

    if not success:
        return (False, error)

    if dry_run:
        return (True, f"[DRY RUN] Would write to {test_file_path}")

    try:
        test_file_path.write_text(new_content)
        return (True, f"Successfully added test to {test_file_path}")
    except Exception as e:
        return (False, f"Error writing test file: {e}")


if __name__ == "__main__":
    # Example usage
    example_content = """import unittest

class TestGetLabel(unittest.TestCase):
    def setUp(self):
        self.labels = ["neither", "checo", "tuni"]

    def test_valid_index(self):
        self.assertEqual(get_label(self.labels, 0), "neither")

class TestOther(unittest.TestCase):
    def test_something(self):
        pass
"""

    example_test = """    def test_index_equal_to_length(self):
        \"\"\"Test that index equal to length returns 'unknown'.\"\"\"
        idx = len(self.labels)
        self.assertEqual(get_label(self.labels, idx), "unknown")"""

    success, new_content, error = insert_test_method(
        example_content, "TestGetLabel", example_test
    )

    if success:
        print("Success!")
        print(new_content)
    else:
        print(f"Error: {error}")
