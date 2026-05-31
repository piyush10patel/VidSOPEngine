# Offline eval suite for the Video → SOP pipeline

Run this when you want numbers about the pipeline's quality without
deploying or running real video processing. The harness loads synthetic
fixtures, runs the DSPy synthesis stage in-process, and grades the
output with three layers of metrics.

> ## When to actually use which tier
>
> Honest take after iterating on this for a while: the four tiers below
> have very different cost / value profiles. Skipping the wrong one
> wastes hours; running the wrong one wastes dollars.
>
> | Tier | Run cost | Use it when |
> | --- | --- | --- |
> | **Deterministic** (`--skip-llm --skip-giskard`) | $0, ~5 seconds | After every code change that touches synthesis / assignment / parsing. Catches schema, alignment, anchoring, source-grounding regressions. **This is the daily-driver pre-commit check.** |
> | **DeepEval** | $0.05–0.15 per fixture | Rarely. The 8b-instant judge is noisy; the 70b judge is expensive and slow. When you want a structured second opinion on prose quality you're already unsure about. |
> | **SOPBench** | $0.05–0.15 per fixture | When you want axis-level feedback (step quality / coherence / language / safety) with rationale strings on **one specific generated SOP**. Best path: drop the SOP into a fixture, run `--skip-synthesis`, read the rationales, ship the fix. |
> | **Giskard** | $0.20–1.00 per fixture | Drop on the floor. The adversarial scan re-runs synthesis on 5 perturbations and the signal it produces (does the pipeline crash on empty transcript? hindi? scrambled frames?) is easier to discover by manually testing one edge case. |
> | **`--auto-rewrite`** | +$0.05–0.15 per failing axis | When SOPBench flags a specific axis as failing AND you want to see what the judge thinks the fix looks like. Mostly diagnostic; usually you'll fix the prompt yourself. |
>
> **For the day-to-day "did I break anything" loop**, run only the
> deterministic tier. It's free, it's fast, and it catches every
> regression where a code change broke the structural shape of the
> output. Everything else is for one-off investigations.
>
> **For evaluating LLM judgment** (does the synthesised SOP actually
> read well, did training generate in Hindi correctly, did the right
> answer get marked right), uploading a real video and reading the
> diag-log lines I added in `sop_generator_service` and
> `training_service` is faster and more accurate than running the
> matrix. Grep production logs for:
>
>   - `[diag-sop]` — pipeline, frame count, step count, source_frame_num emission, output language
>   - `[diag-training]` — language plumbing into training generation, model used
>   - `[frame_assign]` — explicit anchors vs citation vs similarity vs fallback breakdown
>   - `[training_repair]` — correct_answer values the backend had to fix on save
>
> Those four lines together tell you which fix landed without running
> any judge calls.

```bash
cd backend

# Deterministic only — no LLM calls, runs in seconds, no API keys needed.
python -m evals.offline.runner --all --skip-llm --skip-giskard

# Everything (uses GROQ_API_KEY for the DeepEval judge).
pip install -r requirements-eval.txt
python -m evals.offline.runner --all

# Score a specific fixture's baseline SOP without calling DSPy.
# Useful for testing the metrics themselves.
python -m evals.offline.runner --skip-synthesis --fixtures uneven_pacing
```

## API keys (DeepEval + SOPBench tiers only)

Keys are read from environment variables **never from code**. The
runner auto-loads `backend/.env` at startup, so the same
`GROQ_API_KEY` you already use for production also works for the
eval suite — no separate config.

Three ways to provide a key, in order of preference:

1. **Local dev** — drop your key into `backend/.env` (which should be
   gitignored). The runner picks it up automatically. This is the
   normal path.
2. **CI** — store the key as a CI secret and `export GROQ_API_KEY=...`
   before invoking the runner.
3. **Render shell** — the key is already in the prod env. Useful for
   one-off debugging but consumes the production rate-limit / billing
   pool, so prefer local dev.

What if you don't have / don't want to use a key?

```bash
python -m evals.offline.runner --all --skip-llm --skip-giskard
```

The deterministic tier alone is responsible for catching most real
bugs the suite has flagged (Devanagari tokenizer, source_frame_num
write-back, step→image alignment drift). It runs instantly with no
external dependencies.

