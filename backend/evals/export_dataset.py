"""Convert data/failures.jsonl → tests.json in Promptfoo's format.

Run from backend/evals/:
    python export_dataset.py
    python export_dataset.py --source /path/to/failures.jsonl

Each failure case becomes one test, with `transcript`, `events`, and
`expected_output` exposed as Promptfoo `vars` so prompts and assertions
can reference them.
"""
import argparse
import json
import sys
from pathlib import Path

# Allow `python export_dataset.py` to import app.* without installing the package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.datasets.failures import iter_cases, _dataset_path  # noqa: E402


def case_to_test(case) -> dict:
    """Map one FailureCase → one Promptfoo test entry."""
    events = [
        {
            "frame_num": obs.get("frame_num"),
            "action": obs.get("description", "")[:300],
        }
        for obs in case.input.frame_observations
    ]
    return {
        "description": f"{case.failure_type.value} | {case.id}",
        "vars": {
            "transcript": case.input.transcript,
            "events": json.dumps(events) if events else "[]",
            "expected_output": json.dumps(case.expected_output),
        },
        "metadata": {
            "case_id": case.id,
            "video_id": case.video_id,
            "failure_type": case.failure_type.value,
            "severity": case.severity.value,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="Override path to failures.jsonl")
    parser.add_argument("--out", default="tests.json", help="Output path (default: tests.json)")
    args = parser.parse_args()

    if args.source:
        # Manually iter without going through the settings-derived path
        path = Path(args.source)
        if not path.exists():
            print(f"ERROR: {path} not found", file=sys.stderr)
            sys.exit(1)
        from app.schemas.failure import FailureCase
        cases = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cases.append(FailureCase.model_validate_json(line))
                except Exception as e:
                    print(f"  skipping malformed line: {e}", file=sys.stderr)
    else:
        path = _dataset_path()
        cases = list(iter_cases())

    if not cases:
        print(f"WARNING: no cases found in {path}")
        print("Record some failures first via POST /failures, then re-run.")
        Path(args.out).write_text("[]")
        return

    tests = [case_to_test(c) for c in cases]
    Path(args.out).write_text(json.dumps(tests, indent=2))

    by_type = {}
    for c in cases:
        by_type[c.failure_type.value] = by_type.get(c.failure_type.value, 0) + 1

    print(f"Wrote {len(tests)} tests → {args.out}")
    print(f"Source: {path}")
    print(f"Breakdown: {by_type}")


if __name__ == "__main__":
    main()
