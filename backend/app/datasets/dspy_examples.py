"""Convert FailureCase records → dspy.Example for training/optimization.

Usage:
    import dspy
    from app.datasets.dspy_examples import to_dspy_examples
    from app.datasets.failures import load_all
    from app.dspy_modules.pipeline import SOPGenerationPipeline

    examples = to_dspy_examples(load_all())
    train, dev = examples[:40], examples[40:]

    optimizer = dspy.MIPROv2(metric=sop_match_metric)
    optimized = optimizer.compile(
        SOPGenerationPipeline(),
        trainset=train,
        valset=dev,
    )
"""
import json
from typing import List

import dspy

from app.schemas.failure import FailureCase


def _expected_to_outputs(expected: dict) -> dict:
    """Map an SOPSchema-shaped dict back to the SOPSynthesis output fields."""
    steps = expected.get("steps", [])
    # Convert SOPStep schema fields back into the LLM's expected output shape
    canonical_steps = [
        {
            "step_number": s.get("step_number"),
            "title": s.get("title", ""),
            "instruction": s.get("description", ""),
            "objects": s.get("tools", []),
            "checks": s.get("checks", []),
            "evidence": s.get("evidence", []),
            "confidence": s.get("confidence", 1.0),
            "notes": s.get("notes"),
        }
        for s in steps
    ]
    return {
        "title": expected.get("title", ""),
        "summary": expected.get("description", ""),
        "steps_json": json.dumps(canonical_steps),
        "overall_confidence": str(expected.get("overall_confidence", 1.0)),
        "warnings_json": json.dumps(expected.get("warnings", [])),
    }


def to_dspy_examples(cases: List[FailureCase]) -> List[dspy.Example]:
    """Convert failure cases to DSPy training examples.

    Cases with frame_observations train the vision-assisted SOPSynthesis;
    cases without train the text-only TextOnlySOPSynthesis.
    """
    examples = []
    for case in cases:
        outputs = _expected_to_outputs(case.expected_output)

        if case.input.frame_observations:
            events = [
                {
                    "frame_num": obs.get("frame_num"),
                    "action": obs.get("description", "")[:200],
                }
                for obs in case.input.frame_observations
            ]
            ex = dspy.Example(
                transcript=case.input.transcript,
                events=json.dumps(events),
                **outputs,
            ).with_inputs("transcript", "events")
        else:
            ex = dspy.Example(
                transcript=case.input.transcript,
                **outputs,
            ).with_inputs("transcript")

        examples.append(ex)

    return examples


def sop_match_metric(example: dspy.Example, prediction, trace=None) -> float:
    """Simple metric: 1.0 if predicted step count matches expected, else partial credit.

    Replace with semantic similarity (embedding distance) once the dataset is large
    enough to warrant it.
    """
    try:
        expected_steps = json.loads(example.steps_json)
        predicted_steps = json.loads(prediction.steps_json)
    except (json.JSONDecodeError, AttributeError):
        return 0.0

    if not expected_steps:
        return 0.0

    # Step count similarity (penalises hallucinated extra steps)
    count_score = 1.0 - min(
        abs(len(expected_steps) - len(predicted_steps)) / len(expected_steps),
        1.0,
    )

    # Title overlap as a coarse content check
    expected_titles = {s.get("title", "").lower().strip() for s in expected_steps}
    predicted_titles = {s.get("title", "").lower().strip() for s in predicted_steps}
    if expected_titles:
        title_score = len(expected_titles & predicted_titles) / len(expected_titles)
    else:
        title_score = 0.0

    return 0.5 * count_score + 0.5 * title_score
