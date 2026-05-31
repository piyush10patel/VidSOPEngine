"""LlamaIndex BM25 retriever over the failure dataset.

Pulls the most similar past *corrected* SOPs to use as few-shot examples.
BM25 (lexical) — no embeddings, no extra API key, fast for <1000 docs.

The index is rebuilt lazily on each call so newly recorded failures
become available without a restart.

Per-user personalisation:
  When a user_id is provided, that user's pinned_examples_json (curated
  domain-specific corrected pairs) is merged AHEAD of global BM25 results.
  This lets per-customer style and vocabulary outweigh generic patterns
  while still benefiting from the broader failure dataset.

Operational verb boost:
  BM25 weighs every token equally. For VidSOPEngine, we want operational
  verbs (pour, fill, open, close, place, …) to weigh more so two videos
  with the same procedure but different incidental wording match. The
  retrieve query is augmented by repeating any operational verbs found
  in the input transcript — a cheap deterministic boost.
"""
import json
import logging
import re
from typing import List, Optional

from app.datasets.failures import load_all

logger = logging.getLogger(__name__)


# Operational vocabulary — used to boost BM25 matches on procedural intent
# rather than incidental scene wording. Aligned with the verb-first action
# vocabulary used by enforce_atomic_action_vocabulary.
_OPERATIONAL_VERBS = (
    "pick", "pickup", "place", "put", "lift", "lower", "rotate", "remove",
    "press", "tap", "hold", "grip", "open", "close", "pour", "fill",
    "empty", "turn", "switch", "wipe", "fold", "unfold", "insert",
    "extract", "push", "pull", "slide", "twist", "unscrew", "screw",
    "stack", "align", "scan", "attach", "detach", "load", "unload",
    "carry", "deliver", "collect", "verify", "inspect", "check",
    "measure", "weigh", "label", "wrap", "seal", "clean",
)
_VERB_RE = re.compile(
    r"\b(" + "|".join(_OPERATIONAL_VERBS) + r")\b", re.IGNORECASE
)


def _boost_operational_query(transcript: str) -> str:
    """Repeat operational verbs found in the transcript so BM25 weights them.

    BM25's term-frequency component means a doubled token roughly doubles
    its contribution to the match score. We don't add new vocabulary —
    only amplify what's already there — so the retriever stays honest.
    """
    if not transcript:
        return transcript
    found = {m.group(1).lower() for m in _VERB_RE.finditer(transcript)}
    if not found:
        return transcript
    return transcript + "\n\n" + " ".join(found)


def _build_documents() -> list:
    """Convert each FailureCase into a LlamaIndex Document."""
    try:
        from llama_index.core import Document
    except ImportError:
        logger.warning("llama-index not installed — few-shot retrieval disabled")
        return []

    cases = load_all()
    docs = []
    for case in cases:
        # Index by transcript so similar procedures match
        text = case.input.transcript
        docs.append(Document(
            text=text,
            metadata={
                "case_id": case.id,
                "expected_output": json.dumps(case.expected_output),
                "failure_type": case.failure_type.value,
            },
        ))
    return docs


def _user_pinned_examples(user_id: Optional[str]) -> List[dict]:
    """Load this user's curated example list, BM25-ranked against transcript.

    Returns the raw list (no ranking) — the caller does similarity ranking.
    Falls back to [] on any failure (missing user, missing column, etc.).
    """
    if not user_id:
        return []
    try:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from app.core.config import settings
        from app.models.user import User

        sync_url = settings.database_url
        if "sqlite+aiosqlite" in sync_url:
            sync_url = sync_url.replace("sqlite+aiosqlite", "sqlite")
        if "postgresql+asyncpg" in sync_url:
            sync_url = sync_url.replace("postgresql+asyncpg", "postgresql")
        engine = create_engine(sync_url, echo=False)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
            if not user or not user.pinned_examples_json:
                return []
            return [
                {
                    "transcript": ex.get("transcript", ""),
                    "expected_output": ex.get("expected_output", {}),
                    "step_corrections": ex.get("step_corrections", []),
                    "case_id": f"user-pin-{user_id[:8]}-{i}",
                }
                for i, ex in enumerate(user.pinned_examples_json)
                if isinstance(ex, dict) and ex.get("expected_output")
            ]
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"per-user pinned examples lookup failed: {e}")
        return []


