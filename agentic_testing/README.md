# Agentic Testing Loop

LLM-powered mutation testing system inspired by Meta's **ACH (Automated Compliance Hardening)**.

## Overview

Instead of manually analyzing survived mutants, this system uses an **agentic loop** to:
1. Generate mutations (using mutmut)
2. **Triage** survived mutants with LLM (filter out non-critical ones)
3. **Generate tests** automatically with LLM
4. **Apply tests** to test files
5. **Verify** tests work
6. **Iterate** until coverage improves

## Key Difference from Traditional Mutation Testing

| Traditional Mutation Testing | Agentic Testing (ACH-inspired) |
|------------------------------|-------------------------------|
| Run mutations → manually analyze survivors | Run mutations → **LLM triages** → **LLM generates tests** |
| Time-consuming manual work | Automated with human-in-the-loop option |
| Generic rule-based mutations | Same mutations, but **smarter test generation** |
| Post-hoc analysis | **Proactive test generation** |

## Workflow

```
┌─────────────────────────────────────────────────────────────┐
│  1. Run mutmut                                              │
│     → Generate mutations using mutmut                       │
│     → Identify survived mutants                             │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  2. LLM Triage (with filtering criteria)                    │
│     → Analyze each survived mutant                          │
│     → Filter out: print statements, cosmetic changes, etc.  │
│     → Only keep mutants that affect correctness             │
│     → Generate agent_prompt for test generation             │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  3. LLM Test Generation                                     │
│     → For each triaged mutant:                              │
│       - LLM reads existing tests                            │
│       - LLM generates new test method                       │
│       - Test targets specific mutation                      │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  4. Apply Tests                                             │
│     → Insert generated test into appropriate test class     │
│     → Maintain proper formatting and style                  │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  5. Verify Tests                                            │
│     → Run pytest to ensure test passes                      │
│     → (Future: Re-run mutmut to confirm mutant is killed)   │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  6. Human Approval or Auto-Commit                           │
│     → Interactive: User reviews each test                   │
│     → Auto: Commit all tests and create PR                  │
└─────────────────────────────────────────────────────────────┘
```

## Usage

### Prerequisites

```bash
# Install dependencies
pip install mutmut pytest

# Set OpenAI API key
export OPENAI_API_KEY="your-api-key"
```

### Interactive Mode (Recommended)

```bash
# Run with human approval for each test
python -m agentic_testing.cli
```

For each mutant, you'll be prompted:
```
Generated test:
   Class: TestGetLabel
   Method: test_index_equal_to_length
   Explanation: Verifies bounds checking for edge case

   Code:
       def test_index_equal_to_length(self):
           """Test that index equal to length returns 'unknown'."""
           idx = len(self.labels)
           self.assertEqual(get_label(self.labels, idx), "unknown")

Approve this test? [y/n/skip/quit]:
```

### Auto Mode

```bash
# Automatically generate tests and create PR
python -m agentic_testing.cli --auto
```

### Other Options

```bash
# Limit to first 5 mutants (for testing)
python -m agentic_testing.cli --limit 5

# Dry run (don't modify files)
python -m agentic_testing.cli --dry-run

# Skip mutmut run (use existing results)
python -m agentic_testing.cli --skip-mutmut

# Skip triage (use cached triage results)
python -m agentic_testing.cli --skip-triage
```

## Architecture

```
agentic_testing/
├── __init__.py          # Package initialization
├── __main__.py          # Module entry point
├── cli.py               # Command-line interface
├── agent_loop.py        # Main orchestrator
├── test_generator.py    # LLM test code generation
├── test_applier.py      # Insert tests into files
├── verifier.py          # Verify tests work
└── README.md            # This file

# Reuses existing:
mutation_testing/
├── run_mutmut.py                  # mutmut wrapper
└── analyze_survived_mutants.py    # LLM triage
```

## Triage Filtering Criteria

The LLM triage step (from `analyze_survived_mutants.py`) filters out mutations that don't need tests:

❌ **DO NOT test:**
- Mutations inside `print()`, `logging.*`, `logger.*`, `warnings.warn()`
- String formatting differences in log/error messages
- Cosmetic changes (variable names in messages)
- Equivalent mutations with same observable behavior

✅ **DO test:**
- Changes that affect **return values**
- Changes that affect **stored values** (database, file, state)
- Changes that affect **control flow** (conditionals, loops)
- Changes that affect **externally visible side effects**

## Example

Given a survived mutant that changes:
```python
def get_label(labels, idx: int) -> str:
    if idx < 0 or idx >= len(labels):  # Original
        return "unknown"
```

to:
```python
def get_label(labels, idx: int) -> str:
    if idx < 0 or idx > len(labels):   # Mutated (>= changed to >)
        return "unknown"
```

The agentic loop will:
1. **Triage**: Confirm this affects correctness (boundary check)
2. **Generate**: Create test for `idx == len(labels)` edge case
3. **Apply**: Add test to `TestGetLabel` class
4. **Verify**: Run test to ensure it passes
5. **Result**: Mutant is killed!

## Future Improvements

- [ ] Full mutmut re-verification (currently just runs pytest)
- [ ] Use mutahunter for smarter mutation generation
- [ ] Parallel test generation for multiple mutants
- [ ] Better error recovery and rollback
- [ ] Integration with CI/CD pipelines
- [ ] Support for other mutation testing tools

## Inspiration

This system is inspired by Meta's **ACH (Automated Compliance Hardening)** approach described in their engineering blog:
- [Revolutionizing software testing: LLM-powered bug catchers at Meta](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)

Key insights from Meta's ACH:
1. **Target-driven**: Focus on specific fault concerns, not generic coverage
2. **LLM-powered**: Use LLMs for contextual analysis and generation
3. **Verification**: Ensure tests actually catch the faults they target
4. **Practical**: Deployed at scale (FB Feed, Instagram, Messenger, WhatsApp)
