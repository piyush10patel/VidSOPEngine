"""Step-level alignment between predicted and expected SOPs.

Maps each predicted step to its best-matching expected step (or marks it
as an insertion / hallucination). Likewise marks expected steps that have
no matching prediction as deletions / missing steps.

The alignment drives granular step-level A/B scoring.

Usage::

    from evals.step_alignment import align_steps, StepAlignment

    alignment = align_steps(predicted_steps, expected_steps)
    for entry in alignment.entries:
        print(entry.predicted_idx, entry.expected_idx, entry.similarity)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


# ── Text similarity ──────────────────────────────────────────────────


# Token regex covers Latin (ASCII) AND Devanagari so Hindi step text
# isn't silently stripped to an empty token set. The old regex
# ``[a-z0-9]+`` returned {} for any Devanagari string, which produced
# Jaccard similarity = 0 for every cross-language alignment. With this
# regex a Hindi step like "गैस ऑन करें" tokenises to {"गैस", "ऑन", "करें"}
# and aligns correctly against a Hindi expected SOP.
_TOKEN_RE = re.compile(r"[a-z0-9ऀ-ॿ]+", re.IGNORECASE)


def _tokenize(text: str) -> set[str]:
    """Lowercase token set covering ASCII + Devanagari, 2+ chars."""
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2}


def token_overlap(a: str, b: str) -> float:
    """Jaccard-style token overlap ∈ [0, 1]."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def step_similarity(pred: dict, exp: dict) -> float:
    """Composite similarity between two SOP steps.

    Weighted blend:
      - frame anchor agreement (30%) — if both sides have source_frame_num,
        score 1.0 when they match exactly, 0.5 when within ±1, else 0
      - title similarity  (30%)
      - description/instruction similarity (30%)
      - tool overlap (10%)

    The frame-anchor component lets cross-language alignments still work
    when token overlap is unreliable (Hindi predicted vs Hindi expected
    is fine, but predicted Hindi vs expected English would otherwise
    fail purely on vocabulary mismatch).
    """
    title_sim = token_overlap(
        pred.get("title", ""),
        exp.get("title", ""),
    )
    desc_sim = token_overlap(
        pred.get("description", pred.get("instruction", "")),
        exp.get("description", exp.get("instruction", "")),
    )
    pred_tools = " ".join(pred.get("tools", pred.get("objects", [])))
    exp_tools = " ".join(exp.get("tools", exp.get("objects", [])))
    tool_sim = token_overlap(pred_tools, exp_tools)

    pred_frame = pred.get("source_frame_num")
    exp_frame = exp.get("source_frame_num")
    if isinstance(pred_frame, int) and isinstance(exp_frame, int):
        diff = abs(pred_frame - exp_frame)
        frame_sim = 1.0 if diff == 0 else (0.5 if diff == 1 else 0.0)
    else:
        frame_sim = None

    if frame_sim is not None:
        return 0.30 * frame_sim + 0.30 * title_sim + 0.30 * desc_sim + 0.10 * tool_sim
    # Fall back to the legacy blend when frame anchors are missing.
    return 0.4 * title_sim + 0.4 * desc_sim + 0.2 * tool_sim


# ── Alignment data structures ────────────────────────────────────────


@dataclass
class AlignmentEntry:
    """One row in the alignment table."""
    predicted_idx: Optional[int]   # None → missing from prediction (deletion)
    expected_idx: Optional[int]    # None → extra prediction (insertion/hallucination)
    similarity: float = 0.0
    predicted_step: Optional[dict] = None
    expected_step: Optional[dict] = None

    @property
    def is_match(self) -> bool:
        return self.predicted_idx is not None and self.expected_idx is not None

    @property
    def is_insertion(self) -> bool:
        """Predicted step with no matching expected step (hallucinated)."""
        return self.predicted_idx is not None and self.expected_idx is None

    @property
    def is_deletion(self) -> bool:
        """Expected step with no matching prediction (missing)."""
        return self.predicted_idx is None and self.expected_idx is not None


