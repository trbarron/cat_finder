#!/usr/bin/env python3
"""
Extract error messages from pytest output.
"""

import re


def extract_pytest_error(pytest_output: str) -> dict:
    """
    Extract the relevant error information from pytest output.

    Args:
        pytest_output: Full pytest output string

    Returns:
        dict with:
            - error_type: str (e.g., "AssertionError", "AttributeError")
            - error_message: str (the main error message)
            - full_trace: str (the full traceback)
            - test_name: str (name of the failing test)
    """
    # Extract test name from FAILED line
    test_name = "unknown"
    test_match = re.search(r"FAILED.*::(.*?) -", pytest_output)
    if test_match:
        test_name = test_match.group(1)

    # Extract error type and message from last line of traceback
    error_type = "Error"
    error_message = ""

    # Look for the error in the short test summary
    error_match = re.search(
        r"(AssertionError|AttributeError|TypeError|ValueError|KeyError|ImportError|NameError|RuntimeError|SyntaxError): (.+)",
        pytest_output,
    )
    if error_match:
        error_type = error_match.group(1)
        error_message = error_match.group(2)

    # Extract full traceback (between test name and short summary)
    full_trace = ""
    trace_match = re.search(
        r"(_{10,}.*?_{10,}.*?short test summary)",
        pytest_output,
        re.DOTALL,
    )
    if trace_match:
        full_trace = trace_match.group(1)
    else:
        # Fallback: try to get everything after FAILURES
        trace_match = re.search(r"={3,} FAILURES ={3,}(.+?)={3,}", pytest_output, re.DOTALL)
        if trace_match:
            full_trace = trace_match.group(1)

    # If no error message extracted, try to get it from the full trace
    if not error_message and full_trace:
        # Look for lines starting with 'E   '
        e_lines = [line for line in full_trace.split("\n") if line.startswith("E   ")]
        if e_lines:
            error_message = "\n".join(e_lines)

    return {
        "error_type": error_type,
        "error_message": error_message.strip(),
        "full_trace": full_trace.strip(),
        "test_name": test_name,
    }


if __name__ == "__main__":
    # Test with sample output
    sample_output = """
=================================== FAILURES ===================================
______________ TestAddToUrlDynamodb.test_timestamp_format_in_url _______________

self = <test_cat_finder.TestAddToUrlDynamodb testMethod=test_timestamp_format_in_url>

    def test_timestamp_format_in_url(self):
        mock_table = MagicMock()
        expected_uuid = 'fixed-uuid'
        with patch('cat_finder.uuid') as mock_uuid:
            mock_uuid.uuid4.return_value = expected_uuid
            url = mock_table.put_item.call_args[1]['Item']['URL']
            pattern = r'\\d{4}-\\d{2}-\\d{2}_\\d{2}-\\d{2}-\\d{2}_\\w{8}-\\w{4}-\\w{4}-\\w{4}-\\w{12}'
>           self.assertRegex(url, pattern)
E           AssertionError: Regex didn't match: '\\d{4}-\\d{2}-\\d{2}_\\d{2}-\\d{2}-\\d{2}_\\w{8}-\\w{4}-\\w{4}-\\w{4}-\\w{12}' not found in '2020-01-02_03-04-05_fixed-uuid'

test_cat_finder.py:246: AssertionError
=========================== short test summary info ============================
FAILED test_cat_finder.py::TestAddToUrlDynamodb::test_timestamp_format_in_url
    """

    result = extract_pytest_error(sample_output)
    print(f"Error type: {result['error_type']}")
    print(f"Error message: {result['error_message']}")
    print(f"Test name: {result['test_name']}")
