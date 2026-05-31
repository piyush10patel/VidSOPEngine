"""Three scorers for SOP generation outputs.

Two tiers:
  - online (cheap, deterministic) → run on every prod inference
      hallucination_rate
  - offline (LLM-as-judge, costly) → run in Braintrust experiments only
      factual_correctness, relevance

All scorers return a dict {name: float in [0, 1]}. Higher is better.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Online scorers (programmatic — no LLM call)
# ---------------------------------------------------------------------------

def hallucination_rate(output: dict, transcript: str, frame_observations: list) -> float:
    """Fraction of step tools that DO appear in transcript or frame observations.

    Returns 1.0 = no hallucination, 0.0 = everything hallucinated.
    """
    steps = output.get("steps") or output.get("sop") or []
    all_tools = []
    for s in steps:
        tools = s.get("tools") or s.get("objects") or []
        all_tools.extend(str(t).lower().strip() for t in tools)

    if not all_tools:
        return 1.0  # vacuously grounded

    corpus = transcript.lower() + " "
    for obs in frame_observations:
        corpus += str(obs.get("description", "")).lower() + " "

    grounded = 0
    for tool in all_tools:
        # Token-level: any 3+ char token in tool name appearing in corpus = grounded
        tokens = [t for t in tool.split() if len(t) > 2]
        if tokens and any(tok in corpus for tok in tokens):
            grounded += 1

    return grounded / len(all_tools)


def confidence_alignment(output: dict) -> float:
    """How well overall_confidence reflects the average of per-step confidences.

    A model that claims 0.95 overall but has steps averaging 0.5 is poorly calibrated.
    """
    steps = output.get("steps") or output.get("sop") or []
    if not steps:
        return 0.0
    per_step = [float(s.get("confidence", 1.0)) for s in steps]
    avg = sum(per_step) / len(per_step)
    overall = float(output.get("overall_confidence", 1.0))
    # Score = 1 - |overall - avg|; closer = better
    return max(0.0, 1.0 - abs(overall - avg))


def online_scores(output: dict, transcript: str, frame_observations: list) -> dict:
    """All cheap scorers — safe to run on every production inference."""
    return {
        "hallucination_rate": hallucination_rate(output, transcript, frame_observations),
        "confidence_alignment": confidence_alignment(output),
    }


# ---------------------------------------------------------------------------
# Offline scorers (LLM-as-judge — for Braintrust experiments)
# ---------------------------------------------------------------------------

_FACTUALITY_PROMPT = """You are evaluating an AI-generated Standard Operating Procedure against a labelled correct version.

PREDICTED SOP:
{predicted}

EXPECTED (CORRECT) SOP:
{expected}

ORIGINAL TRANSCRIPT:
{transcript}

Score factual correctness from 0.0 to 1.0:
- 1.0: Every step in the predicted SOP is supported by the transcript and matches the expected actions
- 0.5: Roughly half of predicted steps are correct/grounded
- 0.0: Predicted SOP contradicts the transcript or expected version

Reply with JSON only: {{"score": <float>, "reason": "<one sentence>"}}"""


_RELEVANCE_PROMPT = """You are evaluating whether an AI-generated SOP describes the procedure shown in the source video.

PREDICTED SOP:
{predicted}

ORIGINAL TRANSCRIPT:
{transcript}

Score relevance from 0.0 to 1.0:
- 1.0: Every step is about the actual procedure; no off-topic or generic filler
- 0.5: About half the steps are on-topic
- 0.0: SOP is generic boilerplate or about a different procedure