@dataclass
class StepAlignment:
    """Full alignment between predicted and expected step lists."""
    entries: List[AlignmentEntry] = field(default_factory=list)
    similarity_matrix: List[List[float]] = field(default_factory=list)

    @property
    def matched(self) -> List[AlignmentEntry]:
        return [e for e in self.entries if e.is_match]

    @property
    def insertions(self) -> List[AlignmentEntry]:
        return [e for e in self.entries if e.is_insertion]

    @property
    def deletions(self) -> List[AlignmentEntry]:
        return [e for e in self.entries if e.is_deletion]

    @property
    def precision(self) -> float:
        """Fraction of predicted steps that have a match."""
        total_predicted = sum(
            1 for e in self.entries if e.predicted_idx is not None
        )
        if total_predicted == 0:
            return 0.0
        return len(self.matched) / total_predicted

    @property
    def recall(self) -> float:
        """Fraction of expected steps that were found."""
        total_expected = sum(
            1 for e in self.entries if e.expected_idx is not None
        )
        if total_expected == 0:
            return 0.0
        return len(self.matched) / total_expected

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    @property
    def avg_match_similarity(self) -> float:
        matches = self.matched
        if not matches:
            return 0.0
        return sum(e.similarity for e in matches) / len(matches)

    def summary(self) -> dict:
        return {
            "total_predicted": sum(1 for e in self.entries if e.predicted_idx is not None),
            "total_expected": sum(1 for e in self.entries if e.expected_idx is not None),
            "matched": len(self.matched),
            "insertions": len(self.insertions),
            "deletions": len(self.deletions),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "avg_match_similarity": round(self.avg_match_similarity, 4),
        }


# ── Core alignment algorithm ─────────────────────────────────────────


def align_steps(
    predicted_steps: Sequence[dict],
    expected_steps: Sequence[dict],
    *,
    min_similarity: float = 0.15,
) -> StepAlignment:
    """Greedy bipartite alignment of predicted ↔ expected steps.

    Algorithm:
      1. Build an n×m similarity matrix.
      2. Greedily pick the highest-similarity (pred, exp) pair that
         exceeds ``min_similarity``. Mark both as consumed. Repeat.
      3. Unconsumed predicted steps → insertions (hallucinated).
      4. Unconsumed expected steps → deletions (missing).

    ``min_similarity`` prevents wildly-different steps from matching just
    because they share a common word like "step" or "press".
    """
    n = len(predicted_steps)
    m = len(expected_steps)

    # Build similarity matrix
    sim_matrix: list[list[float]] = []
    for i in range(n):
        row = []
        for j in range(m):
            row.append(step_similarity(predicted_steps[i], expected_steps[j]))
        sim_matrix.append(row)

    # Greedy matching
    used_pred: set[int] = set()
    used_exp: set[int] = set()
    entries: list[AlignmentEntry] = []

    # Collect all (sim, i, j) and sort descending
    candidates = []
    for i in range(n):
        for j in range(m):
            if sim_matrix[i][j] >= min_similarity:
                candidates.append((sim_matrix[i][j], i, j))
    candidates.sort(key=lambda x: x[0], reverse=True)

    for sim, i, j in candidates:
        if i in used_pred or j in used_exp:
            continue
        entries.append(AlignmentEntry(
            predicted_idx=i,
            expected_idx=j,
            similarity=sim,
            predicted_step=dict(predicted_steps[i]),
            expected_step=dict(expected_steps[j]),
        ))
        used_pred.add(i)
        used_exp.add(j)

    # Insertions (unmatched predicted steps)
    for i in range(n):
        if i not in used_pred:
            entries.append(AlignmentEntry(
                predicted_idx=i,
                expected_idx=None,
                similarity=0.0,
                predicted_step=dict(predicted_steps[i]),
                expected_step=None,
            ))

    # Deletions (unmatched expected steps)
    for j in range(m):
        if j not in used_exp:
            entries.append(AlignmentEntry(
                predicted_idx=None,
                expected_idx=j,
                similarity=0.0,
                predicted_step=None,
                expected_step=dict(expected_steps[j]),
            ))

    # Sort by the step index that is present (predicted or expected)
    entries.sort(key=lambda e: (
        e.predicted_idx if e.predicted_idx is not None
        else e.expected_idx if e.expected_idx is not None
        else 999
    ))

    return StepAlignment(entries=entries, similarity_matrix=sim_matrix)


# ── Longest Common Subsequence (for temporal order scoring) ───────────


def lcs_length(seq_a: Sequence, seq_b: Sequence) -> int:
    """Standard LCS length via dynamic programming."""
    n, m = len(seq_a), len(seq_b)
    if n == 0 or m == 0:
        return 0
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if seq_a[i - 1] == seq_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m]


def temporal_order_score(alignment: StepAlignment) -> float:
    """Fraction of matched steps that preserve temporal order.

    Uses LCS on the expected-step indices of matched pairs (ordered by
    predicted index). A score of 1.0 means every matched step appears
    in the same relative order as the expected SOP.
    """
    matches = sorted(alignment.matched, key=lambda e: e.predicted_idx)
    if len(matches) <= 1:
        return 1.0  # trivially ordered

    expected_indices = [e.expected_idx for e in matches]
    # LCS of expected_indices vs sorted(expected_indices) = order-preserving subset
    sorted_indices = sorted(expected_indices)
    lcs = lcs_length(expected_indices, sorted_indices)
    return lcs / len(expected_indices)
"""Step alignment utility for granular A/B testing of SOP synthesis."""
