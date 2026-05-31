"""CI quality gate — runs the deterministic scorers on every fixture.

Zero LLM calls. Designed for GitHub Actions free tier so the gate fires
on every PR without burning any credits.

What it checks (per fixture):
    1. Fixture JSON is well-formed and has every required field.
    2. The fixture's ``baseline_sop`` scores above configured thresholds
       on each deterministic metric (step alignment F1, source grounding,
       schema coverage, frame anchoring).
    3. The deterministic harness itself imports and runs cleanly — i.e.
       a refactor that breaks ``run_deterministic`` is caught here.

Exit code:
    0 — all fixtures cleared every threshold
    1 — at least one fixture failed (table printed with the offenders)

Usage::

    python -m evals.ci_gate
    python -m evals.ci_gate --min-f1 0.6      # relax for an exploratory PR
    python -m evals.ci_gate --strict          # require 1.0 on schema coverage
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parent
FIXTURES_DIR = HERE / "offline" / "fixtures"

# Make `evals.*` and `app.*` importable when this file is invoked as
# `python -m evals.ci_gate` from the backend/ directory.
sys.path.insert(0, str(BACKEND_ROOT))


# Default thresholds. Tuned to the current baseline_sop fixtures so a
# clean main always passes with a small amount of headroom. Lower a
# floor only with a commit-message comment explaining which fixture /
# metric regressed and why the new floor is acceptable. Raising a
# floor is the desired direction over time.
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "step_alignment.f1": 0.90,
    "source_grounding.mean_grounding": 0.60,
    "schema_coverage.required": 1.00,
    "frame_anchor.strictly_increasing": 0.55,
    "step_image_alignment.mean": 0.50,
}


def _load_fixtures() -> List[dict]:
    if not FIXTURES_DIR.exists():
        print(f"FAIL: fixtures dir not found: {FIXTURES_DIR}")
        sys.exit(1)
    fixtures = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"FAIL: invalid JSON in {path.name}: {e}")
            sys.exit(1)
        data["__path"] = str(path)
        fixtures.append(data)
    if not fixtures:
        print(f"FAIL: no fixtures found in {FIXTURES_DIR}")
        sys.exit(1)
    return fixtures


def _require_fields(fixture: dict, fields: List[str]) -> List[str]:
    """Return a list of human-readable problems with the fixture."""
    problems = []
    for f in fields:
        node: Any = fixture
        for key in f.split("."):
            if not isinstance(node, dict) or key not in node:
                problems.append(f"missing field: {f}")
                break
            node = node[key]
    return problems


def _read_metric(report: Dict[str, Any], dotted: str) -> float | None:
    """Walk a dotted path through nested dicts. Return None if any step misses."""
    node: Any = report
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return float(node) if isinstance(node, (int, float)) else None


def _gate_fixture(
    fixture: dict,
    thresholds: Dict[str, float],
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """Run deterministic scorers on the fixture's baseline_sop.

    Returns (passed, raw_report, list_of_threshold_failures).
    """
    # Hard schema check on the fixture itself first.
    problems = _require_fields(fixture, [
        "name", "transcript", "frame_observations",
        "expected_sop.steps", "baseline_sop.steps",
    ])
    if problems:
        return False, {}, problems

    from evals.offline.metrics.deterministic import run_deterministic

    report = run_deterministic(fixture, fixture["baseline_sop"])
    failures: List[str] = []
    for metric_path, floor in thresholds.items():
        value = _read_metric(report, metric_path)
        if value is None:
            failures.append(f"{metric_path}: missing from report")
        elif value < floor:
            failures.append(f"{metric_path}: {value:.3f} < {floor:.3f}")
    return (not failures), report, failures


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-f1", type=float, default=None,
                        help="Override step_alignment.f1 floor.")
    parser.add_argument("--strict", action="store_true",
                        help="Require 1.0 on schema_coverage + 0.9 on grounding.")
    parser.add_argument("--show-report", action="store_true",
                        help="Print the full per-fixture metric dump on success too.")
    args = parser.parse_args(argv)

    thresholds = dict(DEFAULT_THRESHOLDS)
    if args.min_f1 is not None:
        thresholds["step_alignment.f1"] = args.min_f1
    if args.strict:
        thresholds["source_grounding.mean_grounding"] = 0.85
        thresholds["step_image_alignment.mean"] = 0.60

    print("== Thresholds ==")
    for k, v in thresholds.items():
        print(f"  {k:36s} >= {v:.2f}")
    print()

    fixtures = _load_fixtures()
    print(f"Loaded {len(fixtures)} fixture(s) from {FIXTURES_DIR.name}/")
    print()

    all_passed = True
    for fixture in fixtures:
        name = fixture.get("name") or Path(fixture["__path"]).stem
        passed, report, failures = _gate_fixture(fixture, thresholds)
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed:
            all_passed = False
            for line in failures:
                print(f"        {line}")
        if args.show_report or not passed:
            # Print the key scores so a developer can see what landed.
            for path in thresholds.keys():
                value = _read_metric(report, path)
                if value is not None:
                    print(f"        {path:36s} = {value:.3f}")
        print()

    if all_passed:
        print("CI gate: PASS")
        return 0
    print("CI gate: FAIL — see threshold violations above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
