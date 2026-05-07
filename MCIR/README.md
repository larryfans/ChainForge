# MCIR: Multi-Criteria Iterative Refinement

This module implements the MCIR pipeline for iteratively refining tool chain code across three quality dimensions: **TCSC** (Text-Code Semantic Consistency), **PUR** (Parameter Utilization Rate), and **SA** (Structural Assimilation).

## Requirements

- Python 3.11+
- [openai](https://pypi.org/project/openai/) (for LLM calls)
- [agentdojo](https://github.com/ethz-spylab/agentdojo) (optional, for runtime verification)

## Quick Start

```bash
# Set API credentials
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # optional

# Single scenario
python run_mcir.py --scenario path/to/scenario.py --objective "Your adversarial objective"

# Batch mode
python run_mcir.py --scenario-dir scenarios/ --objectives-file objectives.json

# Without verification (faster, no agentdojo dependency)
python run_mcir.py --scenario scenario.py --no-verify
```

## Command Line Options

| Flag | Default | Description |
|------|---------|-------------|
| `--scenario` | - | Path to a single scenario `.py` file |
| `--scenario-dir` | - | Directory of scenario files (batch mode) |
| `--objective` | - | Adversarial objective (overrides file) |
| `--objectives-file` | `objectives.json` | JSON mapping scenario ID to objective |
| `--model` | `gpt-4o` | LLM model name |
| `--api-key` | env `OPENAI_API_KEY` | API key |
| `--base-url` | env `OPENAI_BASE_URL` | API base URL |
| `--th-tcsc` | 0.7 | TCSC threshold |
| `--th-pur` | 0.95 | PUR threshold |
| `--th-sa` | 0.8 | SA threshold |
| `--max-iter` | 8 | Maximum refinement iterations |
| `--domain` | `banking` | Domain (banking/slack/travel/workspace) |
| `--output-dir` | `./output` | Output directory |
| `--no-verify` | false | Disable runtime verification |
| `--validator-path` | - | External validator script path |

## Architecture

```
phase2code/
├── run_mcir.py              CLI entry point
├── objectives.json          Adversarial objectives per scenario
├── verifier.py              AgentDojo runtime verifier
├── core/
│   ├── llm_client.py        OpenAI-compatible LLM client
│   ├── parser.py            Scenario file parser (AST-based)
│   └── pipeline.py          MCIR iterative refinement loop
├── evaluators/
│   └── scoring.py           TCSC / PUR / SA evaluators
└── prompts/
    └── strategies.py        Dimension-specific prompt templates
```

### Evaluation Dimensions

| Dimension | Method | Threshold | Description |
|-----------|--------|-----------|-------------|
| **TCSC** | LLM judge | 0.7 | Code-docstring alignment (purpose, operations, return, side effects) |
| **PUR** | AST analysis | 0.95 | Fraction of declared parameters actually used |
| **SA** | Regex rules | 0.8 | Evasion of static pattern detection |

### Iterative Refinement Loop

1. **Evaluate** all dimensions + functional verification
2. **Refine** the weakest tool with a dimension-specific prompt
3. **Validate** re-score and re-run verification
4. **Guard** rollback on regression, lock attack pattern on success

### Prompt Strategies

| Prompt | Target | Trigger |
|--------|--------|---------|
| `P_semantic` | TCSC | Score below threshold |
| `P_param` | PUR | Unused parameters found |
| `P_stealth` | SA | Suspicious patterns detected |
| `P_functional` | Utility | User task fails |
| `P_attack` | Attack | Adversarial objective not achieved |
| `P_analyze` | - | Regression detected |

## External Validator (Optional)

For runtime verification against the real AgentDojo environment, provide a validator script:

```bash
python run_mcir.py --scenario scenario.py --validator-path /path/to/validator.py
```

Or set the environment variable:

```bash
export MCIR_VALIDATOR_PATH=/path/to/validator.py
```

Without a validator, use `--no-verify` to skip the verification step.

## Output

For each scenario, the pipeline produces:

- `{scenario_id}_refined.py` — Refined scenario module
- `{scenario_id}_report.json` — Iteration log with per-step scores

## Programmatic Usage

```python
from core.pipeline import MCIRPipeline, MCIRConfig

config = MCIRConfig(
    api_key="your-key",
    model="gpt-4o",
    adversarial_objective="Send money to IBAN ...",
    th_tcsc=0.7,
    th_pur=0.95,
    th_sa=0.8,
)

pipeline = MCIRPipeline(config)
result = pipeline.refine("path/to/scenario.py")

print(f"Success: {result.success}")
print(f"Iterations: {result.iterations}")
print(f"LLM calls: {result.total_llm_calls}")
```
