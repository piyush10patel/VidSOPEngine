"""Deterministic SOP-quality metrics — zero LLM cost, run instantly.

These are the first numbers to look at when something feels off. They cover:

- **Step alignment** — precision / recall / F1 against an expected SOP,
  using the existing token-overlap aligner from ``evals.step_alignment``.
- **Frame anchoring** — does every step have ``source_frame_num``, are
  values strictly increasing, are they distinct, do they spread across
  the available frame range or bunch at the ends?
- **Source grounding** — fraction of tokens in each step that appear in
  the transcript or frame observations. Cheap proxy for hallucination.
- **Schema coverage** — fraction of required SOPStep fields that are
  populated (title, description/instruction, evidence, etc.).
- **Step-image alignment** — after running the assignment helper, what
  is the Jaccard overlap between each step's text and its assigned
  frame's vision description?

Every metric is a float in [0, 1] (higher is better) or a small integer
count. None of them need an LLM, so they're the first signal to read.
"""
from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional, Sequence, Set

# Import the existing aligner so we don't duplicate logic.
from evals.step_alignment import align_steps, temporal_order_score


_WORD_RE = re.compile(r"[A-Za-zऀ-ॿ][A-Za-z0-9ऀ-ॿ]+")
_STOPWORDS = frozenset(
    "a an and any are as at be by for from in into is it its of on or the to "
    "with show shown showing visible see seen observe observed step number "
    "this that these those will would can could may might".split()
)


_SUFFIXES = ("ings", "ing", "edly", "ed", "est", "ers", "er", "ies", "es", "ly", "s")


def _stem(token: str) -> str:
    """Lightweight suffix-stripper so "reach" and "reaches" collapse.

    Catches the most common English inflections (-s, -es, -ed, -ing, -ly,
    -er) that otherwise punish the step->image alignment score even when
    the step and frame description clearly refer to the same action:

      "reaches" -> "reach"        (frame "reaches for" vs step "reach")
      "unscrewing" -> "unscrew"   (frame "unscrewing the cap" vs step "unscrew")
      "holds" -> "hold"           (frame "holds the bottle" vs step "hold")
      "bottles" -> "bottle"

    Only strips when the remaining stem is at least 4 characters, so
    "miss" -> "mis" / "less" -> "les" don't happen.
    """
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _tokenize(text: Any, stem: bool = True) -> Set[str]:
    """Tokenize text into content words. Stems by default so vocabulary
    drift between step text and frame descriptions doesn't tank the
    overlap score — that drift is the metric's biggest source of noise."""
    if text is None:
        return set()
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    tokens = {tok.lower() for tok in _WORD_RE.findall(str(text))}
    tokens = {t for t in tokens if len(t) > 2 and t not in _STOPWORDS}
    if stem:
        tokens = {_stem(t) for t in tokens}
    return tokens


def _step_text(step: dict) -> str:
    """Flatten a step into a single string for tokenisation."""
    parts = [
        step.get("title", ""),
        step.get("description", "") or step.get("instruction", ""),
    ]
    parts.extend(step.get("tools", []) or step.get("objects", []) or [])
    parts.extend(step.get("checks", []) or [])
    return " ".join(str(p) for p in parts if p)


# ─────────────────────────────────────────── step alignment


def step_alignment_metrics(
    predicted_steps: Sequence[dict],
    expected_steps: Sequence[dict],
) -> Dict[str, float]:
    """Precision / recall / F1 / temporal-order score against the expected SOP."""
    alignment = align_steps(list(predicted_steps), list(expected_steps))
    summary = alignment.summary()
    summary["temporal_order"] = round(temporal_order_score(alignment), 4)
    return summary


# ─────────────────────────────────────────── frame anchoring


def frame_anchor_metrics(
    steps: Sequence[dict],
    n_frames: int,
) -> Dict[str, Any]:
    """How well-anchored are the step images?

    Returns:
      - coverage:      fraction of steps with a non-null source_frame_num
      - unique_ratio:  fraction of populated values that are unique
      - strictly_increasing: 1.0 if every step's value > previous, else lower
      - spread_score:  std-dev of populated values / ideal proportional std-dev
                       (1.0 = perfectly distributed, <1.0 = clustered)
      - first_frame:   index of the first step (debug)
      - last_frame:    index of the last step
    """
    values = [s.get("source_frame_num") for s in steps]
    populated = [v for v in values if isinstance(v, int)]
    n_steps = len(steps)

    coverage = len(populated) / n_steps if n_steps else 0.0
    unique_ratio = (
        len(set(populated)) / len(populated) if populated else 0.0
    )

    # strictly_increasing: walk pairs of consecutive populated values
    if len(populated) >= 2:
        ascents = sum(
            1
            for a, b in zip(populated, populated[1:])
            if b > a
        )
        strictly_increasing = ascents / (len(populated) - 1)
    else:
        strictly_increasing = 1.0

    # Spread vs the ideal proportional distribution.
    if len(populated) >= 2 and n_frames > 1:
        ideal = [
            1 + round(i * (n_frames - 1) / (n_steps - 1))
            for i in range(n_steps)
        ]
        actual_std = statistics.pstdev(populated)
        ideal_std = statistics.pstdev(ideal) or 1.0
        spread = min(actual_std / ideal_std, 1.0)
    else:
        spread = 1.0

    return {
        "coverage": round(coverage, 4),
        "unique_ratio": round(unique_ratio, 4),
        "strictly_increasing": round(strictly_increasing, 4),
        "spread_score": round(spread, 4),
        "n_unique": len(set(populated)),
        "n_populated": len(populated),
        "n_steps": n_steps,
        "n_frames": n_frames,
        "first_frame": populated[0] if populated else None,
        "last_frame": populated[-1] if populated else None,
    }


