"""
Agentic testing loop inspired by Meta's ACH (Automated Compliance Hardening).

Workflow:
1. Run mutmut to generate mutations
2. LLM triages survived mutants (filters out non-critical ones)
3. LLM generates test code for critical mutations
4. Apply tests to test files
5. Verify tests kill mutants
6. Iterate until coverage improves
7. Human approval or auto-commit to PR
"""

__version__ = "0.1.0"
