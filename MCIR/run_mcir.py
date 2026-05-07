#!/usr/bin/env python3
"""
MCIR CLI -- Multi-Criteria Iterative Refinement for tool chain code.

Usage:
    # Set environment variables
    export OPENAI_API_KEY="your-api-key"

    # Single scenario
    python run_mcir.py --scenario scenario.py --model gpt-4o

    # Batch mode
    python run_mcir.py --scenario-dir scenarios/ --model gpt-4o

    # With external validator
    python run_mcir.py --scenario scenario.py --validator-path /path/to/validator.py
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.pipeline import MCIRPipeline, MCIRConfig, run_batch

DEFAULT_OBJECTIVES_FILE = Path(__file__).parent / "objectives.json"


def load_objectives(objectives_file: str | None = None) -> dict[str, str]:
    """Load objectives from JSON file."""
    path = Path(objectives_file) if objectives_file else DEFAULT_OBJECTIVES_FILE
    if not path.exists():
        print(f"Warning: Objectives file not found: {path}")
        return {}
    try:
        data = json.loads(path.read_text())
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: Failed to parse objectives file: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser(
        description="MCIR: Multi-Criteria Iterative Refinement for tool chain code"
    )

    # Input
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", type=str, help="Path to a single scenario .py file")
    group.add_argument("--scenario-dir", type=str, help="Directory containing scenario .py files")

    # Adversarial objective
    parser.add_argument("--objective", type=str, default="",
                        help="Adversarial objective description (overrides objectives.json)")
    parser.add_argument("--objectives-file", type=str, default=None,
                        help="JSON file mapping scenario_id -> objective")

    # LLM
    parser.add_argument("--model", type=str, default="gpt-4o")
    parser.add_argument("--api-key", type=str, default=None,
                        help="API key (default: OPENAI_API_KEY env var)")
    parser.add_argument("--base-url", type=str, default=None,
                        help="API base URL (default: OPENAI_BASE_URL env var)")

    # Thresholds
    parser.add_argument("--th-tcsc", type=float, default=0.7, help="TCSC threshold (default: 0.7)")
    parser.add_argument("--th-pur", type=float, default=0.95, help="PUR threshold (default: 0.95)")
    parser.add_argument("--th-sa", type=float, default=0.8, help="SA threshold (default: 0.8)")

    # Pipeline
    parser.add_argument("--max-iter", type=int, default=8, help="Max iterations (default: 8)")
    parser.add_argument("--output-dir", type=str, default="./output")
    parser.add_argument("--no-verify", action="store_true", help="Disable functionality verification")
    parser.add_argument("--validator-path", type=str, default=None,
                        help="Path to external validator script")
    parser.add_argument("--domain", type=str, default="banking",
                        choices=["banking", "slack", "travel", "workspace"],
                        help="Domain for validator resolution (default: banking)")

    args = parser.parse_args()

    # Load objectives from file
    objectives = load_objectives(args.objectives_file)

    # Get objective for current scenario
    objective = args.objective

    if args.scenario:
        scenario_id = Path(args.scenario).stem
        if not objective:
            m = __import__("re").search(r'task[_-]?(\d+)', scenario_id)
            task_num = m.group(1) if m else ""

            candidates = []
            if args.domain:
                candidates.append(f"{args.domain}_{scenario_id}")
            candidates.append(scenario_id)
            if scenario_id.endswith("_refined"):
                base = scenario_id.replace("_refined", "")
                if args.domain:
                    candidates.append(f"{args.domain}_{base}")
                candidates.append(base)
            if args.domain and task_num:
                candidates.append(f"{args.domain}_injection{task_num}")
                candidates.append(f"{args.domain}_injection_{task_num}")

            for key in candidates:
                if key in objectives:
                    objective = objectives[key]
                    break

            if objective:
                print(f"\n[Objective] Loaded from objectives.json: {objective[:60]}...")
            else:
                print(f"\n[Objective] Warning: No objective found for {scenario_id}")
    elif args.scenario_dir:
        if not objective:
            print(f"\n[Objective] Using objectives from {args.objectives_file or 'objectives.json'}")
            print(f"  Loaded {len(objectives)} scenario objectives")

    # Resolve LLM config
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL")

    config = MCIRConfig(
        api_key=api_key,
        base_url=base_url,
        model=args.model,
        max_iterations=args.max_iter,
        th_tcsc=args.th_tcsc,
        th_pur=args.th_pur,
        th_sa=args.th_sa,
        adversarial_objective=objective,
        output_dir=args.output_dir,
        enable_verification=not args.no_verify,
        domain=args.domain,
        validator_path=args.validator_path or "",
    )

    if args.scenario:
        pipeline = MCIRPipeline(config)
        result = pipeline.refine(args.scenario)
        status = "SUCCESS" if result.success else "INCOMPLETE"
        print(f"\nFinal: {result.scenario_id} -> {status}")

    elif args.scenario_dir:
        scenario_dir = Path(args.scenario_dir)
        py_files = sorted(scenario_dir.glob("*injection*.py"))

        if not py_files:
            print(f"No injection scenario files found in {scenario_dir}")
            sys.exit(1)

        print(f"\nFound {len(py_files)} scenario files")
        results = run_batch(
            config,
            [str(f) for f in py_files],
            objectives=objectives if not args.objective else None,
        )


if __name__ == "__main__":
    main()
