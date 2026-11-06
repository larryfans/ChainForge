# Additional chain-aware defense configurations

**Status: configuration documentation and reported aggregates are available; runnable reproductions are still in preparation.** This directory does not claim to contain the original defense implementations or the experiment adapters.

The experiments evaluate adapted configurations on GPT-5.4 with 97 tasks over five evaluation runs. These are the configurations reported in the paper's additional-defense appendix, not general guarantees about the original systems.

| Configuration | Evaluated boundary | Adaptation and limits |
| :--- | :--- | :--- |
| ToolSafe | Step-level tool-call review | GPT-5.4 guardian, threshold 0.5; not the original trained TS-Guard. Review adds blocking/replanning and affects legitimate task completion. |
| FORGE | Policy enforcement at represented actions | Hand-written Datalog policies, task-derived destination authorization, and internal email/web-write checks. Other internal side effects are not instrumented. The first round includes five reruns under revised authorization rules; the aggregate mixes configurations. |
| FIDES | Planner-level information-flow checks | Adaptation of the public tutorial's checks. It does not inspect tool-internal side effects; this is a boundary-specific result, not a conclusion about every FIDES deployment. |

Execution errors remain in the denominator and count as failures for ASR and utility. Reported values are in [results/additional_defenses.csv](../results/additional_defenses.csv), with provenance and limits in [results/README.md](../results/README.md).

Before marking runnable reproductions as released, include the actual adapters, upstream version references, configuration files, environment requirements, a safe evaluation entry point, and a verified example. Configuration notes and links to the paper alone do not fulfill that implementation release.
