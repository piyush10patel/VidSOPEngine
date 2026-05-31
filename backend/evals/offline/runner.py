"""Offline eval runner for the Video → SOP pipeline.

Usage (from ``backend/``)::

    python -m evals.offline.runner --all
    python -m evals.offline.runner --fixtures physical_simple,hindi_simple
    python -m evals.offline.runner --skip-llm           # deterministic only
    python -m evals.offline.runner --skip-giskard
    python -m evals.offline.runner --skip-synthesis     # score the fixture's
                                                        # baseline_sop instead
                                                        # of calling DSPy

Loads every JSON file under ``evals/offline/fixtures/`` (or the subset
named after ``--fixtures``), calls the live DSPy synthesis pipeline on
the fixture's transcript + frame_observations, scores the output with
every enabled metric, and prints a table per fixture plus a JSON dump.

The runner is deliberately decoupled from FastAPI / SQLAlchemy: it
imports ``app.dspy_modules.pipeline`` directly so no DB / Redis / R2
is required.
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── Resolve paths so this runs from any cwd ──────────────────────────
HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

FIXTURES_DIR = HERE / "fixtures"
REPORTS_DIR = HERE / "reports"

logger = logging.getLogger("evals.offline.runner")


# Auto-load backend/.env so the same GROQ_API_KEY / TOGETHER_API_KEY
# already used by production also works for the eval suite without the
# user having to ``export`` manually. dotenv is optional — if it's not
# installed the runner still works, just expects the env vars to be set
# in the shell. dotenv.load_dotenv silently does nothing if the file is
# missing, so this is also safe in CI where keys come from secrets.
def _load_dotenv_quietly() -> None:
    env_path = BACKEND_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Soft fallback: parse KEY=VALUE lines ourselves. Same behaviour
        # as load_dotenv for the simple case (no quotes, no exports, no
        # variable interpolation) and avoids forcing python-dotenv as a
        # hard dependency.
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Don't clobber an explicit export from the shell.
                os.environ.setdefault(key, value)
        except OSError:
            pass
        return
    load_dotenv(env_path, override=False)


_load_dotenv_quietly()


# ── Lazy imports so the deterministic path works even when dspy / app
#    settings / litellm aren't importable. ─────────────────────────────


def _load_fixture(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("name", path.stem)
    return data


def _collect_fixtures(names: Optional[List[str]]) -> List[dict]:
    if not FIXTURES_DIR.exists():
        return []
    paths = sorted(FIXTURES_DIR.glob("*.json"))
    if names:
        wanted = set(names)
        paths = [p for p in paths if p.stem in wanted]
    return [_load_fixture(p) for p in paths]


def _build_synthesis_callable(use_self_check: bool = False) -> Callable[[dict], Optional[dict]]:
    """Return a function that takes a fixture dict and returns a SOP dict.

    Lazy imports so a missing ``dspy`` / ``litellm`` install doesn't kill
    the runner; ``--skip-synthesis`` is the escape hatch for that case.
    """
    from app.dspy_modules.pipeline import FullSOPPipeline
    from app.services.llm.dspy_config import configure_dspy
    from app.services.sop_pipelines.base import parse_sop_response, assign_frame_images_linear

    configure_dspy(task="sop")

    def _run(fixture: dict) -> Optional[dict]:
        pipeline = FullSOPPipeline(event_strategy="predict", sop_strategy="predict")
        result = pipeline(
            transcript=fixture.get("transcript", ""),
            frame_observations=fixture.get("frame_observations", []),
        )
        raw = json.dumps({
            "title": result["title"],
            "summary": result["summary"],
            "sop": result["steps"],
            "overall_confidence": result.get("overall_confidence", 1.0),
            "warnings": result.get("warnings", []),
            "needs_review": result.get("needs_review", False),
        })
        sop = parse_sop_response(raw, video_type="physical")
        assign_frame_images_linear(sop.steps, fixture.get("frame_observations", []))
        return sop.model_dump()

    return _run


def _eval_one_fixture(
    fixture: dict,
    run_synthesis: Callable[[dict], Optional[dict]],
    *,
    skip_llm: bool = False,
    skip_giskard: bool = False,
    skip_synthesis: bool = False,
    skip_sopbench: bool = False,
    auto_rewrite: bool = False,
    rewrite_max_attempts: int = 2,
) -> Dict[str, Any]:
    """Run every enabled metric against one fixture."""
    from evals.offline.metrics.deterministic import run_deterministic
    from evals.offline.metrics.deepeval_metrics import run_deepeval
    from evals.offline.metrics.giskard_scan import run_giskard_scan
    from evals.offline.metrics.sopbench import run_sopbench

    if skip_synthesis:
        predicted_sop = fixture.get("baseline_sop") or fixture.get("expected_sop") or {}
        synthesis_error = None
    else:
        try:
            predicted_sop = run_synthesis(fixture) or {}
            synthesis_error = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Synthesis failed for fixture %s", fixture.get("name"))
            predicted_sop = {}
            synthesis_error = type(exc).__name__ + ": " + str(exc)[:200]

    report: Dict[str, Any] = {
        "predicted_sop": predicted_sop,
        "deterministic": run_deterministic(fixture, predicted_sop),
    }
    if synthesis_error:
        report["synthesis_error"] = synthesis_error

    if not skip_llm:
        report["deepeval"] = run_deepeval(fixture, predicted_sop)

    if not skip_sopbench and not skip_llm:
        sopbench = run_sopbench(fixture, predicted_sop)
        report["sopbench"] = sopbench

        if auto_rewrite and sopbench and sopbench.get("failed_axes"):
            from evals.offline.rewriter import auto_rewrite as _auto_rewrite

            def _rescore(sop: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                return run_sopbench(fixture, sop)

            rewrite_trace = _auto_rewrite(
                fixture=fixture,
                initial_sop=predicted_sop,
                initial_score=sopbench,
                rescore=_rescore,
                max_attempts=rewrite_max_attempts,
            )
            report["sopbench_rewrite"] = rewrite_trace
            # Replace the report's predicted_sop with the final rewritten
            # version so downstream serialisation reflects what the user
            # would actually persist.
            if rewrite_trace.get("final_sop"):
                report["predicted_sop_after_rewrite"] = rewrite_trace["final_sop"]
                report["sopbench_after_rewrite"] = rewrite_trace.get("final_score")

    if not skip_giskard:
        # Giskard re-runs synthesis on perturbed inputs — only enable
        # when synthesis is actually working.
        if skip_synthesis or synthesis_error:
            report["giskard"] = {
                "perturbations": [],
                "n_robust": 0,
                "n_total": 0,
                "note": "synthesis disabled, scan skipped",
            }
        else:
            report["giskard"] = run_giskard_scan(fixture, run_synthesis)

    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline eval runner for the Video → SOP pipeline.",
    )
    parser.add_argument(
        "--fixtures",
        type=str,
        default=None,
        help="Comma-separated fixture names (stem of file in evals/offline/fixtures/).",
    )
    parser.add_argument("--all", action="store_true", help="Run every fixture.")
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip DeepEval (no judge calls). Useful when GROQ_API_KEY is missing.",
    )
    parser.add_argument(
        "--skip-giskard",
        action="store_true",
        help="Skip Giskard adversarial scan (5x synthesis calls per fixture).",
    )
    parser.add_argument(
        "--skip-sopbench",
        action="store_true",
        help="Skip the SOPBench-inspired 4-axis quality scorer.",
    )
    parser.add_argument(
        "--auto-rewrite",
        action="store_true",
        help=(
            "When SOPBench flags failing axes, ask the Groq judge to patch "
            "the SOP and re-score. Bounded by --rewrite-max-attempts."
        ),
    )
    parser.add_argument(
        "--rewrite-max-attempts",
        type=int,
        default=2,
        help="Maximum SOPBench rewrite rounds per fixture (default 2).",
    )
    parser.add_argument(
        "--skip-synthesis",
        action="store_true",
        help=(
            "Skip live DSPy synthesis and score the fixture's `baseline_sop` "
            "block instead. Useful for testing the harness without API keys."
        ),
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Don't write the JSON dump (table only).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG-level logs.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    names = (
        [n.strip() for n in args.fixtures.split(",") if n.strip()]
        if args.fixtures
        else None
    )
    if not args.all and not names:
        # Default: run every fixture.
        names = None

    fixtures = _collect_fixtures(names)
    if not fixtures:
        print("No fixtures found. Add JSON files under evals/offline/fixtures/.")
        return 1

    # Pre-flight: tell the user which tiers will actually run BEFORE we
    # burn any judge tokens. Common failure mode otherwise is "I ran the
    # full suite but nothing scored because deepeval wasn't installed
    # and we silently skipped". Surface that up front.
    groq_set = bool(os.environ.get("GROQ_API_KEY"))
    try:
        import deepeval  # noqa: F401
        deepeval_ready = True
    except ImportError:
        deepeval_ready = False
    print()
    print("Tiers enabled for this run:")
    print(f"  Deterministic                 ON  (no API key required)")
    if args.skip_llm or not deepeval_ready or not groq_set:
        reason = (
            "--skip-llm" if args.skip_llm
            else "deepeval not installed (pip install -r requirements-eval.txt)"
            if not deepeval_ready
            else "GROQ_API_KEY not set"
        )
        print(f"  DeepEval + SOPBench           SKIPPED  ({reason})")
    else:
        print(f"  DeepEval + SOPBench           ON  (judge=groq, key found)")
    if args.skip_giskard or args.skip_synthesis:
        reason = "--skip-giskard" if args.skip_giskard else "synthesis disabled"
        print(f"  Giskard adversarial scan      SKIPPED  ({reason})")
    else:
        print(f"  Giskard adversarial scan      ON  (5x synthesis calls per fixture)")
    if args.auto_rewrite and not (deepeval_ready and groq_set):
        print(f"  Auto-rewrite                  SKIPPED  (needs SOPBench, which is off)")
    elif args.auto_rewrite:
        print(f"  Auto-rewrite                  ON  (max {args.rewrite_max_attempts} attempts per fixture)")
    print()

    if args.skip_synthesis:
        def run_synthesis(_: dict) -> dict:
            return {}
    else:
        try:
            run_synthesis = _build_synthesis_callable()
        except Exception as exc:  # noqa: BLE001
            print(
                f"Could not initialise synthesis pipeline: {exc}\n"
                "Run with --skip-synthesis to use the fixture's baseline_sop instead."
            )
            return 2

    from evals.offline.report import print_fixture_report, write_json_dump

    all_reports: Dict[str, Dict[str, Any]] = {}
    for fx in fixtures:
        name = fx["name"]
        print(f"\n========== fixture: {name} ==========")
        report = _eval_one_fixture(
            fx,
            run_synthesis,
            skip_llm=args.skip_llm,
            skip_giskard=args.skip_giskard,
            skip_synthesis=args.skip_synthesis,
            skip_sopbench=args.skip_sopbench,
            auto_rewrite=args.auto_rewrite,
            rewrite_max_attempts=args.rewrite_max_attempts,
        )
        all_reports[name] = report
        print_fixture_report(name, report)

    if not args.no_json:
        path = write_json_dump(all_reports, REPORTS_DIR)
        print(f"\nJSON dump written to {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
