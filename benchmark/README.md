# ChainBench public task inventory

The paper reports 97 task–attack pairs across Banking (16), Slack (21), Travel (20), and Workspace (40). `task_inventory.json` is a sanitized inventory of the corresponding 97 user-task IDs present in the existing public reference-log collection. It includes domain, task ID, source path, and source commit; it does not include execution traces, concrete attacker destinations, credentials, payloads, or operational generation prompts.

**This inventory is not yet a validated release of the paper's complete task–attack pair manifest.** It supports checking domain coverage and task membership. The final attack pairing, sanitized objective descriptions, and per-pair evaluation metadata remain in preparation.

## Why pairing is not inferred automatically

The legacy Slack `injection_tasks_v3.py` defines two candidate attack classes targeting `UserTask4`. The available public source alone does not establish which mapping was used in every reported evaluation. The inventory therefore does not silently choose a candidate or claim that task membership establishes the exact evaluated pairing.

The public benchmark release should resolve that mapping from actual experiment records, provide stable pair IDs, and explain exclusions and judge semantics. Until then, use the inventory for task membership and the [protocol](../evaluation/PROTOCOL.md) for reported evaluation definitions.

## Provenance

The inventory is derived from file names in the public reference-log collection at source commit `b6f10ce6abfe106fd3a27bb15e34ad462b71209a`. Existing legacy reference logs remain separate from this sanitized inventory and are not represented as L3 attacker inputs.
