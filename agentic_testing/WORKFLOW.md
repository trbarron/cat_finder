# Agentic Testing Loop - Detailed Workflow

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         AGENTIC TESTING LOOP                            │
│                  (Inspired by Meta's ACH System)                        │
└─────────────────────────────────────────────────────────────────────────┘

                                    │
                                    ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 1: MUTATION GENERATION                  │
        │  ────────────────────────────────────         │
        │  • Run mutmut on source code                  │
        │  • Generate mutations using rule-based tool   │
        │  • Identify survived mutants                  │
        │  • Save to mutmut_results.txt                 │
        └────────────────┬──────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 2: LLM TRIAGE (Critical!)               │
        │  ─────────────────────────────────            │
        │  For each survived mutant:                    │
        │  • Extract diff, source context, tests        │
        │  • LLM analyzes mutation                      │
        │  • Applies filtering criteria:                │
        │    ❌ Print/logging statements                │
        │    ❌ Cosmetic changes                        │
        │    ❌ String formatting in messages           │
        │    ✅ Return value changes                    │
        │    ✅ Control flow changes                    │
        │    ✅ State/database changes                  │
        │  • Returns: should_write_test + agent_prompt  │
        └────────────────┬──────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 3: TEST GENERATION                      │
        │  ──────────────────────────                   │
        │  For each triaged mutant:                     │
        │  • LLM reads agent_prompt                     │
        │  • LLM reads existing test file               │
        │  • LLM generates new test method              │
        │  • Returns: test_class, test_code, name       │
        └────────────────┬──────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 4: HUMAN APPROVAL (if interactive)      │
        │  ───────────────────────────────────          │
        │  • Display generated test                     │
        │  • Show explanation                           │
        │  • User chooses: y/n/skip/quit                │
        └────────────────┬──────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 5: APPLY TEST                           │
        │  ─────────────────────                        │
        │  • Parse test file                            │
        │  • Find target test class                     │
        │  • Insert new test method                     │
        │  • Write updated file                         │
        └────────────────┬──────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 6: VERIFY                               │
        │  ────────────────                             │
        │  • Run pytest on modified test file           │
        │  • Confirm new test passes                    │
        │  • (Future: Re-run mutmut to confirm kill)    │
        └────────────────┬──────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 7: ITERATE                              │
        │  ─────────────────                            │
        │  • Move to next triaged mutant                │
        │  • Repeat steps 3-6                           │
        │  • Until all mutants processed                │
        └────────────────┬──────────────────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────────────────┐
        │  STEP 8: COMMIT (if auto mode)                │
        │  ────────────────────────────────             │
        │  • git add test files                         │
        │  • git commit with summary                    │
        │  • git push to remote                         │
        │  • gh pr create                               │
        └───────────────────────────────────────────────┘
```

## Component Details

### 1. Mutation Generation (`cli.py::run_mutmut`)
- **Input**: Source code (`cat_finder.py`)
- **Tool**: `mutmut` (via `mutation_testing/run_mutmut.py`)
- **Output**: `mutation_testing/mutmut_results.txt`

### 2. LLM Triage (`cli.py::triage_mutants`)
- **Input**: Survived mutants list
- **Reuses**: `mutation_testing/analyze_survived_mutants.py`
- **LLM Prompt**: Contains strict filtering criteria
- **Output**: List of mutants where `should_write_test: true`
- **Cache**: `mutation_testing/triage.json`

### 3. Test Generation (`test_generator.py::generate_test_code`)
- **Input**:
  - `agent_prompt` from triage
  - Existing test file content
  - Test file path
- **LLM Model**: `gpt-5-mini` (temperature=0.2)
- **Output**: JSON with test_class, test_method_name, test_code, explanation

### 4. Test Application (`test_applier.py::apply_test`)
- **Input**:
  - Test file path
  - Test class name
  - Generated test code
- **Processing**:
  - Parse test file
  - Locate test class
  - Insert test method at end of class
  - Maintain proper indentation
- **Output**: Modified test file

### 5. Verification (`verifier.py::verify_test_kills_mutant`)
- **Current**: Runs pytest to ensure test passes
- **Future**: Re-run mutmut on specific mutant to confirm kill

### 6. Orchestration (`agent_loop.py::run_agent_loop`)
- Main loop that coordinates all steps
- Handles interactive vs auto mode
- Tracks success/error/skip/reject counts
- Provides summary statistics

## Data Flow

```
mutmut_results.txt
    │
    ▼
parse_survived_mutants()
    │
    ▼
triage_mutants() ──────────> LLM API (triage)
    │                              │
    │                              ▼
    │                       {should_write_test: true,
    │                        agent_prompt: "..."}
    │                              │
    └──────────────────────────────┘
                 │
                 ▼
        triage.json (cache)
                 │
                 ▼
        run_agent_loop()
                 │
                 ▼
        For each mutant:
            │
            ├──> generate_test_code() ──> LLM API (generation)
            │         │
            │         ▼
            │    {test_code: "...",
            │     test_class: "...",
            │     test_method_name: "..."}
            │         │
            ├─────────┘
            │
            ├──> [Human approval] (if interactive)
            │
            ├──> apply_test()
            │         │
            │         ▼
            │    Modified test_cat_finder.py
            │         │
            └─────────┘
                 │
                 ▼
        verify_test_kills_mutant()
                 │
                 ▼
        Summary Report
```

## Key Design Decisions

### 1. **Separation of Concerns**
- Each component has a single responsibility
- Easy to test and modify independently
- Clear interfaces between components

### 2. **Reuse Existing Tools**
- Leverages `mutmut` for mutation generation
- Reuses `analyze_survived_mutants.py` for triage
- Builds on proven filtering criteria

### 3. **Human-in-the-Loop by Default**
- Interactive mode ensures quality
- Auto mode available for CI/CD integration
- Clear approval prompts

### 4. **Caching and Resumability**
- Triage results cached to `triage.json`
- Can skip mutmut run with `--skip-mutmut`
- Can skip triage with `--skip-triage`

### 5. **Progressive Enhancement**
- Basic verification (pytest) now
- Full mutmut re-verification later
- Room for mutahunter integration

## Filtering Criteria (Triage Step)

The triage step uses strict criteria to filter out non-critical mutations:

### ❌ DO NOT Generate Tests For:
1. **Print/Logging Statements**
   - Mutations inside `print()`, `logging.*`, `logger.*`
   - Changes to log message content
   - String formatting in error messages

2. **Cosmetic Changes**
   - Variable name changes in messages
   - Whitespace changes
   - Comment changes

3. **Equivalent Mutations**
   - Changes that produce identical behavior
   - Logically equivalent expressions

### ✅ DO Generate Tests For:
1. **Return Value Changes**
   - Different return values
   - Return type changes
   - Missing returns

2. **Control Flow Changes**
   - Modified conditionals
   - Loop bound changes
   - Early exits/breaks

3. **State Changes**
   - Database writes
   - File operations
   - Shared state modifications

4. **Externally Visible Side Effects**
   - API calls
   - S3 uploads
   - DynamoDB operations

## Example: End-to-End Flow

### Input Mutation
```python
# Original
if idx < 0 or idx >= len(labels):
    return "unknown"

# Mutated (>= changed to >)
if idx < 0 or idx > len(labels):
    return "unknown"
```

### Step-by-Step Processing

1. **Triage**:
   ```json
   {
     "should_write_test": true,
     "reason": "Boundary check change affects correctness",
     "agent_prompt": "Add test for idx == len(labels) edge case"
   }
   ```

2. **Generation**:
   ```python
   def test_index_equal_to_length(self):
       """Test that index equal to length returns 'unknown'."""
       idx = len(self.labels)
       self.assertEqual(get_label(self.labels, idx), "unknown")
   ```

3. **Application**:
   - Insert into `TestGetLabel` class
   - Maintain indentation and style

4. **Verification**:
   - Run pytest → PASSED
   - (Future) Re-run mutmut → KILLED

5. **Result**:
   - New test committed
   - Mutant killed
   - Coverage improved!

## Performance Considerations

### LLM API Calls
- **Triage**: 1 call per survived mutant
- **Generation**: 1 call per triaged mutant
- **Total**: ~2 calls per test generated

### Optimization Strategies
1. **Batch Processing**: Process mutants in parallel (future)
2. **Caching**: Reuse triage results across runs
3. **Skip Options**: Skip mutmut/triage when iterating
4. **Limits**: Use `--limit N` for testing

### Estimated Cost
- For 10 survived mutants → 5 need tests (after triage)
- 5 triage calls + 5 generation calls = 10 LLM calls
- Using gpt-5-mini: ~$0.01-0.05 per run

## Future Enhancements

1. **Better Mutation Generation**
   - Integrate mutahunter for context-aware mutations
   - LLM-generated mutations (like Meta's ACH)

2. **Full Verification**
   - Re-run mutmut after test application
   - Confirm mutant is actually killed

3. **Parallel Processing**
   - Generate tests in parallel
   - Faster iteration

4. **CI/CD Integration**
   - Run as GitHub Action
   - Automatic PRs for test improvements

5. **Multi-Language Support**
   - Extend to TypeScript, Go, Rust
   - Generic test generation framework
