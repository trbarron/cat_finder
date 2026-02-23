#!/usr/bin/env python3
"""Wrapper to run mutmut with print/logging calls excluded from mutation."""

import mutmut.file_mutation as fm

# Add print and logging calls to the skip list so mutmut
# never mutates arguments inside these calls.
fm.NEVER_MUTATE_FUNCTION_CALLS = fm.NEVER_MUTATE_FUNCTION_CALLS | {
    "print",
    "logging",
}

from mutmut.__main__ import cli

cli()