def retrieve_similar_examples(
    transcript: str,
    top_k: int = 2,
    user_id: Optional[str] = None,
) -> List[dict]:
    """Return the top-k most similar corrected SOPs as few-shot examples.

    When user_id is set, the user's pinned_examples_json is merged AHEAD
    of the global BM25 results — per-customer corrections beat generic
    failure-dataset matches.

    Each item: {"transcript": str, "expected_output": dict, "case_id": str}
    Returns [] if no examples are available.
    """
    user_pins = _user_pinned_examples(user_id)
    # If we already have enough user pins, skip the global lookup entirely
    # (saves the BM25 rebuild on hot paths).
    if len(user_pins) >= top_k:
        return user_pins[:top_k]

    docs = _build_documents()
    global_results: List[dict] = []
    if docs:
        try:
            from llama_index.retrievers.bm25 import BM25Retriever
            retriever = BM25Retriever.from_defaults(
                nodes=[d.to_node() if hasattr(d, "to_node") else d for d in docs],
                similarity_top_k=top_k,
            )
            # Boost operational verbs in the query so procedural intent
            # weighs more than incidental scene wording.
            results = retriever.retrieve(_boost_operational_query(transcript))
            for node in results:
                meta = getattr(node, "metadata", None) or getattr(node.node, "metadata", {})
                try:
                    expected = json.loads(meta.get("expected_output", "{}"))
                except json.JSONDecodeError:
                    continue
                global_results.append({
                    "transcript": node.get_content() if hasattr(node, "get_content") else node.text,
                    "expected_output": expected,
                    "case_id": meta.get("case_id", ""),
                })
        except ImportError:
            logger.warning("llama-index-retrievers-bm25 not installed — global RAG disabled")
        except Exception as e:
            logger.warning(f"BM25 retrieval failed: {e}")

    # Merge: user pins first, then global, deduped by case_id, capped at top_k.
    merged: List[dict] = []
    seen = set()
    for ex in user_pins + global_results:
        cid = ex.get("case_id", "")
        if cid in seen:
            continue
        seen.add(cid)
        merged.append(ex)
        if len(merged) >= top_k:
            break
    return merged


def format_examples_for_prompt(examples: List[dict]) -> str:
    """Format retrieved examples as a few-shot block for the prompt."""
    if not examples:
        return ""
    blocks = []
    for i, ex in enumerate(examples, 1):
        compact_sop = {
            "title": ex["expected_output"].get("title", ""),
            "steps": [
                {"title": s.get("title", ""), "description": s.get("description", "")}
                for s in ex["expected_output"].get("steps", [])
            ],
        }
        correction_lines = []
        for correction in ex.get("step_corrections", [])[:5]:
            after = correction.get("after", {}) if isinstance(correction, dict) else {}
            note = correction.get("note", "") if isinstance(correction, dict) else ""
            correction_lines.append(
                f"- Step {after.get('step_number', '?')}: {note or after.get('description', '')}"
            )
        corrections = "\nSTEP CORRECTIONS:\n" + "\n".join(correction_lines) if correction_lines else ""
        blocks.append(
            f"--- EXAMPLE {i} ---\n"
            f"TRANSCRIPT: {ex['transcript'][:500]}\n"
            f"GOOD SOP: {json.dumps(compact_sop, indent=2)[:1000]}{corrections}\n"
        )
    return "REFERENCE EXAMPLES (similar past procedures, written correctly):\n\n" + "\n".join(blocks)