### Pre-flight banner

The runner now prints which tiers are actually going to run BEFORE it
makes any calls — so you know up front whether SOPBench will be
silently skipped because the key is missing or deepeval isn't
installed:

```
Tiers enabled for this run:
  Deterministic                 ON  (no API key required)
  DeepEval + SOPBench           ON  (judge=groq, key found)
  Giskard adversarial scan      ON  (5x synthesis calls per fixture)
  Auto-rewrite                  ON  (max 2 attempts per fixture)
```

### Cost ballpark per fixture run

| Tier | Approx Groq cost (per fixture) |
|---|---|
| Deterministic | Free |
| + DeepEval (3 GEval criteria × 5 sampled steps) | ~$0.01–0.05 |
| + SOPBench (4 axes) | ~$0.02–0.08 |
| + Giskard scan (5 perturbations, each re-synthesises) | ~$0.05–0.20 |
| + Auto-rewrite (max 2 rounds on failing axes) | ~$0.01–0.05 |

Three fixtures × full suite × auto-rewrite ≈ $0.30 worst case.

A JSON dump lands in `evals/offline/reports/run-<timestamp>.json`. Diff
two runs to see what got better or worse.

## What gets measured

### 1. Deterministic metrics (always on, zero LLM cost)

| Metric | What it tells you | Good |
| --- | --- | --- |
| **Step F1 / precision / recall** | Bipartite match between generated steps and the fixture's expected steps, using token overlap. | ≥ 0.8 |
| **Step temporal order** | Fraction of matched steps that appear in the same order as the expected SOP. | 1.0 |
| **Step → image alignment (mean)** | Average Jaccard overlap between each step's text and its assigned frame's vision description. **The smoking gun for "pictures don't match the steps".** | ≥ 0.3 |
| **Steps w/ bad image (< 10%)** | Count of steps whose image has almost no token overlap with the step text. | 0 |
| **Source grounding (mean / min)** | Fraction of each step's content words that appear in the transcript or any frame observation. Cheap proxy for hallucination. | mean ≥ 0.7, min ≥ 0.4 |
| **Hallucinated steps (< 50%)** | Steps whose grounding score fell below 50% — likely talking about something the source never mentions. | 0 |
| **Frame anchor coverage** | Fraction of steps with a populated `source_frame_num`. | 1.0 |
| **Frame uniqueness** | Fraction of populated `source_frame_num` values that are unique. **Catches "all closing steps share the last frame".** | 1.0 |
| **Frame strictly increasing** | Fraction of adjacent step pairs where the later step has a higher `source_frame_num`. | 1.0 |
| **Frame spread score** | Standard deviation of the values divided by the ideal proportional standard deviation. **< 1.0 means the LLM clustered values at one end.** | ≥ 0.7 |
| **Schema required / informative fields** | Coverage of required (title / description) and informative (evidence / source_frame_num / image_url) fields. | required = 1.0, informative ≥ 0.7 |

### 2. DeepEval semantic metrics (judge LLM = Groq Llama 3.3 70B)

Five steps per SOP are sampled evenly across the procedure. Each is
scored 0–10 (DeepEval normalises to 0–1 internally) on three GEval
criteria:

| Criterion | Question the judge answers |
| --- | --- |
| **Faithfulness** | Is the step supported by the transcript or the frame observations? |
| **Hallucination** | Does the step introduce tools, actions, or quantities that are NOT in the source? |
| **Actionability** | Is the step a single verb-first imperative an operator could execute without further interpretation? |

The judge model is configurable via `DEEPEVAL_JUDGE_MODEL` and defaults
to `groq/llama-3.3-70b-versatile`.

### 3. SOPBench-inspired 4-axis quality scorer (judge LLM = Groq)

> **Naming note**: the published *SOPBench* paper benchmarks how well
> AGENTS follow SOPs; it does not expose a pip-installable scorer for
> the QUALITY of a generated SOP. This module re-implements the spirit
> of its rubrics — covering the four axes the project cares about — as
> a `GEval`-style judge call. Different goal, similar methodology.

The scorer asks the Groq judge for a 0–10 score plus a one-line
rationale on each of:

