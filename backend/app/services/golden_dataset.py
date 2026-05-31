"""Build the golden_dataset.jsonl from failures.jsonl.

Three-stage transform — PII scrub → dedupe → LLM-as-judge — that lives
here (not under evals/) so it's importable from FastAPI routers without
sys.path manipulation. The ``evals/build_golden_dataset.py`` CLI is a
thin wrapper around ``build_golden()``.

INV-4 preserved: ``failures.jsonl`` is read-only here; we always write
to a separate ``golden_dataset.jsonl`` file. INV-12 preserved: this is
an offline pre-training step, never invoked at request-handling time
inside the SOP generation path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app.core.config import settings
from app.datasets.failures import load_all
from app.schemas.failure import FailureCase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. PII scrub — regex layer. Designed for Indian SMB use cases.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"""(?x)
    (?:\+?91[\-\s]?)?      # optional country code
    [6-9]\d{9}             # Indian mobile (starts with 6-9)
    |
    \+\d{10,14}            # generic international
    """
)
_AADHAAR_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

_PII_PATTERNS = [
    (_EMAIL_RE, "[email]"),
    (_PHONE_RE, "[phone]"),
    (_AADHAAR_RE, "[aadhaar]"),
    (_GSTIN_RE, "[gstin]"),
    (_PAN_RE, "[pan]"),
]


def scrub_pii_text(value: str) -> str:
    if not value:
        return value
    out = value
    for pattern, placeholder in _PII_PATTERNS:
        out = pattern.sub(placeholder, out)
    return out


def scrub_pii(obj):
    """Walk a JSON-like object and scrub PII from every string leaf.

    Keys aren't scrubbed (they're schema identifiers, not user content).
    Non-string scalars (ints, floats, None) untouched.
    """
    if isinstance(obj, str):
        return scrub_pii_text(obj)
    if isinstance(obj, list):
        return [scrub_pii(item) for item in obj]
    if isinstance(obj, dict):
        return {k: scrub_pii(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# 2. Dedup — content hash on the input + expected output.
# ---------------------------------------------------------------------------

def _content_hash(case: FailureCase) -> str:
    """Stable hash over the bits we'd want to dedupe on.

    Two cases that differ only in id / created_at / notes get the same
    hash — those fields don't change training signal.
    """
    payload = {
        "transcript": case.input.transcript or "",
        "frame_observations": case.input.frame_observations or [],
        "actual_output": case.actual_output,
        "expected_output": case.expected_output,
        "video_type": case.video_type or "physical",
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def dedupe(cases: Iterable[FailureCase]) -> list[FailureCase]:
    seen: dict[str, FailureCase] = {}
    duplicates = 0
    for case in cases:
        h = _content_hash(case)
        if h in seen:
            duplicates += 1
            continue
        seen[h] = case
    if duplicates:
        logger.info("[dedupe] dropped %d duplicate cases", duplicates)
    return list(seen.values())


# ---------------------------------------------------------------------------
# 3. LLM-as-judge — score 0..5 on whether expected_output is high quality.
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """You are a strict reviewer scoring SOP-training examples.

For each example, judge ONLY whether the "expected_output" is high quality
enough to teach a smaller model from. We do NOT care whether the actual_output
matches — we care about the expected_output's own quality.

Score 0..5:
  5  perfect: steps are clear, ordered, action-led; tools and checks make sense
  4  minor issues only
  3  good enough to train on
  2  noticeable problems (missing steps, vague verbs, wrong order)
  1  many issues; do not train on this
  0  unusable

Reply ONLY with a single integer 0..5. No words.

INPUT TRANSCRIPT:
{transcript}

EXPECTED OUTPUT (the candidate training label):
{expected}

Your score (0..5):"""


def _truncate(value: str, limit: int = 2000) -> str:
    if value is None:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + " …[truncated]"


def _judge_one(case: FailureCase) -> int:
    """Single Groq call. Returns 0 on any failure so a flaky judge never
    blocks the whole pipeline. The caller's min-score threshold then
    drops zero-scored cases out of the keep set."""
    try:
        import requests
        if not settings.groq_api_key:
            return 0
        transcript = _truncate(case.input.transcript or "", 1500)
        expected = _truncate(json.dumps(case.expected_output, indent=2), 2500)
        prompt = _JUDGE_PROMPT.format(transcript=transcript, expected=expected)
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model or "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 4,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("[judge] non-200 status=%s body=%s", resp.status_code, resp.text[:200])
            return 0
        text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        for ch in text:
            if ch.isdigit():
                return max(0, min(5, int(ch)))
        return 0
    except Exception as exc:
        logger.warning("[judge] failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def golden_path() -> Path:
    """Where the built golden dataset lives. Sibling of failures.jsonl."""
    base = Path(settings.upload_dir).parent
    return base / "golden_dataset.jsonl"


def _serialise_case(case: FailureCase, score: int) -> dict:
    """Dict shape ready to JSONL-dump. Extra ``golden_score`` /
    ``golden_built_at`` fields are ignored by FailureCase deserialisers."""
    payload = json.loads(case.model_dump_json())
    payload["golden_score"] = score
    payload["golden_built_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def golden_stats() -> dict:
    """Quick read-only stats for the existing golden_dataset.jsonl.

    Used by the superadmin UI to show "last built X, N cases" without
    triggering a rebuild.
    """
    path = golden_path()
    if not path.exists():
        return {"exists": False}
    count = 0
    last_built: Optional[str] = None
    score_hist: Counter[int] = Counter()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                count += 1
                score = int(row.get("golden_score", 0))
                score_hist[score] += 1
                built = row.get("golden_built_at")
                if built and (last_built is None or built > last_built):
                    last_built = built
            except Exception:
                continue
    return {
        "exists": True,
        "count": count,
        "last_built": last_built,
        "scores": dict(score_hist),
        "path": str(path),
    }


def build_golden(
    *,
    skip_judge: bool = False,
    min_score: int = 3,
) -> dict:
    """Run the full PII → dedup → judge → write pipeline.

    Returns a stats dict that the superadmin UI surfaces immediately.
    """
    cases = load_all()
    total_in = len(cases)
    if total_in == 0:
        logger.info("[golden] failures.jsonl is empty — nothing to do.")
        return {"total_in": 0, "kept": 0, "scores": {}, "out_path": str(golden_path())}

    # 1. PII scrub — mutate in place (rows live only for this run)
    scrubbed: list[FailureCase] = []
    for case in cases:
        case.input.transcript = scrub_pii_text(case.input.transcript or "")
        case.input.frame_observations = scrub_pii(case.input.frame_observations or [])
        case.actual_output = scrub_pii(case.actual_output)
        case.expected_output = scrub_pii(case.expected_output)
        if case.notes:
            case.notes = scrub_pii_text(case.notes)
        scrubbed.append(case)

    # 2. Dedup
    deduped = dedupe(scrubbed)

    # 3. Judge
    keep_cases: list[tuple[FailureCase, int]] = []
    score_hist: Counter[int] = Counter()
    for case in deduped:
        if skip_judge:
            score = 0
            keep_cases.append((case, score))
        else:
            score = _judge_one(case)
            score_hist[score] += 1
            if score >= min_score:
                keep_cases.append((case, score))

    out_path = golden_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for case, score in keep_cases:
            fh.write(json.dumps(_serialise_case(case, score), ensure_ascii=False) + "\n")

    return {
        "total_in": total_in,
        "after_dedupe": len(deduped),
        "kept": len(keep_cases),
        "scores": dict(score_hist),
        "out_path": str(out_path),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
