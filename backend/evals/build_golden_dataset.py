"""CLI wrapper around app.services.golden_dataset.build_golden.

The actual logic lives under ``app/services/`` so the FastAPI router
(POST /superadmin/golden/rebuild) can call it without sys.path hacks.

Usage:
    cd backend
    export GROQ_API_KEY=...                  # required for the judge stage
    python -m evals.build_golden_dataset
    python -m evals.build_golden_dataset --skip-judge
    python -m evals.build_golden_dataset --min-score 4
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.golden_dataset import build_golden  # noqa: E402


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-judge", action="store_true",
                        help="Skip LLM-as-judge scoring (keeps all dedupe survivors).")
    parser.add_argument("--min-score", type=int, default=3,
                        help="Minimum judge score to include in golden dataset (default 3).")
    args = parser.parse_args()

    stats = build_golden(skip_judge=args.skip_judge, min_score=args.min_score)
    print("\n=== Golden dataset build ===")
    print(f"  source cases:    {stats.get('total_in', 0)}")
    print(f"  after dedupe:    {stats.get('after_dedupe', '-')}")
    print(f"  kept (score≥{args.min_score}): {stats.get('kept', 0)}")
    if stats.get("scores"):
        print(f"  score histogram: {stats['scores']}")
    print(f"  written to:      {stats.get('out_path', '-')}")


if __name__ == "__main__":
    main()
