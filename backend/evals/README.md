# SOP Prompt Evaluation

Run multiple prompt variations across the failure dataset using [Promptfoo](https://promptfoo.dev).

## The full close-the-loop pipeline

Each tool answers a different question. Together they form a feedback cycle:

```
production → LangSmith trace + diagnosis
                ↓ (auto-capture if needs_review=True)
       failures.jsonl ← human feedback (POST /sop/{id}/feedback)
                ↓
       Promptfoo eval (compare prompt variants)
                ↓
       DSPy MIPROv2 (optimize signature with examples)
                ↓
       Braintrust experiment (factual + relevance + hallucination)
                ↓
              VERDICT — keep new pipeline or revert
```

Run the whole cycle in one command:

```bash
cd backend
export GROQ_API_KEY=...
export BRAINTRUST_API_KEY=...           # optional
python -m evals.close_loop              # 4 stages, prints verdict
python -m evals.close_loop --skip-promptfoo --skip-braintrust   # DSPy only
```

Check current state without running anything:

```bash
curl $API/admin/loop-status -H "Authorization: Bearer $TOKEN"
```

---

## Prerequisites

```bash
# Promptfoo runs via npx — no install needed
# Set your Groq key
export GROQ_API_KEY=gsk_...
```

## One-time setup

The first time you run, fetch the failure dataset from production (or use your local copy):

```bash
# If running locally and you have data/failures.jsonl in the project root:
python export_dataset.py

# Or pull production failures over SSH/scp first, then point at the file:
python export_dataset.py --source ~/Downloads/failures.jsonl
```

This writes `tests.json` (gitignored — regenerate any time).

## Run the eval

```bash
npx promptfoo@latest eval
npx promptfoo@latest view    # open the comparison UI in the browser
```

Promptfoo runs every prompt in `prompts/` against every test case and applies the assertions defined in `promptfooconfig.yaml`. The view UI shows a side-by-side matrix: which prompt won which case, average scores, and the LLM-rubric explanations.

## Adding a new prompt variation

Drop a new `.txt` file into `prompts/` using `{{transcript}}` and `{{events}}` as placeholders, then list it in `promptfooconfig.yaml` under `prompts:`. Re-run `npx promptfoo@latest eval`.

## What's being measured

| Assertion | What it catches |
|-----------|-----------------|
| `is-json` | Output isn't valid JSON |
| `no_hallucinated_tools.js` | A tool appears in the SOP but not in the transcript or events |
| `step_count_match.js` | Predicted step count is >50% off from the labelled expected count |
| `llm-rubric` | Semantic correctness vs the labelled expected SOP (LLM-as-judge) |

## Picking a winner → DSPy

Once one prompt clearly wins, copy its instructions into the relevant DSPy Signature in [backend/app/dspy_modules/signatures.py](../app/dspy_modules/signatures.py) — that's the single source of truth at runtime. The eval is for **selecting**; DSPy is for **executing** with the winning prompt.

---

## Braintrust — production observability + human feedback

Promptfoo is for offline prompt selection. Braintrust runs continuously: every production SOP generation logs there with automatic scores, and a `/sop/{video_id}/feedback` endpoint accepts human ratings that land on the same trace.

### Setup

```bash
export BRAINTRUST_API_KEY=...   # also add on Render
```

### Metrics tracked

| Metric | Where | How |
|---|---|---|
| `hallucination_rate` | online (every prod call) | Programmatic — every tool must appear in transcript or events |
| `confidence_alignment` | online | Programmatic — `overall_confidence` vs avg per-step confidence |
| `factual_correctness` | offline experiments | LLM-as-judge vs labelled `expected_output` |
| `relevance` | offline experiments | LLM-as-judge — are steps about the procedure (not generic)? |
| `human_quality` | logged on demand | POST `/sop/{video_id}/feedback` from the UI |

### Run an offline experiment

```bash
cd backend
python -m evals.braintrust_eval
```

This generates an SOP for every case in `failures.jsonl` using the current DSPy pipeline, scores all three metrics, and pushes the run to Braintrust where you can compare it against past runs.

### Recording human feedback

```bash
curl -X POST $API/sop/$VIDEO_ID/feedback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"score": 0.3, "comment": "step 3 references a tool not shown",
       "failure_type": "hallucination"}'
```

When `score < 0.5` and `failure_type` is set, the case is also auto-appended to `failures.jsonl` so it's available for the next DSPy optimization run.

---

## LangSmith — stage-by-stage tracing + hallucination diagnosis

Promptfoo selects prompts. Braintrust scores outputs. **LangSmith answers "where in the pipeline did things go wrong?"**

### Setup

```bash
export LANGSMITH_API_KEY=...   # also add on Render (already in render.yaml as sync:false)
```

Without the key, every `@traceable` decorator is a no-op — local dev keeps working.

### What gets traced

Every SOP generation produces a tree:

```
generate_sop                           {video_id}
├── analyze_frame                      {frame_num: 1}     ← which frame
├── analyze_frame                      {frame_num: 2}     ← what action it saw
├── ...
├── synthesize_sop                     {n_events: 8}      ← which events fed in
└── diagnose_hallucination (only if hallucination_rate < 1.0)
```

In the LangSmith UI, click any frame span to see the raw vision-model output. Click `synthesize_sop` to see exactly which events DSPy received and which steps came out. The mapping **frame → action → step** is visible end-to-end because every step's `evidence` field already cites the frame number.

### Hallucination root-cause diagnosis

When the online `hallucination_rate` scorer flags an output, [`diagnose_hallucination`](../app/observability/diagnosis.py) runs an LLM judge per ungrounded item to classify why:

| Root cause | Means |
|---|---|
| `action_missing` | Pure invention — no source mentions it |
| `wrong_grouping` | Action is in source but step bundles unrelated actions |
| `constraint_ignored` | Generic filler step despite the "no invention" rule |
| `synonym_miss` | False positive — action IS in source under a different name |

The diagnosis lands on the trace as a child span, so in the UI you see the failed step and the verdict side by side. Filter by `root_cause = action_missing` to triage prompt fixes vs grounding-check fixes.