Reply with JSON only: {{"score": <float>, "reason": "<one sentence>"}}"""


def _llm_judge(prompt: str, model: str = "llama-3.3-70b-versatile") -> tuple[float, str]:
    """LLM-as-judge with a JSON-mode prompt. Returns (score, reason)."""
    try:
        from app.services.llm import get_provider
        resp = get_provider().chat(
            prompt, model=model,
            response_format={"type": "json_object"}, timeout=30,
        )
        result = json.loads(resp.text)
        return float(result.get("score", 0.0)), str(result.get("reason", ""))
    except Exception as e:
        logger.warning(f"LLM judge failed: {e}")
        return 0.0, f"judge error: {e}"


def factual_correctness(predicted: dict, expected: dict, transcript: str) -> dict:
    """LLM-as-judge: does the predicted SOP factually match the expected one?"""
    prompt = _FACTUALITY_PROMPT.format(
        predicted=json.dumps(predicted, indent=2)[:4000],
        expected=json.dumps(expected, indent=2)[:4000],
        transcript=transcript[:2000],
    )
    score, reason = _llm_judge(prompt)
    return {"score": score, "reason": reason}


def relevance(predicted: dict, transcript: str) -> dict:
    """LLM-as-judge: are the steps about the actual procedure (not generic)?"""
    prompt = _RELEVANCE_PROMPT.format(
        predicted=json.dumps(predicted, indent=2)[:4000],
        transcript=transcript[:2000],
    )
    score, reason = _llm_judge(prompt)
    return {"score": score, "reason": reason}


def offline_scores(predicted: dict, expected: Optional[dict], transcript: str) -> dict:
    """All scorers including LLM-as-judge — for use in offline experiments."""
    scores = online_scores(predicted, transcript, [])
    if expected is not None:
        scores["factual_correctness"] = factual_correctness(predicted, expected, transcript)["score"]
    scores["relevance"] = relevance(predicted, transcript)["score"]
    return scores


# ---------------------------------------------------------------------------
# Step-level scorers (granular A/B testing)
# ---------------------------------------------------------------------------

import re as _re


def _tokenize_set(text: str) -> set:
    """Lowercase token set (3+ char tokens only)."""
    return {t for t in _re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokenize_set(a), _tokenize_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def step_action_match(predicted_step: dict, expected_step: dict) -> float:
    """How well a predicted step's action matches the expected action.

    Weighted blend of title similarity (40%) and description similarity (60%).
    """
    title_sim = _jaccard(
        predicted_step.get("title", ""),
        expected_step.get("title", ""),
    )
    desc_sim = _jaccard(
        predicted_step.get("description", predicted_step.get("instruction", "")),
        expected_step.get("description", expected_step.get("instruction", "")),
    )
    return 0.4 * title_sim + 0.6 * desc_sim


def step_tool_grounding(
    step: dict, transcript: str, frame_observations: list
) -> float:
    """Fraction of a single step's tools that are grounded in source material."""
    tools = step.get("tools") or step.get("objects") or []
    if not tools:
        return 1.0  # vacuously grounded

    corpus = transcript.lower() + " "
    for obs in frame_observations:
        corpus += str(obs.get("description", "")).lower() + " "

    grounded = 0
    for tool in tools:
        tokens = [t for t in str(tool).lower().split() if len(t) > 2]
        if tokens and any(tok in corpus for tok in tokens):
            grounded += 1

    return grounded / len(tools)


def _align_steps_greedy(
    predicted_steps: list, expected_steps: list, *, min_sim: float = 0.15
) -> list:
    """Greedy bipartite step alignment (Python-side mirror of the JS logic).

    Returns list of dicts: {pred_idx, exp_idx, similarity}.
    """
    n, m = len(predicted_steps), len(expected_steps)
    candidates = []
    for i in range(n):
        for j in range(m):
            sim = step_action_match(predicted_steps[i], expected_steps[j])
            # Also factor in tool overlap
            pred_tools = " ".join(predicted_steps[i].get("tools", predicted_steps[i].get("objects", [])))
            exp_tools = " ".join(expected_steps[j].get("tools", expected_steps[j].get("objects", [])))
            tool_sim = _jaccard(pred_tools, exp_tools)
            composite = 0.8 * sim + 0.2 * tool_sim
            if composite >= min_sim:
                candidates.append((composite, i, j))

    candidates.sort(key=lambda x: x[0], reverse=True)
    used_pred: set = set()
    used_exp: set = set()
    matches = []

    for sim, i, j in candidates:
        if i in used_pred or j in used_exp:
            continue
        matches.append({"pred_idx": i, "exp_idx": j, "similarity": sim})
        used_pred.add(i)
        used_exp.add(j)

    return matches


