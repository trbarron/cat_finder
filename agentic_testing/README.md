# Agentic Testing Loop

Automatically generate tests to kill survived mutants using LLM-powered test generation and self-correcting iteration loops.

Inspired by Meta's ACH (Automated Compliance Hardening) paper: [Revolutionizing Software Testing with LLM-Powered Bug Catchers](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)

## Quick Start

```bash
# One-time setup
cd agentic_testing
./setup.sh

# Run from cat_finder directory
cd ..
python3 -m agentic_testing.cli
```

## How It Works

1. **Mutation Generation**: Runs mutmut (rule-based) or mutahunter (LLM-powered)
2. **Mutant Testing**: Parser runs the test suite against each mutant to determine killed vs survived (including any untested mutants from incomplete mutahunter runs)
3. **LLM Triage**: Analyzes survived mutants to filter out false positives (uses gpt-4o-mini)
4. **Pre-Filter**: Bulk-checks all mutants against existing tests upfront, skipping already-killed ones
5. **Test Generation**: Uses gpt-5-mini to generate targeted tests with full source context
6. **Self-Correction**: Automatically fixes failed tests with full traceback context (up to 5 iterations, uses gpt-4o-mini)
7. **Verification**: Confirms tests pass with original code and fail with mutant code

## Setup

### Virtual Environment

The module uses its own venv for dependency isolation. **Python 3.11 required** (mutahunter dependency limitation).

Run the setup script:

```bash
cd agentic_testing
./setup.sh
```

This creates the venv, installs dependencies, installs mutahunter, and applies the mutahunter bug fix.

### Environment Variables

Required: `OPENAI_API_KEY` for LLM calls

Create `.env` in the `cat_finder` directory:
```bash
OPENAI_API_KEY=sk-...
```

## Example Runs

### Mutmut (rule-based, fast)

```bash
# Full interactive run — mutmut generates mutants, you approve each test
python3 -m agentic_testing.cli

# Auto mode — no approval, creates PR at end
python3 -m agentic_testing.cli --auto

# Limit to 5 mutants for a quick test
python3 -m agentic_testing.cli --limit 5 --auto

# Re-run with existing mutmut results (skip mutation step)
python3 -m agentic_testing.cli --skip-mutmut

# Re-run with existing triage too (jump straight to test generation)
python3 -m agentic_testing.cli --skip-mutmut --skip-triage

# Dry run — preview everything without modifying files
python3 -m agentic_testing.cli --dry-run
```

### Mutahunter (LLM-powered, semantic)

```bash
# Full run with mutahunter mutations
python3 -m agentic_testing.cli --mutation-engine mutahunter

# Auto mode with mutahunter
python3 -m agentic_testing.cli --mutation-engine mutahunter --auto

# Limit + skip mutation step (use cached mutahunter results)
python3 -m agentic_testing.cli --mutation-engine mutahunter --skip-mutmut --limit 10

# Custom source/test files
python3 -m agentic_testing.cli --mutation-engine mutahunter --source-file my_module.py --test-file test_my_module.py
```

## CLI Options

```bash
python3 -m agentic_testing.cli [OPTIONS]

Options:
  --auto                    Auto mode: skip human approval, create PR at end
  --dry-run                 Don't modify files or create PR
  --limit N                 Process only first N mutants
  --skip-mutmut             Use existing mutation results
  --skip-triage             Use existing triage results
  --mutation-engine ENGINE  mutmut (rule-based) or mutahunter (LLM-powered)
  --source-file FILE        Source file to mutate (default: cat_finder.py)
  --test-file FILE          Test file (default: test_cat_finder.py)
```

## Modes

### Interactive Mode (Default)
- Human approves each generated test before applying
- Review test code and decide whether to apply
- Safe for exploring and learning

### Auto Mode (`--auto`)
- No human approval needed
- Automatically applies tests that pass verification
- Creates PR with results at the end
- Good for CI/CD pipelines

## Architecture

