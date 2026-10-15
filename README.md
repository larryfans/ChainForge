# ChainForge: Tool-Chain Hijacking Attacks against LLM Agents via Execution-Grounded Tool Synthesis
## 🎉🎉🎉 Accepted by The Fortieth Annual Conference on Neural Information Processing Systems

## Abstract
LLM-based agents increasingly rely on external tools to accomplish complex tasks, yet the security of tool-calling pipelines remains poorly understood at the chain level. Prior attacks target individual tool invocations through prompt injection or metadata manipulation, but compromising a single step in a multi-step workflow is conspicuous and rarely sufficient for complex adversarial objectives. In this work, we uncover a more insidious yet realistic attack surface, tool-chain hijacking, in which an adversary constructs a coherent sequence of tools that collectively replace the agent's intended execution trace while still completing the user's task correctly, rendering the hijack invisible to both the agent and the user. To operationalize this threat, we propose ChainForge, an execution-grounded framework that mines agent execution logs to synthesize replacement chains through rollout-based optimization, then embeds adversarial payloads into the chain's code via multi-criteria iterative refinement. To systematically evaluate chain-level threats, we further construct ChainBench, a benchmark of 97 tasks across 4 domains. Experiments on four frontier LLMs show that ChainForge achieves a trace hijack rate of up to 98.54%, maintains task utility above 80.41%, transfers across models with over 84.95% success, and evades all evaluated defenses and code-safety scanners at substantially higher rates than single-tool baselines, exposing a critical blind spot in current agent security.


## ChainForge Workflow

`ChainForge/` is organized to match the two-phase workflow of `\ours{}`.

- **Phase I** synthesizes a task-specific metadata-level tool chain that is grounded in reference tool calls and optimized to attract the agent into the generated trajectory.
- **Phase II** instantiates that optimized chain as executable tool code, preserves benign utility, embeds the adversarial payload, and finally refines the code with MCIR to improve stealth while maintaining execution constraints.

This repository snapshot should be read together with the method description:

> **Phase I** synthesizes a task-specific metadata-level tool chain, grounded in reference tool calls, and optimizes its descriptions and staged outputs to attract the agent into the generated trajectory.  
> **Phase II** instantiates the optimized metadata chain into executable tool code, preserving task utility while embedding adversarial payloads through multi-criteria iterative refinement.

## Environment Setup

Install the Python environment from the `ChainForge/` root:

```bash
pip install -r requirements.txt
```

## Phase I: Chain-Level Hijack Optimization

Phase I operates at the metadata level. For each domain, run the domain-specific generator to synthesize the staged tool chain from all task logs.

Reference command pattern:

```bash
PYTHONPATH=. python <domain>_v6_auto/generate_tool_param_v17.py --all-task-logs
```

Example domain commands:

```bash
PYTHONPATH=. python banking_v6_auto/generate_tool_param_v17.py --all-task-logs
PYTHONPATH=. python slack_v6_auto/generate_tool_param_v17.py --all-task-logs
PYTHONPATH=. python travel_v6_auto/generate_tool_param_v17.py --all-task-logs
PYTHONPATH=. python workspace_v6_auto/generate_tool_param_v17.py --all-task-logs
```

The goal of this stage is to produce optimized metadata-level chains whose tool descriptions, parameters, and staged outputs create semantic momentum and make the generated trajectory the path of least resistance for the agent.

## Phase II: Utility-Preserving Code Instantiation

Phase II turns the optimized metadata chain into executable code. The implementation objective follows the paper directly:

```text
c_i^* = argmax_c MI(c, g_i) * TFUR(c, q) * IPP(c, psi)
```

where:

- `MI` enforces metadata invariance with the Phase I tool specification.
- `TFUR` enforces benign-task completion without replaying constants from ground-truth traces.
- `IPP` preserves a deterministic execution path that embeds the payload.

For code instantiation, use the domain-specific instructions in:

- [banking_code/readme.md](banking_code/readme.md)
- [slack_code/readme.md](slack_code/readme.md)
- [travel_code/readme.md](travel_code/readme.md)
- [workspace_code/readme.md](workspace_code/readme.md)

These prompts define how Codex should:

1. instantiate executable `user_task_X.py` implementations from the Phase I metadata chain,
2. construct `injection_task_X.py` variants while preserving utility, and
3. keep the generated code within the static and dynamic constraints used in evaluation.

## MCIR: Stealthy Refinement

After executable code is generated, use MCIR to improve stealth while preserving task utility and attack validity. This corresponds to the refinement stage in the method section:

```text
c_i^* = argmax_c TCSC(c, m_i)
subject to
SA(c) >= theta_sa,
PUR(c) >= theta_pur,
V_util(c) = true,
V_atk(c) = true
```

In this repository, MCIR is implemented in [MCIR/README.md](MCIR/README.md) and the `MCIR/` module.

Typical usage is:

```bash
cd MCIR
python run_mcir.py --scenario path/to/scenario.py --objective "your adversarial objective"
```

## End-to-End Summary

The full workflow in `ChainForge/` is:

1. Install the environment with `pip install -r requirements.txt`.
2. Run Phase I generation for each domain with `generate_tool_param_v17.py --all-task-logs`.
3. Use the domain-specific `*_code/readme.md` prompt to let Codex instantiate executable code.
4. Run MCIR to improve stealthiness while preserving utility and attack execution constraints.



## Citation

If you find this code useful, please consider citing our paper:
 ```bibtex
@inproceedings{
anonymous2026chainforge,
title={ChainForge: Tool-Chain Hijacking Attacks against {LLM} Agents via Execution-Grounded Tool Synthesis},
author={Anonymous},
booktitle={The Fortieth Annual Conference on Neural Information Processing Systems},
year={2026},
url={https://openreview.net/forum?id=K070HFaYyp}
}
``` 
