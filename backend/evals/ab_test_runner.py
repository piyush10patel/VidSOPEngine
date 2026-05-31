"""A/B test runner for SOP synthesis models.

Runs SOP generation against multiple model variants and produces a
step-level comparison report with granular per-step scores.

Usage::

    cd backend/evals
    python ab_test_runner.py                         # all test cases, all variants
    python ab_test_runner.py --max-cases 3            # limit test cases
    python ab_test_runner.py --stage sop_synthesis    # specific stage
    python ab_test_runner.py --out report.json        # custom output path

The report is a JSON file with per-case, per-model, per-step scores.
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

# Allow running from backend/evals/ without installing the package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ab_test_runner")


def _load_test_cases(path: str = "tests.json", max_cases: int = 0) -> list:
    """Load promptfoo-format test cases."""
    p = Path(path)
    if not p.exists():
        logger.error(f"Test file not found: {p}")
        sys.exit(1)
    cases = json.loads(p.read_text())
    if max_cases > 0:
        cases = cases[:max_cases]
    logger.info(f"Loaded {len(cases)} test cases from {p}")
    return cases


def _generate_sop(prompt_text: str, model_cfg: dict) -> dict:
    """Call the LLM provider for one SOP generation.

    Returns the parsed SOP dict or an error dict.
    """
    from app.services.llm import get_provider

    provider_name = model_cfg["provider"]
    model_name = model_cfg["model"]
    provider = get_provider(provider_name)

    try:
        t0 = time.time()
        resp = provider.chat(
            prompt_text,
            model=model_name,
            timeout=60,
            response_format={"type": "json_object"},
        )
        latency = time.time() - t0
        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError:
            return {"error": "JSON parse failed", "raw": resp.text[:500], "latency": latency}
        return {
            "output": parsed,
            "latency": round(latency, 2),
            "tokens": {
                "prompt": resp.usage.prompt_tokens,
                "completion": resp.usage.completion_tokens,
                "total": resp.usage.total_tokens,
            },
        }
    except Exception as e:
        return {"error": str(e), "latency": 0}


def _build_prompt(transcript: str, events: str) -> str:
    """Build a simple SOP generation prompt from test case vars."""
    from app.services.sop_generator_service import SOP_GENERATION_PROMPT
    return SOP_GENERATION_PROMPT.format(transcript=transcript)


def run_ab_test(
    cases: list,
    stage: str = "sop_synthesis",
    use_llm_judge: bool = False,
) -> dict:
    """Run A/B test across all model variants for a stage.

    Returns the full report dict.
    """
    from app.services.llm.model_routing import stage_routing
    from app.observability.scorers import (
        step_level_scores,
        step_level_factual_correctness,
    )

    routing = stage_routing(stage)
    all_models = [
        {"provider": routing.default.provider,
         "model": routing.default.model,
         "label": routing.default.label},
    ]
    for v in routing.variants:
        all_models.append({
            "provider": v.provider,
            "model": v.model,
            "label": v.label,
        })

    logger.info(
        f"Stage: {stage} | Models: {[m['label'] for m in all_models]} | "
        f"Cases: {len(cases)} | LLM judge: {use_llm_judge}"
    )

    report = {
        "stage": stage,
        "models": [m["label"] for m in all_models],
        "total_cases": len(cases),
        "cases": [],
        "aggregate": {},
    }

    # Per-model aggregate accumulators
    model_agg = {m["label"]: {
        "precision": [], "recall": [], "f1": [],
        "avg_action_match": [], "avg_tool_grounding": [],
        "latency": [], "total_tokens": [],
        "errors": 0,
    } for m in all_models}

    for ci, case in enumerate(cases):
        transcript = case.get("vars", {}).get("transcript", "")
        events = case.get("vars", {}).get("events", "[]")
        expected_str = case.get("vars", {}).get("expected_output", "{}")
        try:
            expected = json.loads(expected_str)
        except json.JSONDecodeError:
            expected = {}

        prompt = _build_prompt(transcript, events)
        case_id = case.get("metadata", {}).get("case_id", f"case_{ci}")
        logger.info(f"  Case {ci+1}/{len(cases)}: {case_id}")

        case_result = {
            "case_id": case_id,
            "description": case.get("description", ""),
            "models": {},
        }

        for model_cfg in all_models:
            label = model_cfg["label"]
            logger.info(f"    → {label}")
            result = _generate_sop(prompt, model_cfg)

            if "error" in result:
                logger.warning(f"    ✗ {label}: {result['error']}")
                model_agg[label]["errors"] += 1
                case_result["models"][label] = {
                    "error": result["error"],
                    "step_scores": None,
                }
                continue

            output = result["output"]
            latency = result["latency"]
            tokens = result.get("tokens", {})

            # Step-level scoring
            scores = step_level_scores(
                output, expected, transcript, []
            )

            # Optional LLM-as-judge per step
            if use_llm_judge and scores["per_step"]:
                pred_steps = output.get("steps") or output.get("sop") or []
                exp_steps = expected.get("steps") or []
                for step_score in scores["per_step"]:
                    pi, ei = step_score["pred_idx"], step_score["exp_idx"]
                    if pi < len(pred_steps) and ei < len(exp_steps):
                        judge = step_level_factual_correctness(
                            pred_steps[pi], exp_steps[ei], transcript
                        )
                        step_score["llm_judge"] = judge

            case_result["models"][label] = {
                "step_scores": scores,
                "latency": latency,
                "tokens": tokens,
            }

            # Accumulate aggregates
            agg = model_agg[label]
            agg["precision"].append(scores["precision"])
            agg["recall"].append(scores["recall"])
            agg["f1"].append(scores["f1"])
            agg["avg_action_match"].append(scores["avg_action_match"])
            agg["avg_tool_grounding"].append(scores["avg_tool_grounding"])
            agg["latency"].append(latency)
            agg["total_tokens"].append(tokens.get("total", 0))

        report["cases"].append(case_result)

    # Compute aggregates
    def _avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else 0.0

    for label, agg in model_agg.items():
        report["aggregate"][label] = {
            "avg_precision": _avg(agg["precision"]),
            "avg_recall": _avg(agg["recall"]),
            "avg_f1": _avg(agg["f1"]),
            "avg_action_match": _avg(agg["avg_action_match"]),
            "avg_tool_grounding": _avg(agg["avg_tool_grounding"]),
            "avg_latency": _avg(agg["latency"]),
            "avg_tokens": _avg(agg["total_tokens"]),
            "errors": agg["errors"],
            "cases_scored": len(agg["f1"]),
        }

    return report


def main():
    parser = argparse.ArgumentParser(
        description="A/B test SOP synthesis models with step-level scoring"
    )
    parser.add_argument(
        "--tests", default="tests.json",
        help="Path to test cases (default: tests.json)",
    )
    parser.add_argument(
        "--max-cases", type=int, default=0,
        help="Limit number of test cases (0 = all)",
    )
    parser.add_argument(
        "--stage", default="sop_synthesis",
        help="Pipeline stage to test (default: sop_synthesis)",
    )
    parser.add_argument(
        "--llm-judge", action="store_true",
        help="Enable LLM-as-judge for per-step scoring (slower, costlier)",
    )
    parser.add_argument(
        "--out", default="ab_report.json",
        help="Output report path (default: ab_report.json)",
    )
    args = parser.parse_args()

    cases = _load_test_cases(args.tests, args.max_cases)
    if not cases:
        logger.error("No test cases loaded")
        sys.exit(1)

    report = run_ab_test(
        cases,
        stage=args.stage,
        use_llm_judge=args.llm_judge,
    )

    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    logger.info(f"Report written to {args.out}")

    # Print summary table
    print("\n" + "=" * 80)
    print(f"A/B TEST REPORT — {args.stage}")
    print("=" * 80)
    print(f"{'Model':<30} {'F1':>6} {'Prec':>6} {'Rec':>6} "
          f"{'Action':>7} {'Tools':>6} {'Lat(s)':>7} {'Err':>4}")
    print("-" * 80)
    for label, agg in report["aggregate"].items():
        print(f"{label:<30} "
              f"{agg['avg_f1']:>6.3f} "
              f"{agg['avg_precision']:>6.3f} "
              f"{agg['avg_recall']:>6.3f} "
              f"{agg['avg_action_match']:>7.3f} "
              f"{agg['avg_tool_grounding']:>6.3f} "
              f"{agg['avg_latency']:>7.2f} "
              f"{agg['errors']:>4}")
    print("=" * 80)


if __name__ == "__main__":
    main()
