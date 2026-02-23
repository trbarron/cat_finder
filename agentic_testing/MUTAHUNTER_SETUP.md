# MutaHunter Setup Guide

## Quick Install

```bash
pip install mutahunter certifi httpx litellm
```

## What is MutaHunter?

MutaHunter is an LLM-powered mutation testing tool that generates **context-aware, realistic mutations** instead of simple rule-based changes.

### Comparison

| Tool | Type | Speed | Mutations |
|------|------|-------|-----------|
| **mutmut** | Rule-based | ⚡ Fast (seconds) | Syntactic (e.g., `>=` → `>`) |
| **mutahunter** | LLM-powered | 🐢 Slower (minutes) | Semantic (understands context) |

## Installation

### Full Installation

```bash
# Install all dependencies
pip install mutahunter certifi httpx litellm

# Verify installation
mutahunter --version
```

### Dependencies

MutaHunter requires:
- `certifi` - SSL certificate validation
- `httpx` - HTTP client
- `litellm` - LLM API wrapper (supports OpenAI, Anthropic, etc.)

## Configuration

### Environment Variables

```bash
# Required: Your LLM API key
export OPENAI_API_KEY="sk-..."

# Optional: Choose model
export MUTAHUNTER_MODEL="gpt-4o-mini"  # or gpt-4, claude-3-5-sonnet, etc.
```

### Usage with Agentic Testing

```bash
# Use mutahunter
python -m agentic_testing.cli --mutation-engine mutahunter

# With custom files
python -m agentic_testing.cli \
  --mutation-engine mutahunter \
  --source-file my_module.py \
  --test-file test_my_module.py
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'certifi'"

```bash
pip install certifi httpx litellm
```

### "mutahunter: command not found"

```bash
pip install mutahunter
```

### Still having issues?

Use mutmut instead (default):
```bash
python -m agentic_testing.cli
# Or explicitly:
python -m agentic_testing.cli --mutation-engine mutmut
```

## Cost Estimate

MutaHunter uses LLM API calls:
- **Mutation generation**: ~$0.01-0.05 per file
- **Model**: gpt-4o-mini recommended (cheap + good)
- **Alternative**: Use mutmut (free) for development, mutahunter for CI/CD

## When to Use Each

### Use mutmut when:
- ✅ Quick iterations during development
- ✅ Large codebases (faster)
- ✅ Cost is a concern
- ✅ Simple mutation coverage is enough

### Use mutahunter when:
- ✅ Finding realistic bugs
- ✅ Security-critical code
- ✅ Final validation before release
- ✅ Want semantic understanding

## Example

### mutmut mutation
```python
# Rule-based: just swaps operators
if x >= 0:  →  if x > 0:
```

### mutahunter mutation
```python
# Context-aware: understands what would break
def transfer_money(from_account, to_account, amount):
    from_account.balance -= amount
    to_account.balance += amount

# Mutahunter might generate:
def transfer_money(from_account, to_account, amount):
    from_account.balance -= amount
    to_account.balance -= amount  # Both subtract - realistic bug!
```

## Resources

- **GitHub**: https://github.com/codeintegrity-ai/mutahunter
- **Documentation**: See mutahunter repo
- **Supported models**: Any model via litellm (OpenAI, Anthropic, Gemini, etc.)
