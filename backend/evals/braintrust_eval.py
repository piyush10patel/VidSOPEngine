"""Run a Braintrust experiment over the failure dataset.

Generates an SOP for each case using the current DSPy pipeline, scores it
with all three metrics (factual_correctness, relevance, hallucination_rate),
pushes everything to Braintrust where it can be reviewed, sliced, and
compared against future runs.

Usage:
    cd backend
    export GROQ_API_KEY=...
    export BRAINTRUST_API_KEY=...
    python -m evals.braintrust_eval
"""
import json
import os
import sys
from pathlib import Path

# Add backend/ to path so `from app...` resolves
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import braintrust  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.datasets.failures import load_all  # noqa: E402
from app.observability.scorers import (  # noqa: E402
    factual_correctness,
    hallucination_rate,
    relevance,
)


def generate_sop_for_case(transcript: str, frame_observations: list) -> dict:
    """Run the same DSPy pipeline used in production."""
    import dspy
    from app.dspy_modules.pipeline import FullSOPPipeline, SOPGenerationPipeline

    lm = dspy.LM(
        f"groq/{settings.llm_model}",
        api_key=settings.groq_api_key,
        temperature=0,
    )
    dspy.configure(lm=lm)

    if frame_observations:
        pipeline = FullSOPPipeline(event_strategy="predict", sop_strategy="predict")
        return pipeline(transcript=transcript, frame_observations=frame_observations)

    pipeline = SOPGenerationPipeline(strategy="predict")
    return pipeline(transcript=transcript, events=None)


def task_fn(input_data: dict) -> dict:
    """Braintrust calls this once per dataset row."""
    return generate_sop_for_case(
        transcript=input_data["transcript"],
        frame_observations=input_data.get("frame_observations", []),
    )


def factuality_scorer(input, output, expected):
    score = factual_correctness(predicted=output, expected=expected, transcript=input["transcript"])
    return {"name": "factual_correctness", "score": score["score"], "metadata": {"reason": score["reason"]}}


def relevance_scorer(input, output, expected):
    score = relevance(predicted=output, transcript=input["transcript"])
    return {"name": "relevance", "score": score["score"], "metadata": {"reason": score["reason"]}}


def hallucination_scorer(input, output, expected):
    score = hallucination_rate(
        output=output,
        transcript=input["transcript"],
        frame_observations=input.get("frame_observations", []),
    )
    return {"name": "hallucination_rate", "score": score}


def main():
    if not settings.braintrust_api_key:
        print("ERROR: BRAINTRUST_API_KEY not set")
        sys.exit(1)
    if not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY not set")
        sys.exit(1)

    cases = load_all()
    if not cases:
        print("WARNING: failure dataset is empty — record cases via POST /failures first")
        sys.exit(1)

    dataset = [
        {
            "input": {
                "transcript": c.input.transcript,
                "frame_observations": c.input.frame_observations,
            },
            "expected": c.expected_output,
            "metadata": {
                "case_id": c.id,
                "video_id": c.video_id,
                "failure_type": c.failure_type.value,
                "severity": c.severity.value,
            },
        }
        for c in cases
    ]

    print(f"Running experiment over {len(dataset)} cases...")

    result = braintrust.Eval(
        name=settings.braintrust_project,
        data=dataset,
        task=task_fn,
        scores=[factuality_scorer, relevance_scorer, hallucination_scorer],
        metadata={"pipeline": "FullSOPPipeline", "strategy": "predict"},
    )

    print("Done. View results in the Braintrust UI:")
    print(f"  https://www.braintrust.dev/app/{settings.braintrust_project}")


if __name__ == "__main__":
    main()