# ─────────────────────────────────────────── source grounding


def source_grounding_metrics(
    steps: Sequence[dict],
    transcript: str,
    frame_observations: Sequence[dict],
) -> Dict[str, float]:
    """Fraction of each step's content words that appear in the source.

    Catches hallucination cheaply: a step talking about a tool that never
    appears in the transcript or any frame description is suspect.
    """
    transcript_tokens = _tokenize(transcript)
    obs_tokens: Set[str] = set()
    for obs in frame_observations:
        obs_tokens |= _tokenize(obs.get("description", ""))
    source_tokens = transcript_tokens | obs_tokens

    if not source_tokens:
        return {
            "mean_grounding": 0.0,
            "min_grounding": 0.0,
            "n_steps_below_50pct": len(steps),
        }

    per_step = []
    for step in steps:
        step_tokens = _tokenize(_step_text(step))
        if not step_tokens:
            per_step.append(1.0)  # empty step is vacuously grounded
            continue
        overlap = len(step_tokens & source_tokens) / len(step_tokens)
        per_step.append(overlap)

    return {
        "mean_grounding": round(sum(per_step) / len(per_step), 4) if per_step else 0.0,
        "min_grounding": round(min(per_step), 4) if per_step else 0.0,
        "n_steps_below_50pct": sum(1 for v in per_step if v < 0.5),
        "per_step": [round(v, 4) for v in per_step],
    }


# ─────────────────────────────────────────── schema coverage


_REQUIRED_FIELDS = ("title", "description", "instruction")
_INFORMATIVE_FIELDS = ("evidence", "source_frame_num", "image_url")


def schema_coverage_metrics(steps: Sequence[dict]) -> Dict[str, float]:
    """Fraction of steps with each required / informative field populated."""
    if not steps:
        return {"required": 0.0, "informative": 0.0}

    def _has(step: dict, key: str) -> bool:
        v = step.get(key)
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip() != ""
        if isinstance(v, (list, tuple)):
            return len(v) > 0
        return True

    n = len(steps)
    required_score = sum(
        1
        for s in steps
        if any(_has(s, k) for k in _REQUIRED_FIELDS)
    ) / n
    informative_per_field = {
        k: sum(1 for s in steps if _has(s, k)) / n
        for k in _INFORMATIVE_FIELDS
    }
    informative_score = sum(informative_per_field.values()) / len(_INFORMATIVE_FIELDS)

    return {
        "required": round(required_score, 4),
        "informative": round(informative_score, 4),
        "per_field": {k: round(v, 4) for k, v in informative_per_field.items()},
    }


# ─────────────────────────────────────────── step ↔ image alignment


def step_image_alignment_metrics(
    steps: Sequence[dict],
    frame_observations: Sequence[dict],
) -> Dict[str, float]:
    """After assignment, how well does each step's image match the step text?

    Scores each step as **step coverage**: what fraction of the step's
    stemmed content words appear in the assigned frame's vision
    description? This is asymmetric on purpose — the frame description
    usually contains extra detail ("operator", "right hand", "the bench")
    that the step doesn't need to repeat. Jaccard would punish those
    extras as "no match"; recall correctly treats the frame as a
    superset that contains the step's action.

    Light stemming via :func:`_stem` collapses surface variants like
    ``reach``/``reaches``/``reaching`` to one token so vocabulary drift
    between step text ("Pick up the bottle") and frame text ("Right hand
    reaches for the bottle") doesn't tank the score.

    This is the most direct measure of the "pictures don't match the
    steps" complaint — persistently low scores here mean the assignment
    is grabbing visually-irrelevant frames.
    """
    obs_by_url = {obs.get("image_url"): obs for obs in frame_observations}

    per_step = []
    misses = 0
    for step in steps:
        url = step.get("image_url")
        if not url or url not in obs_by_url:
            per_step.append(0.0)
            misses += 1
            continue
        obs = obs_by_url[url]
        step_tokens = _tokenize(_step_text(step))
        frame_tokens = _tokenize(obs.get("description", ""))
        if not step_tokens:
            # Step text was empty — nothing to align against; treat as
            # vacuously aligned so we don't punish blank fixtures.
            per_step.append(1.0)
            continue
        if not frame_tokens:
            per_step.append(0.0)
            continue
        # Step coverage: how many of the step's content words appear
        # in the frame description?
        covered = len(step_tokens & frame_tokens)
        per_step.append(covered / len(step_tokens))

    return {
        "mean": round(sum(per_step) / len(per_step), 4) if per_step else 0.0,
        "min": round(min(per_step), 4) if per_step else 0.0,
        "n_below_10pct": sum(1 for v in per_step if v < 0.1),
        "n_unassigned": misses,
        "per_step": [round(v, 4) for v in per_step],
    }


# ─────────────────────────────────────────── public roll-up


def run_deterministic(
    fixture: dict,
    predicted_sop: dict,
) -> Dict[str, Any]:
    """Run every deterministic metric against one fixture's prediction."""
    transcript = fixture.get("transcript", "")
    frame_observations = fixture.get("frame_observations", [])
    expected_steps = fixture.get("expected_sop", {}).get("steps", [])
    predicted_steps = predicted_sop.get("steps", [])
    n_frames = len(frame_observations) or 1

    return {
        "step_alignment": step_alignment_metrics(predicted_steps, expected_steps)
        if expected_steps
        else None,
        "frame_anchor": frame_anchor_metrics(predicted_steps, n_frames),
        "source_grounding": source_grounding_metrics(
            predicted_steps, transcript, frame_observations,
        ),
        "schema_coverage": schema_coverage_metrics(predicted_steps),
        "step_image_alignment": step_image_alignment_metrics(
            predicted_steps, frame_observations,
        ),
    }
