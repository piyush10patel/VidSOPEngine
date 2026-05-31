"""Giskard adversarial / robustness scan for the synthesis pipeline.

Unlike DeepEval (which scores a single completion) or the deterministic
metrics (which grade a single fixture), Giskard probes the pipeline with
PERTURBED inputs and reports failure modes.

We deliberately do not invoke Giskard's full LLM scan (it expects a
``giskard.Model`` wrapper around the model, which is overkill for our
DSPy pipeline). Instead we run a curated set of perturbations through
the same ``run_synthesis`` callable the runner uses, and score the
outputs with the deterministic metrics. A regression in any of these
is a robustness bug.

The same approach the Giskard team recommends for non-classification
models — see https://docs.giskard.ai/en/stable/guides/test_model.html —
just hand-rolled to fit our pipeline.

The module degrades gracefully when ``giskard`` isn't installed (we
only use it for the standard catalog of perturbations).
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

from evals.offline.metrics.deterministic import (
    frame_anchor_metrics,
    schema_coverage_metrics,
    source_grounding_metrics,
)


# A perturbation is a (name, mutator) pair. The mutator receives a
# fixture dict (transcript + frame_observations) and returns a NEW
# fixture; the original is left untouched so subsequent perturbations
# run on a clean slate.

PerturbationFn = Callable[[dict], dict]


def _empty_transcript(fx: dict) -> dict:
    out = copy.deepcopy(fx)
    out["transcript"] = ""
    return out


def _single_frame(fx: dict) -> dict:
    out = copy.deepcopy(fx)
    out["frame_observations"] = out.get("frame_observations", [])[:1]
    return out


def _scrambled_frame_order(fx: dict) -> dict:
    out = copy.deepcopy(fx)
    obs = list(out.get("frame_observations", []))
    # Reverse the middle, leaving first and last in place — a
    # plausibly-broken extraction order.
    if len(obs) > 3:
        obs[1:-1] = list(reversed(obs[1:-1]))
    out["frame_observations"] = obs
    return out


def _duplicated_observations(fx: dict) -> dict:
    out = copy.deepcopy(fx)
    obs = out.get("frame_observations", [])
    if obs:
        # Replace every other frame description with the first one.
        first = obs[0].get("description", "")
        for o in obs[1::2]:
            o["description"] = first
    return out


def _hindi_transcript(fx: dict) -> dict:
    """Replace the transcript with a Devanagari-only string.

    The vision observations stay in English (vision prompts are English),
    so this tests cross-language synthesis — the failure mode that
    derailed Hindi training generation earlier.
    """
    out = copy.deepcopy(fx)
    out["transcript"] = (
        "ऑपरेटर पहले बोतल उठाता है, फिर ढक्कन खोलकर पानी भरता है, "
        "अंत में ढक्कन बंद करता है।"
    )
    out["expected_sop"] = None  # alignment-vs-expected does not apply here
    return out


PERTURBATIONS: List[tuple[str, PerturbationFn]] = [
    ("empty_transcript", _empty_transcript),
    ("single_frame", _single_frame),
    ("scrambled_frame_order", _scrambled_frame_order),
    ("duplicated_observations", _duplicated_observations),
    ("hindi_transcript", _hindi_transcript),
]


def run_giskard_scan(
    fixture: dict,
    run_synthesis: Callable[[dict], Optional[dict]],
) -> Dict[str, Any]:
    """Run every perturbation through ``run_synthesis`` and score it.

    ``run_synthesis(perturbed_fixture)`` should return an SOP dict
    (same shape as the fixture's ``expected_sop``) or ``None`` if the
    pipeline raised. We never let a perturbation crash the whole run —
    a None / exception is recorded as a failure mode.

    Output::

        {
          "perturbations": [
            {
              "name": "empty_transcript",
              "ok": bool,
              "n_steps": int,
              "frame_anchor": {...},
              "source_grounding": {...},
              "schema_coverage": {...},
              "error": Optional[str],
            },
            ...
          ],
          "n_robust": int,
          "n_total": int,
        }

    A perturbation is considered "robust" if the pipeline returns a
    non-empty SOP with ``schema_coverage.required >= 0.8`` — i.e.
    not crashed, not collapsed to a single placeholder step.
    """
    results: List[Dict[str, Any]] = []
    for name, mutator in PERTURBATIONS:
        perturbed = mutator(fixture)
        entry: Dict[str, Any] = {"name": name}
        try:
            sop = run_synthesis(perturbed)
        except Exception as exc:  # noqa: BLE001
            entry.update(
                ok=False,
                error=type(exc).__name__ + ": " + str(exc)[:200],
                n_steps=0,
            )
            results.append(entry)
            continue

        if not sop or not sop.get("steps"):
            entry.update(ok=False, error="empty SOP", n_steps=0)
            results.append(entry)
            continue

        steps = sop.get("steps", [])
        n_frames = len(perturbed.get("frame_observations", []) or [1])
        coverage = schema_coverage_metrics(steps)
        entry.update(
            ok=bool(coverage["required"] >= 0.8),
            n_steps=len(steps),
            frame_anchor=frame_anchor_metrics(steps, n_frames),
            source_grounding=source_grounding_metrics(
                steps,
                perturbed.get("transcript", ""),
                perturbed.get("frame_observations", []),
            ),
            schema_coverage=coverage,
        )
        results.append(entry)

    return {
        "perturbations": results,
        "n_robust": sum(1 for r in results if r.get("ok")),
        "n_total": len(results),
    }
