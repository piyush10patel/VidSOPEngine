"""Close-the-loop orchestrator: dataset → Promptfoo → DSPy → Braintrust.

Runs the full evaluation + optimization cycle in order and prints a verdict.
Each stage is skipped (with a clear message) if its prerequisite is missing.

Usage:
    cd backend
    export GROQ_API_KEY=...
    export BRAINTRUST_API_KEY=...        # optional — Braintrust stage skipped if unset
    python -m evals.close_loop
    python -m evals.close_loop --skip-promptfoo --skip-braintrust   # DSPy only
"""
import argparse
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.datasets.failures import load_all  # noqa: E402

EVALS_DIR = Path(__file__).parent
MIN_FOR_OPTIMIZATION = 20


def _section(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{title}\n{bar}")


def stage_dataset_summary() -> int:
    _section("STAGE 1 — Dataset summary")
    cases = load_all()
    print(f"Total cases: {len(cases)}")
    if cases:
        by_type = Counter(c.failure_type.value for c in cases)
        by_severity = Counter(c.severity.value for c in cases)
        print(f"  by failure_type: {dict(by_type)}")
        print(f"  by severity:     {dict(by_severity)}")
        auto = sum(1 for c in cases if c.notes and c.notes.startswith("Auto-captured"))
        print(f"  auto-captured:   {auto}")
        print(f"  human-marked:    {len(cases) - auto}")
    return len(cases)


def stage_build_golden_dataset(*, skip_judge: bool, min_score: int) -> bool:
    """Stage 1.5 — PII strip + dedup + LLM-as-judge → golden_dataset.jsonl.

    Runs offline against the immutable failures.jsonl (INV-4) and writes
    a separate file. DSPy MIPROv2 (stage 3) consumes the golden output
    when --use-golden is set, so this stage gates training-quality.
    """
    _section("STAGE 1.5 — Build golden dataset (PII strip + dedup + judge)")
    if skip_judge and not settings.groq_api_key:
        # Explicit "yes I know there's no judge" path — still useful for
        # dedupe-only runs while you wait for a Groq key.
        print("Running PII strip + dedupe only (--skip-judge).")
    elif not settings.groq_api_key:
        print("SKIP: GROQ_API_KEY not set; cannot score with LLM-as-judge.")
        print("To run PII strip + dedupe only, pass --skip-golden-judge.")
        return False
    from app.services.golden_dataset import build_golden
    stats = build_golden(skip_judge=skip_judge, min_score=min_score)
    print(f"  source cases:    {stats.get('total_in', 0)}")
    print(f"  after dedupe:    {stats.get('after_dedupe', '-')}")
    print(f"  kept (score≥{min_score}): {stats.get('kept', 0)}")
    if stats.get("scores"):
        print(f"  score histogram: {stats['scores']}")
    print(f"  written to:      {stats.get('out_path', '-')}")
    return stats.get("kept", 0) > 0


def stage_promptfoo() -> bool:
    _section("STAGE 2 — Promptfoo eval (prompt variations)")
    if shutil.which("npx") is None:
        print("SKIP: npx not on PATH (install Node.js to run Promptfoo)")
        return False

    print("Rebuilding tests.json from current failures.jsonl...")
    subprocess.run([sys.executable, "export_dataset.py"], cwd=EVALS_DIR, check=True)

    print("Running promptfoo eval (this can take several minutes)...")
    result = subprocess.run(
        ["npx", "promptfoo@latest", "eval", "--no-progress-bar"],
        cwd=EVALS_DIR,
    )
    if result.returncode == 0:
        print("Promptfoo eval complete. View with: cd evals && npx promptfoo view")
        return True
    print(f"Promptfoo eval exited with code {result.returncode}")
    return False


def stage_dspy_optimize() -> bool:
    _section("STAGE 3 — DSPy MIPROv2 optimization")
    cases = load_all()
    if len(cases) < MIN_FOR_OPTIMIZATION:
        print(f"SKIP: only {len(cases)} cases, need {MIN_FOR_OPTIMIZATION}+ for MIPROv2.")
        print("Generate more failures (auto-capture or POST /failures) and re-run.")
        return False

    output_path = EVALS_DIR / "optimized_pipeline.json"
    print(f"Optimizing → {output_path}")
    result = subprocess.run(
        [sys.executable, "-m", "evals.dspy_optimize", "--output", str(output_path)],
        cwd=ROOT,
    )
    if result.returncode == 0 and output_path.exists():
        print(f"Optimized pipeline saved: {output_path}")
        return True
    print("Optimization failed.")
    return False


def stage_braintrust() -> bool:
    _section("STAGE 4 — Braintrust experiment (auto-scored: factual + relevance + hallucination)")
    if not settings.braintrust_api_key:
        print("SKIP: BRAINTRUST_API_KEY not set")
        return False

    result = subprocess.run(
        [sys.executable, "-m", "evals.braintrust_eval"],
        cwd=ROOT,
    )
    return result.returncode == 0


def stage_verdict(stages: dict) -> None:
    _section("VERDICT")
    for name, ok in stages.items():
        status = "✓" if ok else "✗ skipped/failed"
        print(f"  {status:25s} {name}")
    print(
        "\nNext steps:"
        "\n  - Inspect Promptfoo: cd backend/evals && npx promptfoo view"
        "\n  - Inspect Braintrust: https://www.braintrust.dev"
        "\n  - Refine auto-captured cases with the real expected_output via POST /failures"
        "\n  - To use the optimized pipeline at runtime, load it in SOPGenerationPipeline"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-promptfoo", action="store_true")
    parser.add_argument("--skip-dspy", action="store_true")
    parser.add_argument("--skip-braintrust", action="store_true")
    parser.add_argument(
        "--skip-golden", action="store_true",
        help="Skip the PII-strip + dedup + judge stage; DSPy reads failures.jsonl directly.",
    )
    parser.add_argument(
        "--skip-golden-judge", action="store_true",
        help="Run PII strip + dedupe but skip LLM-as-judge (no Groq calls).",
    )
    parser.add_argument(
        "--golden-min-score", type=int, default=3,
        help="Minimum LLM-as-judge score to include in the golden dataset (default 3).",
    )
    args = parser.parse_args()

    n = stage_dataset_summary()
    stages = {"dataset summary": True}

    if not args.skip_golden and n > 0:
        stages["golden dataset"] = stage_build_golden_dataset(
            skip_judge=args.skip_golden_judge,
            min_score=args.golden_min_score,
        )

    if not args.skip_promptfoo:
        stages["promptfoo eval"] = stage_promptfoo()
    if not args.skip_dspy and n >= MIN_FOR_OPTIMIZATION:
        stages["dspy optimization"] = stage_dspy_optimize()
    elif not args.skip_dspy:
        stages["dspy optimization"] = False
        print(f"\n(Skipping DSPy: dataset has {n} cases, need {MIN_FOR_OPTIMIZATION}+)")
    if not args.skip_braintrust:
        stages["braintrust experiment"] = stage_braintrust()

    stage_verdict(stages)


if __name__ == "__main__":
    main()
