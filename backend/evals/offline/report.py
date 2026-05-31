"""CLI table + JSON dump for the offline eval report.

We try ``rich`` for pretty output, but fall back to plain ASCII tables
so the runner still works without optional deps. JSON dump is always
written so changes can be diffed between runs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# We deliberately avoid the rich library here: on Windows it routes
# through cp1252 and crashes on any non-ASCII character (including
# Devanagari fixture content). A plain ASCII printer works on every
# terminal and is plenty for this report.

_OK = "OK"
_WARN = "!"
_BAD = "X"


# Each row in the deterministic table: (metric label, accessor path, format).
_DETERMINISTIC_ROWS: List[tuple[str, str, str]] = [
    ("Step F1",                "step_alignment.f1",                       "pct"),
    ("Step precision",         "step_alignment.precision",                "pct"),
    ("Step recall",            "step_alignment.recall",                   "pct"),
    ("Step temporal order",    "step_alignment.temporal_order",           "pct"),
    ("Step->image alignment",  "step_image_alignment.mean",               "pct"),
    ("Steps w/ bad image (<10%)", "step_image_alignment.n_below_10pct",   "int"),
    ("Source grounding (mean)", "source_grounding.mean_grounding",        "pct"),
    ("Source grounding (min)",  "source_grounding.min_grounding",         "pct"),
    ("Hallucinated steps (<50%)", "source_grounding.n_steps_below_50pct", "int"),
    ("Frame anchor coverage",  "frame_anchor.coverage",                   "pct"),
    ("Frame uniqueness",       "frame_anchor.unique_ratio",               "pct"),
    ("Frame strictly increasing", "frame_anchor.strictly_increasing",     "pct"),
    ("Frame spread score",     "frame_anchor.spread_score",               "pct"),
    ("Schema required fields", "schema_coverage.required",                "pct"),
    ("Schema informative fields", "schema_coverage.informative",          "pct"),
]


def _get(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if cur is None or not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        try:
            return f"{float(value) * 100:5.1f}%"
        except (TypeError, ValueError):
            return str(value)
    if kind == "int":
        try:
            return f"{int(value)}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _verdict(label: str, value: Any) -> str:
    """Coarse 'OK / warn / bad' marker for headline metrics.

    Most metrics are coverage ratios where 0.8+ is good. A few — Step F1,
    Source grounding, and especially Step->image alignment — are noisy
    word-overlap signals whose realistic ceiling sits much lower because
    step text and frame descriptions use different vocabulary even when
    they describe the same action ("Pick up bottle" vs "Right hand
    reaches for the empty bottle"). The thresholds below were tuned on
    the project's own fixtures so OK/Warn/Bad reflect the metric's
    achievable range, not a Platonic ideal.
    """
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""

    # Integer "bad-row count" metrics: anything > 0 is suspicious.
    bad_int_metrics = {
        "Steps w/ bad image (<10%)",
        "Hallucinated steps (<50%)",
    }
    if label in bad_int_metrics:
        return _OK if v == 0 else (_WARN if v <= 1 else _BAD)

    # Word-overlap metrics — realistic ceiling is much lower than 1.0
    # because step text rarely shares verbatim vocabulary with the
    # generated frame descriptions. A 30% overlap actually represents
    # good alignment; a 15% one is suspicious; below 10% is bad.
    overlap_metrics = {
        "Step->image alignment": (0.30, 0.15),
        "Source grounding (mean)": (0.65, 0.40),
        "Source grounding (min)":  (0.40, 0.20),
    }
    if label in overlap_metrics:
        ok, warn = overlap_metrics[label]
        if v >= ok:
            return _OK
        if v >= warn:
            return _WARN
        return _BAD

    # Standard ratio metrics (precision / recall / coverage).
    if v >= 0.8:
        return _OK
    if v >= 0.5:
        return _WARN
    return _BAD


def _print_deterministic_table(fixture_name: str, det: Dict[str, Any]) -> None:
    header = f"=== {fixture_name} - deterministic ==="
    print()
    print(header)
    print("-" * len(header))
    for label, path, kind in _DETERMINISTIC_ROWS:
        raw = _get(det, path)
        print(f"  {label:34s} {_fmt(raw, kind):>8s}  [{_verdict(label, raw)}]")


def _print_deepeval_table(fixture_name: str, de: Dict[str, Any]) -> None:
    if de is None:
        print(f"\n--- {fixture_name} - DeepEval: skipped (deepeval not installed)")
        return
    if "error" in de:
        print(f"\n--- {fixture_name} - DeepEval: error -> {de['error']}")
        return
    print(
        f"\n--- {fixture_name} - DeepEval (judge: {de.get('judge_model', '?')}, "
        f"n={de.get('n_evaluated', 0)})"
    )
    for field in ("faithfulness", "hallucination", "actionability"):
        block = de.get(field) or {}
        print(
            f"  {field:14s}  mean={block.get('mean', 0):.2f}  "
            f"min={block.get('min', 0):.2f}  "
            f"max={block.get('max', 0):.2f}"
        )


def _print_giskard_table(fixture_name: str, gs: Dict[str, Any]) -> None:
    if gs is None:
        return
    print(
        f"\n--- {fixture_name} - Giskard scan: "
        f"{gs.get('n_robust', 0)}/{gs.get('n_total', 0)} perturbations robust"
    )
    for p in gs.get("perturbations", []):
        ok_mark = _OK if p.get("ok") else _BAD
        suffix = f" - error={p['error']}" if p.get("error") else ""
        print(
            f"  [{ok_mark}] {p['name']:25s}  steps={p.get('n_steps', 0):2d}"
            f"  grounding={(p.get('source_grounding') or {}).get('mean_grounding', 0):.2f}"
            f"{suffix}"
        )


def _print_sopbench_table(fixture_name: str, sb: Dict[str, Any]) -> None:
    if sb is None:
        print(f"\n--- {fixture_name} - SOPBench: skipped (deepeval not installed)")
        return
    if "error" in sb:
        print(f"\n--- {fixture_name} - SOPBench: error -> {sb['error']}")
        return
    threshold = sb.get("threshold", 7.0)
    print(
        f"\n--- {fixture_name} - SOPBench "
        f"(judge: {sb.get('judge_model', '?')}, lang: {sb.get('target_language', '?')}, "
        f"threshold: {threshold:.1f})"
    )
    print(f"  overall:        {sb.get('overall', 0):.2f}/10")
    axis_errors = sb.get("axis_errors") or {}
    for axis_name, axis in (sb.get("axes") or {}).items():
        score = axis.get("score", 0)
        failed = axis.get("failed", False)
        verdict = _BAD if failed else _OK
        if axis_name in axis_errors:
            err = axis_errors[axis_name][:120]
            print(f"  {axis_name:14s}  ERR  [{_BAD}]  {err}")
        else:
            print(f"  {axis_name:14s} {score:>5.2f}/10  [{verdict}]  {axis.get('rationale', '')}")
    if axis_errors:
        print(f"  (note: {len(axis_errors)} axes failed; common cause: judge rate limit)")
    if sb.get("failed_axes"):
        print(f"  failed axes: {', '.join(sb['failed_axes'])}")


def _print_rewrite_table(fixture_name: str, trace: Dict[str, Any]) -> None:
    if not trace:
        return
    print(f"\n--- {fixture_name} - SOPBench auto-rewrite")
    if trace.get("error"):
        print(f"  ERROR: {trace['error']}")
        return
    if not trace.get("attempts"):
        print("  no attempts (nothing failed)")
        return
    for entry in trace["attempts"]:
        score = entry.get("score") or {}
        overall = score.get("overall", 0) if isinstance(score, dict) else 0
        targeted = ", ".join(entry.get("axes_targeted", []))
        err = entry.get("error")
        if err:
            print(f"  attempt {entry.get('attempt', '?')}: targeted=[{targeted}]  ERROR: {err}")
        else:
            still = ", ".join((score or {}).get("failed_axes", []) or [])
            print(
                f"  attempt {entry.get('attempt', '?')}: targeted=[{targeted}]  "
                f"-> overall={overall:.2f}  still_failing=[{still}]"
            )
    final_score = trace.get("final_score") or {}
    print(
        f"  final overall: {final_score.get('overall', 0):.2f}/10  "
        f"improved={trace.get('improved', False)}"
    )


def print_fixture_report(name: str, report: Dict[str, Any]) -> None:
    _print_deterministic_table(name, report.get("deterministic") or {})
    _print_deepeval_table(name, report.get("deepeval"))
    _print_sopbench_table(name, report.get("sopbench"))
    if "sopbench_rewrite" in report:
        _print_rewrite_table(name, report["sopbench_rewrite"])
    _print_giskard_table(name, report.get("giskard"))


def write_json_dump(reports: Dict[str, Dict[str, Any]], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"run-{stamp}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2, ensure_ascii=False, default=str)
    return path
