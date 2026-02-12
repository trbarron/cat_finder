# Scripts for moz-artery-tree

## analyze_survived_mutants.py

Parses `mutmut_results.txt`, collects context for each **survived** mutant (diff, source snippet, tests that run), and optionally calls an LLM to decide whether to write a test.

### Output format (`survived_mutants_analysis.json`)

Consumable by a future step that writes or suggests tests. Top-level shape:

```json
{
  "source": "path/to/mutmut_results.txt",
  "package_dir": "path/to/packages/moz-artery-tree",
  "num_survived": 123,
  "mutants": [
    {
      "mutant_id": "moz_artery_tree.utils.data_utils.x_one_study__mutmut_11",
      "mangled_name": "moz_artery_tree.utils.data_utils.x_one_study",
      "diff": "--- ...\n+++ ...\n@@ ...",
      "source_file": "moz_artery_tree/utils/data_utils.py",
      "source_snippet": "  75| def one_study(...)\n  76|     ...",
      "tests_that_run": ["tests/utils/test_data_utils.py::test_one_study", ...],
      "test_files_content": "--- tests/... ---\n...",
      "prompt_for_llm": "You are analyzing...",
      "llm_analysis": { "should_write_test": true, "reason": "...", "suggestion": "..." }
    }
  ]
}
```

- **llm_analysis** is present only when run with `--call-llm` and `OPENAI_API_KEY` set.
- Downstream: filter `mutants` by `llm_analysis.should_write_test === true`, then use `mutant_id`, `diff`, `source_file`, `tests_that_run`, and `suggestion` to drive test generation.

### Run from package root

```bash
cd packages/moz-artery-tree
python scripts/analyze_survived_mutants.py -o ./mutation_testing/survived_test.json --report ./mutation_testing/survived_test_report.md
python scripts/analyze_survived_mutants.py -o ./mutation_testing/survived_test.json --report ./mutation_testing/survived_test_report.md --limit 5 --call-llm 
```
