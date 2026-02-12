# Mutation Testing

## What is Mutation Testing?

Mutation testing measures test suite quality by introducing small code changes ("mutants") and checking whether your tests catch them. Each mutant is a single modification — changing `>` to `>=`, swapping a parameter for `None`, altering arithmetic, etc.

- **Killed**: Tests failed — they caught the mutation (good)
- **Survived**: Tests still passed — potential gap in coverage (investigate)
- **No tests**: No tests cover this code at all

The mutation score is:

```
Mutation Score = Killed / (Total - Equivalent Mutants)
```

A higher score means stronger tests. 100% is rarely achievable or necessary — focus on critical code paths.

## Why Bother?

Code coverage tells you what lines are *executed* by tests. Mutation testing tells you what lines are actually *verified* by tests. A function can have 100% line coverage but 0% mutation coverage if the tests never assert on its output.

## Workflow

### 1. Configure

In `setup.cfg`:

```ini
[mutmut]
paths_to_mutate=cat_finder.py
tests_dir=.
runner=env NO_PROXY=* python3 -m pytest -x

[tool:pytest]
norecursedirs = mutants
```

- `paths_to_mutate`: Source file(s) to mutate
- `tests_dir`: Where to find tests
- `runner`: How to run the test suite (the `-x` flag stops on first failure for speed)

### 2. Run Mutations

```bash
mutmut run
```

This creates each mutation, runs the test suite against it, and records the result. It takes a while — every mutant triggers a full test run.

### 3. Export Results

```bash
mutmut results > mutation_testing/mutmut_results.txt
```

Example output:

```
cat_finder.x_get_label__mutmut_1: killed
cat_finder.x_get_label__mutmut_5: survived
cat_finder.x_is_image_too_dark__mutmut_1: survived
```

### 4. Analyze Survived Mutants

The analysis script gathers context for each survived mutant and optionally asks an LLM whether a new test is warranted:

```bash
python3 mutation_testing/analyze_survived_mutants.py --call-llm
```

What it does for each survived mutant:
1. Runs `mutmut show <mutant_id>` to get the diff
2. Identifies which tests cover that code
3. Reads the relevant test files
4. Builds a prompt and sends it to the LLM for triage
5. Writes a Markdown report with the LLM's verdict

**Options:**

| Flag | Description |
|------|-------------|
| `--call-llm` | Send each mutant to the LLM for analysis (requires `OPENAI_API_KEY` in `.env`) |
| `--limit N` | Only process the first N mutants (useful for testing the script) |
| `-o FILE` | Output file (default: `survived_test_report.md`) |
| `--mutmut PATH` | Path to mutmut binary if not on PATH |

### 5. Review the Report

The output (`survived_test_report.md`) contains one section per survived mutant with:

- The diff showing what was changed
- Which tests cover that code
- The test file contents
- The LLM's analysis: should we write a test, and if so, what specifically

The LLM is prompted to be conservative — it should only recommend a test when the mutation affects a **return value, stored data, control flow, or externally visible side effect**. Mutations to print/logging statements are explicitly filtered out.

When the LLM recommends a test, it includes an `agent_prompt` — a detailed instruction that can be handed directly to a coding agent (like Claude Code) to write the test.

### 6. Write Tests and Re-run

Write or strengthen tests based on the report, then re-run mutation testing to verify the mutants are now killed:

```bash
mutmut run
mutmut results > mutation_testing/mutmut_results.txt
python3 mutation_testing/analyze_survived_mutants.py --call-llm
```

## Key Files

| File | Purpose |
|------|---------|
| `setup.cfg` | mutmut configuration |
| `mutation_testing/mutmut_results.txt` | Raw results from `mutmut results` |
| `mutation_testing/analyze_survived_mutants.py` | Analysis script (gathers context, calls LLM) |
| `mutation_testing/survived_test_report.md` | Generated Markdown report |
| `mutants/` | Directory where mutmut creates mutated code |
| `.mutmut-cache` | SQLite database with mutation results |

## Not All Survived Mutants Matter

Some survived mutants are **equivalent** — the mutation doesn't change observable behavior. Common examples:

- Changing the content of a `print()` or log message
- Arithmetic changes inside string formatting
- Swapping `<` and `<=` when the boundary value is never hit

The LLM triage step filters these out so you only spend time on mutants that represent real gaps.