| Axis | What the judge grades |
| --- | --- |
| **step_quality** | Verb-first, single-action, unambiguous steps. No filler. |
| **coherence** | Title-vs-steps consistency, ordering, no duplicates, no missing critical steps. |
| **language** | Devanagari fidelity for Hindi, natural Hinglish, no random English fallbacks for content words. |
| **safety** | Warnings present where source warranted them; PPE / hazards surfaced. Skipped automatically when the source has no safety signal. |

Failing axes (score < threshold, default 7.0; override via
`SOPBENCH_THRESHOLD`) are highlighted in the report and feed the
auto-rewriter when enabled.

```bash
python -m evals.offline.runner --all --skip-giskard         # SOPBench on, no rewrite
python -m evals.offline.runner --all --skip-giskard --auto-rewrite
```

### 4. Auto-rewrite (opt-in)

When `--auto-rewrite` is set AND SOPBench flags at least one failing
axis, the runner hands the SOP + source + judge rationales to a focused
text-only Groq call that PATCHES only the failing axes. Step-level
grounding fields — `source_frame_num`, `evidence`, `image_url`,
`confidence`, `linked_*` — are preserved verbatim by a merge step so
the rewriter cannot accidentally rewrite the pipeline's anchoring.

Bounded by `--rewrite-max-attempts` (default 2). Each attempt only
targets axes that are still failing, so the rewriter doesn't keep
mutating already-passing parts of the SOP.

The full trace lives in the JSON dump under `sopbench_rewrite`:

```jsonc
{
  "sopbench_rewrite": {
    "attempts": [
      {
        "attempt": 1,
        "axes_targeted": ["language", "safety"],
        "sop": {...},          // SOP after this rewrite
        "score": {...}         // SOPBench score after this rewrite
      }
    ],
    "final_sop": {...},
    "final_score": {...},
    "improved": true
  }
}
```

### 5. Giskard adversarial scan

Re-runs synthesis on perturbed inputs and scores each output with the
deterministic metrics. The default perturbations are:

| Perturbation | What it tests |
| --- | --- |
| `empty_transcript` | Can the pipeline produce a SOP from vision alone? |
| `single_frame` | Does it collapse / hallucinate when only one frame is provided? |
| `scrambled_frame_order` | Does it still produce monotonic frame anchoring when its inputs aren't ordered? |
| `duplicated_observations` | Does it dedupe repetitive frame descriptions instead of writing one step per repeat? |
| `hindi_transcript` | Does cross-language input (Hindi transcript + English vision) break the synthesis? |

A perturbation is considered "robust" when the pipeline returns a
non-empty SOP with `schema_coverage.required ≥ 0.8` — i.e. it didn't
crash and didn't collapse to a single placeholder step.

## Adding a fixture

A fixture is a JSON file under `evals/offline/fixtures/`:

```jsonc
{
  "name": "my_fixture",
  "transcript": "...",                  // the Whisper text
  "frame_observations": [               // synthetic vision output
    {"frame_num": 1, "image_url": "/test/frames/1.jpg", "description": "..."}
  ],
  "expected_sop": {                     // optional — drives Step F1/precision/recall
    "title": "...",
    "description": "...",
    "steps": [
      {"step_number": 1, "title": "...", "description": "...", "source_frame_num": 1}
    ]
  },
  "baseline_sop": {                     // optional — used by --skip-synthesis
    // same shape as the schema, used for testing the metrics
    // themselves without calling DSPy.
  }
}
```

Tips for writing useful fixtures:

- Keep them short (6–10 frames, 4–8 expected steps). Anything bigger
  burns judge cost and rarely surfaces a new failure mode.
- Make sure the vision descriptions are *granular*. "Operator at
  workbench" repeated across every frame degrades all the deterministic
  metrics in a way that mirrors the real production problem.
- The `baseline_sop` is a useful place to encode a *known bad pattern*
  (e.g. all closing steps anchored to the last frame) so you can verify
  the metrics still detect it after a refactor.

## Diffing two runs

```bash
python -m evals.offline.runner --all --skip-llm --skip-giskard       # baseline
# ... make changes ...
python -m evals.offline.runner --all --skip-llm --skip-giskard       # after

diff evals/offline/reports/run-<baseline>.json evals/offline/reports/run-<after>.json
```

The runner writes `run-<UTC timestamp>.json` per invocation so you keep
a chronological trail.
