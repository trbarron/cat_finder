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

# With options
python3 -m agentic_testing.cli --limit 5 --auto
python3 -m agentic_testing.cli --mutation-engine mutahunter
```

## Pipeline Phases

Each run creates a timestamped log directory with a separate file per phase:

```
.agentic_testing_cache/logs/run_YYYYMMDD_HHMMSS/
  1_mutation.log      Phase 1: Mutation generation
  2_parsing.log       Phase 2: Mutant parsing and testing
  3_triage.log        Phase 3: LLM triage decisions
  4_prefilter.log     Phase 4: Bulk pre-filter
  5_generation.log    Phase 5: LLM test generation
  6_fixing.log        Phase 6: LLM test fixing
  7_verification.log  Phase 7: Test verification
  summary.log         Final summary and stats
```

### Phase 1: Mutation Generation (`1_mutation.log`)

Generates mutants using either mutmut (rule-based) or mutahunter (LLM-powered).

- **mutmut**: Fast, syntax-level mutations (operator swaps, boundary changes)
- **mutahunter**: Slower, semantic mutations via gpt-5-mini (realistic bugs)

Logs: engine used, source file, raw output from the mutation tool.

### Phase 2: Mutant Parsing (`2_parsing.log`)

Parses mutation results and determines which mutants survived.

For mutahunter (`mutahunter_parser.py`):
1. Reads mutant files from `logs/_latest/mutants/`
2. Parses `debug.log` for killed/survived status ("Mutant killed"/"Mutant survived")
3. **Syntax-checks** each mutant with `compile()` -- deletes invalid ones
4. **Tests untested mutants** against the test suite (handles incomplete runs)
5. **Early exit**: Stops testing once `max_survived` (default: 30) survivors are found
6. Returns genuine survivors for triage

Logs: file counts, test results per mutant, final killed/survived/syntax error counts.

### Phase 3: LLM Triage (`3_triage.log`)

Analyzes each survived mutant with gpt-4o-mini to decide if a test is worth writing.

Filters out:
- Print/logging mutations
- Cosmetic/string changes
- Equivalent mutations (same observable behavior)
- Mutations where existing tests already cover the branch

For each mutant, logs the full LLM prompt and response including `should_write_test`, `reason`, and `agent_prompt`.

### Phase 4: Bulk Pre-Filter (`4_prefilter.log`)

Before the main loop, checks all triaged mutants against the current test suite.

- Eliminates stale mutants already killed by existing tests
- Warns if ALL mutants are already killed (stale mutant list)
- Re-checks during the loop in case a newly written test kills later mutants

Logs: each mutant's killed/survived status.

### Phase 5: Test Generation (`5_generation.log`)

Uses gpt-5-mini to generate targeted tests with full source context.

For each mutant:
- Sends the `agent_prompt` from triage, full source file, existing test content, and available imports
- Receives a test method with class name, method name, code, and explanation

Logs: the agent prompt sent and the full LLM response (including generated code).

### Phase 6: Test Fixing (`6_fixing.log`)

When a generated test fails verification, uses gpt-4o-mini to fix it (up to 5 iterations).

Each iteration:
1. Extracts full traceback from pytest output
2. Sends traceback + test code + source to LLM fixer
3. Applies the fix and re-verifies

Logs: the error message sent and the fix response for each attempt.

### Phase 7: Verification (`7_verification.log`)

Confirms each test:
1. **PASSES** with original code (the test is correct)
2. **FAILS** with mutant code (the test kills the mutant)

If verification fails, the test enters the fixing loop (Phase 6). After 5 failed attempts, the test is rolled back.

Logs: pass/fail status and output for each verification attempt.

### Summary (`summary.log`)

Final stats as JSON:
- Total processed, already killed, new tests written
- Verification failed, errors, skipped, rejected
- Effective kill rate: (success + already_killed) / total

## Setup

### Virtual Environment

The module uses its own venv for dependency isolation. **Python 3.11 required** (mutahunter dependency limitation):

```bash
cd agentic_testing

# Create venv with Python 3.11
python3.11 -m venv venv

# Install core dependencies
./venv/bin/pip install -r requirements.txt

# Install mutahunter (LLM-powered mutations)
./venv/bin/pip install git+https://github.com/codeintegrity-ai/mutahunter.git

# Apply bug fix (one-line patch for AttributeError)
sed -i '' '51 a\
        self.unexpected_test_error_mutants = 0
