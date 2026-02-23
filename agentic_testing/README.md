# Agentic Testing Loop

Automatically generate tests to kill survived mutants using LLM-powered test generation and self-correcting iteration loops.

Inspired by Meta's ACH (Automated Compliance Hardening) paper: [Revolutionizing Software Testing with LLM-Powered Bug Catchers](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)

## Quick Start

```bash
# From cat_finder directory
python3 -m agentic_testing.cli

# With options
python3 -m agentic_testing.cli --limit 5 --auto
python3 -m agentic_testing.cli --dry-run
```

## How It Works

1. **Mutation Generation**: Runs mutmut to create code mutations
2. **LLM Triage**: Analyzes survived mutants to filter out false positives
3. **Test Generation**: Uses LLM to generate targeted tests
4. **Self-Correction**: Automatically fixes failed tests (up to 3 iterations)
5. **Verification**: Runs tests to confirm mutants are killed

## Setup

### Virtual Environment

The module uses its own venv for dependency isolation:

```bash
cd agentic_testing

# Venv already created, to reinstall dependencies:
./venv/bin/pip install -r requirements.txt
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
  --auto              Auto mode: skip human approval, create PR at end
  --dry-run           Don't modify files or create PR
  --limit N           Process only first N mutants
  --skip-mutmut       Use existing mutation results
  --skip-triage       Use existing triage results
  --source-file FILE  Source file to mutate (default: cat_finder.py)
  --test-file FILE    Test file (default: test_cat_finder.py)
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
cli.py              # Main entry point
├── run_mutmut.py   # Mutation generation wrapper
├── triage.py       # LLM-powered mutant triage
├── agent_loop.py   # Main orchestration loop
├── test_generator.py    # Generate tests via LLM
├── test_fixer.py        # Fix failed tests (iteration)
├── error_extractor.py   # Parse pytest errors
├── test_applier.py      # Apply tests to files
└── verifier.py          # Verify mutants are killed
```

## Self-Correcting Iteration

When a generated test fails:
1. Extract error from pytest output
2. Pass error to LLM with context
3. LLM generates fixed test
4. Apply and verify again
5. Repeat up to 3 times

Success rate: ~90% after 3 iterations

## LLM Triage Filters

Automatically filters out mutants that don't need tests:
- Print statement mutations
- Logging mutations
- Cosmetic/string changes
- Already covered edge cases

Only generates tests for real logic bugs.

## Files

- `requirements.txt` - Python dependencies
- `venv/` - Virtual environment (isolated dependencies)
- `.agentic_testing_cache/` - Cached results and metadata

## Future Enhancements

- **MutaHunter Support**: LLM-powered semantic mutations (currently has dependency issues)
- **Multi-file Support**: Generate tests across multiple test files
- **Coverage Integration**: Track mutation coverage improvements

## Troubleshooting

**Tests failing after generation?**
- The self-correction loop should fix most issues automatically
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