```
cli.py                  # Main entry point
|-- run_mutmut.py       # Mutmut wrapper (skips print/logging mutations)
|-- run_mutahunter.py   # Mutahunter wrapper (sets LITELLM_DROP_PARAMS for gpt-5 compat)
|-- mutahunter_parser.py # Parse results, syntax-check mutants, test untested mutants
|-- triage.py           # LLM triage (gpt-4o-mini)
|-- agent_loop.py       # Main loop with bulk pre-filter and per-mutant processing
|-- test_generator.py   # Generate tests via LLM (gpt-5-mini, full source context)
|-- test_fixer.py       # Fix failed tests with full traceback (gpt-4o-mini)
|-- error_extractor.py  # Parse pytest errors
|-- test_applier.py     # Apply/remove tests (allows stdlib imports, blocks source imports)
|-- verifier.py         # Verify mutants are killed
|-- phase_logger.py     # Per-phase log file management
```

## LLM Models

| Component | Model | Why |
|-----------|-------|-----|
| Triage | gpt-4o-mini | Simple yes/no classification, cost-effective |
| Test generation | gpt-5-mini | Hardest task, needs to write correct mock-heavy tests |
| Test fixer | gpt-4o-mini | Understands errors + code, cost-effective for iteration |
| Mutahunter | gpt-5-mini | Mutation generation (via LITELLM_DROP_PARAMS=true) |

Note: gpt-5-mini only supports temperature=1. The `LITELLM_DROP_PARAMS` env var is set when running mutahunter to drop unsupported parameters.

## Pipeline Details

### Mutant Parsing and Testing

The mutahunter parser (`mutahunter_parser.py`):
1. Reads mutant files from `logs/_latest/mutants/`
2. Parses `debug.log` for killed/survived status (matches "Mutant killed"/"Mutant survived" lines)
3. **Syntax-checks** each mutant file with `compile()` -- deletes invalid ones from disk
4. **Tests untested mutants** against the current test suite (handles incomplete mutahunter runs)
5. Returns only genuine survivors for triage

### Bulk Pre-Filter

Before the main loop starts, all mutants are checked against existing tests in bulk. This:
- Eliminates stale mutants that the current test suite already kills
- Warns if ALL mutants are already killed (stale mutant list)
- Re-checks during the loop in case a newly written test kills later mutants

### Self-Correcting Iteration

When a generated test fails verification:
1. Extract full traceback from pytest output
2. Pass traceback + test code + source to LLM fixer
3. LLM generates fixed test
4. Apply and verify again
5. Repeat up to 5 times, then roll back

### Test Applier Rules

- Stdlib imports (`import threading`, `from uuid import UUID`, etc.) are **allowed** inside test methods
- `import cat_finder` is **allowed** (for accessing `cat_finder.main()`, etc.)
- `from cat_finder import ...` is **blocked** (should be at top of file where hardware mocks are set up)

### Summary Output

The loop tracks all outcome types:
- **Already killed**: Mutant killed by existing tests (pre-filter or during loop)
- **New tests written**: Successfully generated and verified tests
- **Verification failed**: Test generation failed after max attempts (rolled back)
- **Effective kill rate**: (success + already_killed) / total

## Mutation Engines Comparison

| Feature | mutmut | mutahunter |
|---------|--------|------------|
| **Speed** | Fast (seconds) | Slower (minutes) |
| **Mutations** | Rule-based (syntax) | LLM-powered (semantic) |
| **Setup** | Simple | Requires Python 3.11 + fix |
| **Cost** | Free | ~$0.01-0.05 per file |
| **Use Case** | Quick iteration | Realistic bugs |

## Troubleshooting

**Tests failing after generation?**
- The self-correction loop should fix most issues automatically (up to 5 attempts)
- Use `--dry-run` to preview without applying changes

**No mutants found?**
- Ensure tests are passing first: `pytest test_cat_finder.py`
- Check `.agentic_testing_cache/mutmut_results.txt` for mutation results

**LLM errors?**
- Verify `OPENAI_API_KEY` is set correctly in `.env`
- Check API rate limits and quotas

## References

- [Meta's ACH Paper](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)
- [mutmut Documentation](https://github.com/boxed/mutmut)
