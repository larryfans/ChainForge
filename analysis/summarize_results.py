"""Validate and format reported defense aggregates; no model or network calls."""

import csv
import math
from pathlib import Path


RESULTS = Path(__file__).resolve().parents[1] / "results" / "additional_defenses.csv"


def main():
    with RESULTS.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError("The result table is empty.")
    names = set()
    for row in rows:
        name = row["configuration"]
        if not name or name in names:
            raise ValueError("Configurations must be named and unique.")
        names.add(name)
        if int(row["n_tasks"]) <= 0 or int(row["n_evaluation_runs"]) < 2:
            raise ValueError("Positive task counts and at least two runs are required.")
        for metric in ("asr", "utility"):
            mean = float(row[f"{metric}_mean_pct"])
            std = float(row[f"{metric}_sample_std_pct"])
            if not math.isfinite(mean) or not 0 <= mean <= 100:
                raise ValueError("Mean percentages must be finite and in [0, 100].")
            if not math.isfinite(std) or not 0 <= std <= 100:
                raise ValueError("Standard deviations must be finite and in [0, 100].")
    print("Reported mean ± sample standard deviation (%); not a fresh evaluation.\n")
    print("| Configuration | ASR ↓ | Utility ↑ |")
    print("| :--- | ---: | ---: |")
    for row in rows:
        asr = f"{float(row['asr_mean_pct']):.2f} ± {float(row['asr_sample_std_pct']):.2f}"
        utility = f"{float(row['utility_mean_pct']):.2f} ± {float(row['utility_sample_std_pct']):.2f}"
        print(f"| {row['configuration']} | {asr} | {utility} |")
    print("\nFORGE mixes authorization configurations; see defenses/README.md.")


if __name__ == "__main__":
    main()
