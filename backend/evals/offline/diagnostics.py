"""Targeted diagnostic for the post-synthesis frame-assignment stage.

Unlike ``runner.py`` (which calls the full DSPy synthesis pipeline and
needs API keys), this script feeds *simulated* synthesis output directly
into ``assign_frame_images_linear`` so the no-LLM portion of the pipeline
can be measured in isolation. Each scenario simulates a different way the
upstream LLM might emit step data — a happy path, a cluster-prone lazy
LLM, sparse evidence, Hindi step text — and runs the same deterministic
metrics the main runner uses on the resulting SOP.

Usage::

    cd backend
    python -m evals.offline.diagnostics
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# Side-step the sop_pipelines/__init__.py import chain (it pulls in DSPy
# via atomic_simple.py). We only need the assignment helper, so load the
# base module by file path instead. Same code; no transitive dspy import.
import importlib.util
_BASE_PATH = BACKEND_ROOT / "app" / "services" / "sop_pipelines" / "base.py"
_spec = importlib.util.spec_from_file_location("sop_pipelines_base_isolated", _BASE_PATH)
_base = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_base)
assign_frame_images_linear = _base.assign_frame_images_linear

from app.schemas.sop import SOPSchema, SOPStep

from evals.offline.metrics.deterministic import run_deterministic


def _to_step(d: Dict[str, Any]) -> SOPStep:
    """Build an SOPStep from a partial dict (matches the schema)."""
    return SOPStep(
        step_number=d["step_number"],
        title=d.get("title", ""),
        description=d.get("description", ""),
        tools=d.get("tools", []),
        checks=d.get("checks", []),
        evidence=d.get("evidence", []),
        source_frame_num=d.get("source_frame_num"),
    )


def _build_synthetic_sop(steps: List[dict]) -> SOPSchema:
    return SOPSchema(
        title="synthetic",
        description="",
        steps=[_to_step(s) for s in steps],
    )


def _run(fixture: dict, predicted_steps: List[dict]) -> Dict[str, Any]:
    sop = _build_synthetic_sop(predicted_steps)
    assign_frame_images_linear(sop.steps, fixture["frame_observations"])
    predicted_dict = sop.model_dump()
    metrics = run_deterministic(fixture, predicted_dict)
    return {
        "assigned_frames": [s.source_frame_num for s in sop.steps],
        "assigned_images": [
            Path(s.image_url).name if s.image_url else None
            for s in sop.steps
        ],
        "metrics": metrics,
    }


def _load_fixture(name: str) -> dict:
    path = HERE / "fixtures" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_field(steps: List[dict], field: str) -> List[dict]:
    out = deepcopy(steps)
    for s in out:
        s.pop(field, None)
    return out


def _override_field(steps: List[dict], field: str, values: List[Any]) -> List[dict]:
    out = deepcopy(steps)
    for s, v in zip(out, values):
        s[field] = v
    return out


def _headline(metrics: Dict[str, Any]) -> str:
    fa = metrics["frame_anchor"]
    sia = metrics["step_image_alignment"]
    sg = metrics["source_grounding"]
    return (
        f"unique={fa['unique_ratio']*100:5.1f}%  "
        f"incr={fa['strictly_increasing']*100:5.1f}%  "
        f"spread={fa['spread_score']*100:5.1f}%  "
        f"img_align(mean)={sia['mean']*100:5.1f}%  "
        f"bad_img={sia['n_below_10pct']:d}  "
        f"grounding(mean)={sg['mean_grounding']*100:5.1f}%"
    )


def main() -> int:
    fixture = _load_fixture("uneven_pacing")
    expected_steps = fixture["expected_sop"]["steps"]

    scenarios = []

    # 1. Best case: synthesis perfectly anchored every step.
    scenarios.append(("clean LLM output (perfect)", expected_steps))

    # 2. Lazy LLM: every closing step pinned to the last frame.
    scenarios.append((
        "lazy LLM (last 4 steps -> frame 8)",
        _override_field(
            expected_steps,
            "source_frame_num",
            [1, 2, 8, 8, 8, 8],
        ),
    ))

    # 3. Lazy LLM: every step pinned to frame 1.
    scenarios.append((
        "very lazy LLM (every step -> frame 1)",
        _override_field(
            expected_steps,
            "source_frame_num",
            [1, 1, 1, 1, 1, 1],
        ),
    ))

    # 4. Reverse-monotonic (model emitted decreasing values).
    scenarios.append((
        "reverse-monotonic LLM output",
        _override_field(
            expected_steps,
            "source_frame_num",
            [8, 7, 6, 5, 3, 1],
        ),
    ))

    # 5. No explicit field, only evidence citations.
    no_explicit = _strip_field(expected_steps, "source_frame_num")
    no_explicit = _override_field(
        no_explicit,
        "evidence",
        [["Frame 1"], ["Frame 2"], ["Frame 3"], ["Frame 5"], ["Frame 7"], ["Frame 8"]],
    )
    scenarios.append(("no explicit, evidence citations only", no_explicit))

    # 6. No explicit, no evidence — pure content match.
    no_signal = _strip_field(_strip_field(expected_steps, "source_frame_num"), "evidence")
    scenarios.append(("no explicit, no evidence (content match)", no_signal))

    # 7. Hindi step text (vocabulary mismatch with English frame descriptions).
    hindi = _strip_field(expected_steps, "source_frame_num")
    hindi_titles = [
        ("स्क्रूड्राइवर लें",      "वर्कबेंच से फिलिप्स स्क्रूड्राइवर उठाएं।"),
        ("रिंच लें",              "वर्कबेंच से रिंच उठाएं।"),
        ("कपड़ा लें",             "वर्कबेंच से कपड़ा उठाएं।"),
        ("मोटर साफ करें",         "मोटर हाउसिंग के हर तरफ कपड़े से साफ करें।"),
        ("निरीक्षण करें",         "साफ की हुई मोटर हाउसिंग की जांच करें।"),
        ("रैक पर रखें",           "साफ की हुई मोटर हाउसिंग को रैक पर रखें।"),
    ]
    for s, (t, d) in zip(hindi, hindi_titles):
        s["title"] = t
        s["description"] = d
    hindi = _strip_field(hindi, "evidence")
    scenarios.append(("Hindi step text, no explicit, no evidence", hindi))

    print()
    print("=" * 100)
    print(f"Fixture: {fixture['name']}  ({len(fixture['frame_observations'])} frames, "
          f"{len(expected_steps)} expected steps)")
    print("=" * 100)
    print()

    all_results = {}
    for name, steps in scenarios:
        result = _run(fixture, steps)
        all_results[name] = result
        emitted = [s.get("source_frame_num") for s in steps]
        assigned = result["assigned_frames"]
        print(f"  [{name}]")
        print(f"    LLM emitted source_frame_num: {emitted}")
        print(f"    Final assigned             : {assigned}")
        print(f"    {_headline(result['metrics'])}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