' ./venv/lib/python3.11/site-packages/mutahunter/core/controller.py

echo "Setup complete!"
```

### Environment Variables

Required: `OPENAI_API_KEY` for LLM calls

Create `.env` in the `cat_finder` directory:
```bash
OPENAI_API_KEY=sk-...
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

### Mutation Engines

**mutmut (default)**: Rule-based, fast mutations
```bash
python3 -m agentic_testing.cli
```

**mutahunter**: LLM-powered semantic mutations (requires setup)
```bash
python3 -m agentic_testing.cli --mutation-engine mutahunter
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
cli.py                  # Main entry point, orchestrates all phases
|-- phase_logger.py     # Per-phase logging (separate log file per phase)
|-- run_mutahunter.py   # Mutahunter wrapper (sets LITELLM_DROP_PARAMS for gpt-5 compat)
|-- mutahunter_parser.py # Parse results, syntax-check mutants, test untested mutants
|-- triage.py           # LLM triage (gpt-4o-mini)
|-- agent_loop.py       # Main loop with bulk pre-filter and per-mutant processing
|-- test_generator.py   # Generate tests via LLM (gpt-5-mini, full source context)
|-- test_fixer.py       # Fix failed tests with full traceback (gpt-4o-mini)
|-- error_extractor.py  # Parse pytest errors
|-- test_applier.py     # Apply/remove tests (allows stdlib imports, blocks source imports)
|-- verifier.py         # Verify mutants are killed
```

## LLM Models

| Component | Model | Why |
|-----------|-------|-----|
| Triage | gpt-4o-mini | Simple yes/no classification, cost-effective |
| Test generation | gpt-5-mini | Hardest task, needs to write correct mock-heavy tests |
| Test fixer | gpt-4o-mini | Error analysis and targeted fixes |
| Mutahunter | gpt-5-mini | Mutation generation (via LITELLM_DROP_PARAMS=true) |

Note: gpt-5-mini only supports temperature=1. The `LITELLM_DROP_PARAMS` env var is set when running mutahunter to drop unsupported parameters.

## Test Applier Rules

- Stdlib imports (`import threading`, `from uuid import UUID`, etc.) are **allowed** inside test methods
- `import cat_finder` is **allowed** (for accessing `cat_finder.main()`, etc.)
- `from cat_finder import ...` is **blocked** (should be at top of file where hardware mocks are set up)

## Debugging with Phase Logs

When a run produces unexpected results, check the phase logs in order:

1. **Tests not being generated?** Check `3_triage.log` -- see if triage is filtering too aggressively
2. **Tests failing verification?** Check `5_generation.log` for the LLM prompt/response, then `7_verification.log` for the failure output
3. **Fixes not working?** Check `6_fixing.log` to see what error was sent and what the LLM responded
4. **All mutants already killed?** Check `4_prefilter.log` -- mutant list may be stale
5. **Parser issues?** Check `2_parsing.log` for killed/survived counts

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
- Check `6_fixing.log` and `7_verification.log` for details
- The self-correction loop should fix most issues automatically (up to 5 attempts)
- Use `--dry-run` to preview without applying changes

**All mutants already killed?**
- Check `4_prefilter.log` to confirm
- The mutant list is stale. Re-run without `--skip-mutmut` to generate fresh mutations.

**No mutants found?**
- Ensure tests are passing first: `pytest test_cat_finder.py`
- Check `.agentic_testing_cache/mutmut_results.txt` for mutation results

**LLM errors?**
- Check `3_triage.log` or `5_generation.log` for API error responses
- Verify `OPENAI_API_KEY` is set correctly in `.env`
- Check API rate limits and quotas
- For gpt-5-mini temperature errors with mutahunter, ensure `LITELLM_DROP_PARAMS=true` is set (handled automatically by `run_mutahunter.py`)

**Stale bytecode?**
- If you see `NameError` after editing files, clear the cache: `rm -rf agentic_testing/__pycache__`

## Files

- `requirements.txt` - Python dependencies
- `venv/` - Virtual environment (isolated dependencies)
- `.agentic_testing_cache/` - Cached results, triage, and logs
  - `logs/run_*/` - Per-run phase log directories

## References

- [Meta's ACH Paper](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)
- [mutmut Documentation](https://github.com/boxed/mutmut)
