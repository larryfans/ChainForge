# Reported results and provenance

`additional_defenses.csv` transcribes the aggregate values reported in the camera-ready paper's additional-defense table. These are reported summaries, not newly performed experiments or inferred per-task records.

- Model: GPT-5.4.
- Tasks: 97 per evaluation run (Banking 16, Slack 21, Travel 20, Workspace 40).
- Repetitions: five evaluation runs.
- Values: percentages, mean and sample standard deviation.
- Errors: included in the denominator; failures for both ASR and utility.
- FORGE: mixed authorization configurations, including five reruns in the first round; see [configuration notes](../defenses/README.md).

The CSV contains rounded published aggregates. The per-task and full-precision run records needed to independently recompute those aggregates are not bundled. Repeating a summary-table calculation is distinct from reproducing an evaluation.

To validate the CSV and print its Markdown summary, run from the repository root:

```bash
python3 analysis/summarize_results.py
```

The command uses the Python standard library, makes no network calls, needs no API key, and does not invoke attack code, agent execution, or restricted materials.

Reference: [ChainForge paper](https://openreview.net/forum?id=K070HFaYyp), additional runtime defense evaluation appendix. Before publishing further datasets, provide actual provenance and the reporting metadata in [evaluation/PROTOCOL.md](../evaluation/PROTOCOL.md). Do not reconstruct unpublished counts or outcomes from rounded percentages.