def step_level_scores(
    predicted: dict,
    expected: dict,
    transcript: str,
    frame_observations: list,
) -> dict:
    """Granular per-step scoring for A/B testing.

    Returns::

        {
            "per_step": [
                {
                    "pred_idx": 0,
                    "exp_idx": 1,
                    "action_match": 0.82,
                    "tool_grounding": 1.0,
                    "similarity": 0.75,
                    "pred_title": "...",
                    "exp_title": "...",
                },
                ...
            ],
            "unmatched_predicted": [...],  # hallucinated steps
            "unmatched_expected": [...],   # missing steps
            "precision": 0.9,
            "recall": 0.85,
            "f1": 0.87,
            "avg_action_match": 0.78,
            "avg_tool_grounding": 0.95,
        }
    """
    pred_steps = predicted.get("steps") or predicted.get("sop") or []
    exp_steps = expected.get("steps") or []

    matches = _align_steps_greedy(pred_steps, exp_steps)
    matched_pred = {m["pred_idx"] for m in matches}
    matched_exp = {m["exp_idx"] for m in matches}

    per_step = []
    for m in matches:
        ps = pred_steps[m["pred_idx"]]
        es = exp_steps[m["exp_idx"]]
        per_step.append({
            "pred_idx": m["pred_idx"],
            "exp_idx": m["exp_idx"],
            "action_match": round(step_action_match(ps, es), 4),
            "tool_grounding": round(step_tool_grounding(ps, transcript, frame_observations), 4),
            "similarity": round(m["similarity"], 4),
            "pred_title": ps.get("title", ""),
            "exp_title": es.get("title", ""),
            "pred_confidence": float(ps.get("confidence", 1.0)),
        })

    unmatched_pred = [
        {"idx": i, "title": pred_steps[i].get("title", ""), "type": "hallucinated"}
        for i in range(len(pred_steps)) if i not in matched_pred
    ]
    unmatched_exp = [
        {"idx": j, "title": exp_steps[j].get("title", ""), "type": "missing"}
        for j in range(len(exp_steps)) if j not in matched_exp
    ]

    n_matched = len(matches)
    precision = n_matched / len(pred_steps) if pred_steps else 0.0
    recall = n_matched / len(exp_steps) if exp_steps else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    avg_action = (
        sum(s["action_match"] for s in per_step) / len(per_step)
        if per_step else 0.0
    )
    avg_tool = (
        sum(s["tool_grounding"] for s in per_step) / len(per_step)
        if per_step else 0.0
    )

    return {
        "per_step": per_step,
        "unmatched_predicted": unmatched_pred,
        "unmatched_expected": unmatched_exp,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "avg_action_match": round(avg_action, 4),
        "avg_tool_grounding": round(avg_tool, 4),
    }


# ---------------------------------------------------------------------------
# Step-level LLM-as-judge
# ---------------------------------------------------------------------------

_STEP_JUDGE_PROMPT = """You are evaluating ONE step of an AI-generated SOP.

PREDICTED STEP:
  Title: {pred_title}
  Description: {pred_desc}
  Tools: {pred_tools}

EXPECTED (CORRECT) STEP:
  Title: {exp_title}
  Description: {exp_desc}
  Tools: {exp_tools}

ORIGINAL TRANSCRIPT EXCERPT:
{transcript_excerpt}

Score this single step on three dimensions (each 0.0 to 1.0):
1. action_correctness: Does the predicted step describe the same physical action?
2. tool_accuracy: Are the tools/objects correct (not hallucinated)?
3. specificity: Is the step precise and actionable (not vague)?

Reply with JSON only: {{"action_correctness": <float>, "tool_accuracy": <float>, "specificity": <float>, "reason": "<one sentence>"}}"""


def step_level_factual_correctness(
    predicted_step: dict,
    expected_step: dict,
    transcript: str,
    *,
    model: str = "llama-3.3-70b-versatile",
) -> dict:
    """LLM-as-judge for a single step pair. Returns per-dimension scores."""
    prompt = _STEP_JUDGE_PROMPT.format(
        pred_title=predicted_step.get("title", ""),
        pred_desc=predicted_step.get("description", predicted_step.get("instruction", ""))[:300],
        pred_tools=", ".join(predicted_step.get("tools", predicted_step.get("objects", []))),
        exp_title=expected_step.get("title", ""),
        exp_desc=expected_step.get("description", expected_step.get("instruction", ""))[:300],
        exp_tools=", ".join(expected_step.get("tools", expected_step.get("objects", []))),
        transcript_excerpt=transcript[:1000],
    )
    try:
        from app.services.llm import get_provider
        resp = get_provider().chat(
            prompt, model=model,
            response_format={"type": "json_object"}, timeout=30,
        )
        result = json.loads(resp.text)
        return {
            "action_correctness": float(result.get("action_correctness", 0.0)),
            "tool_accuracy": float(result.get("tool_accuracy", 0.0)),
            "specificity": float(result.get("specificity", 0.0)),
            "reason": str(result.get("reason", "")),
        }
    except Exception as e:
        logger.warning(f"Step-level judge failed: {e}")
        return {
            "action_correctness": 0.0,
            "tool_accuracy": 0.0,
            "specificity": 0.0,
            "reason": f"judge error: {e}",
        }

