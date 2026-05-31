"""Run DSPy MIPROv2 optimization against the failure dataset.

Saves the optimized SOPGenerationPipeline as JSON for later loading.
Bails if dataset has fewer than MIN_CASES (won't produce a useful optimizer).

Usage:
    cd backend
    export GROQ_API_KEY=...
    python -m evals.dspy_optimize
    python -m evals.dspy_optimize --output optimized_v2.json --min-cases 10
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dspy  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.datasets.dspy_examples import sop_match_metric, to_dspy_examples  # noqa: E402
from app.datasets.failures import load_all  # noqa: E402
from app.dspy_modules.pipeline import SOPGenerationPipeline  # noqa: E402

MIN_CASES_DEFAULT = 20
TRAIN_RATIO = 0.8


def _load_cases(source: str):
    """Load training cases from the requested source.

    - ``auto``: prefer golden_dataset.jsonl when it exists, else failures.jsonl
    - ``golden``: only golden_dataset.jsonl (errors out if missing)
    - ``failures``: raw failures.jsonl (the legacy path, pre-golden)
    """
    import json as _json
    from pathlib import Path
    from app.schemas.failure import FailureCase

    base = Path(settings.upload_dir).parent
    golden_path = base / "golden_dataset.jsonl"

    if source == "failures":
        cases = load_all()
        print(f"Loaded {len(cases)} cases from failures.jsonl (forced --source failures)")
        return cases

    if source == "golden" and not golden_path.exists():
        print(
            f"ERROR: --source golden requested but {golden_path} doesn't exist.\n"
            "       Run `python -m evals.build_golden_dataset` first.",
            file=sys.stderr,
        )
        sys.exit(2)

    if golden_path.exists() and source in ("auto", "golden"):
        cases = []
        with golden_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = _json.loads(line)
                    # FailureCase ignores unknown fields (golden_score etc.)
                    cases.append(FailureCase.model_validate(payload))
                except Exception as exc:
                    print(f"  WARN skipping malformed golden row: {exc}", file=sys.stderr)
        print(f"Loaded {len(cases)} cases from golden_dataset.jsonl")
        return cases

    cases = load_all()
    print(f"Loaded {len(cases)} cases from failures.jsonl (golden_dataset.jsonl not found)")
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="optimized_pipeline.json",
                        help="Path to write optimized pipeline state")
    parser.add_argument("--min-cases", type=int, default=MIN_CASES_DEFAULT,
                        help=f"Minimum dataset size to attempt optimization (default {MIN_CASES_DEFAULT})")
    parser.add_argument("--num-candidates", type=int, default=10,
                        help="Number of prompt variants MIPROv2 explores")
    parser.add_argument("--video-type", choices=["ui", "physical"], default=None,
                        help="Train only on cases of this video_type (default: all)")
    parser.add_argument(
        "--source",
        choices=["auto", "golden", "failures"],
        default="auto",
        help=(
            "Dataset to train against. 'auto' (default) reads golden_dataset.jsonl "
            "if present, else falls back to failures.jsonl. 'golden' requires the "
            "file to exist (build with evals/build_golden_dataset.py). 'failures' "
            "preserves the legacy path for cold-start runs."
        ),
    )
    args = parser.parse_args()

    if not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    cases = _load_cases(args.source)
    if args.video_type:
        cases = [c for c in cases if (c.video_type or "physical") == args.video_type]
        print(f"Filtered to video_type={args.video_type}: {len(cases)} cases")

    if len(cases) < args.min_cases:
        print(f"SKIP: only {len(cases)} cases in dataset, need {args.min_cases}+ for MIPROv2")
        print("Record more failures via POST /failures or via auto-capture, then re-run.")
        sys.exit(0)

    examples = to_dspy_examples(cases)
    split = int(len(examples) * TRAIN_RATIO)
    trainset, valset = examples[:split], examples[split:]
    print(f"Loaded {len(examples)} examples → train={len(trainset)} val={len(valset)}")

    lm = dspy.LM(
        f"groq/{settings.llm_model}",
        api_key=settings.groq_api_key,
        temperature=0,
    )
    dspy.configure(lm=lm)

    base = SOPGenerationPipeline(strategy="predict", use_few_shot=False)

    print(f"Running MIPROv2 with {args.num_candidates} candidates...")
    try:
        from dspy.teleprompt import MIPROv2
        optimizer = MIPROv2(
            metric=sop_match_metric,
            num_candidates=args.num_candidates,
            init_temperature=0.7,
        )
    except ImportError:
        from dspy.teleprompt import BootstrapFewShot
        print("MIPROv2 unavailable — falling back to BootstrapFewShot")
        optimizer = BootstrapFewShot(metric=sop_match_metric, max_bootstrapped_demos=4)

    optimized = optimizer.compile(base, trainset=trainset, valset=valset if hasattr(optimizer, 'valset') else None)

    optimized.save(args.output)
    print(f"Optimized pipeline saved → {args.output}")
    print(f"Load at runtime: pipeline.load('{args.output}')")


if __name__ == "__main__":
    main()
