# VidSOPEngine — Architecture

A small full-stack project that turns a process video into a structured SOP
(Standard Operating Procedure) using an LLM pipeline, with a closed-loop
mechanism that lets the system learn from human corrections over time.

## Pieces

```
        ┌───────────────────────────────┐
        │  Next.js frontend (Vercel)    │
        │  Login / register             │
        │  Upload video                 │
        │  Edit + view generated SOPs   │
        └───────────────┬───────────────┘
                        │ HTTPS (JWT)
        ┌───────────────▼───────────────┐
        │  FastAPI backend (Render)     │
        │  /auth /videos /sops          │
        │  /failures /feedback          │
        └───────┬────────────────┬──────┘
                │                │
        ┌───────▼──────┐  ┌──────▼───────┐
        │ Neon Postgres│  │ Cloudflare R2│
        │ (users, sops,│  │ (video files,│
        │  videos, …)  │  │  frames)     │
        └──────────────┘  └──────────────┘
                │
        ┌───────▼──────────────────────────┐
        │ AI providers                     │
        │  Groq Whisper (audio→text)       │
        │  OpenRouter Qwen3-VL (vision)    │
        │  Together / Groq (synthesis)     │
        └──────────────────────────────────┘
```

## SOP pipeline (the interesting bit)

For each uploaded video the backend runs three pipelines, routed by a tiny
complexity classifier:

| Pipeline | When it runs | Output style |
|---|---|---|
| `physical` | Long real-world recordings | Procedural, evidence-cited SOP |
| `atomic_simple` | Short single-task clips | Compact granular SOP |
| `ui` | Screen recordings | UI-flow SOP |

All three live in `backend/app/services/sop_pipelines/`.

The physical / atomic-simple pipelines share these stages:

1. **Transcription** — Whisper-large-v3 via Groq (audio → text).
2. **Frame extraction** — ffmpeg pulls ~12 representative frames.
3. **Vision pass** — each frame is described independently by Qwen3-VL.
4. **Synthesis** — a single LLM call grounds the SOP in the transcript +
   frame descriptions. The model is asked to cite a `source_frame_num` for
   every step so the UI can show the matching screenshot.
5. **Self-check** — a verifier model re-reads each step against the source
   and tags `verified=true/false`. Anything unverified gets `needs_review`.
6. **Translation** — output language is picked per user; SOPs can be
   re-translated on demand (`POST /videos/{id}/sop/translate`).

## Closed loop

When a step is marked wrong or a whole SOP is corrected by a reviewer, the
backend appends a record to `app/datasets/data/failures.jsonl`. That file
is the only training signal. Two things use it:

- **DSPy modules** (`app/dspy_modules/`) — the synthesis prompt and the
  verifier prompt are DSPy programs. `backend/evals/dspy_optimize.py`
  bootstraps few-shot examples from failures.jsonl and freezes the
  optimized prompt at deploy time (prompts are never tuned at runtime).
- **RAG retrieval** (`app/dspy_modules/retrieval.py`) — at SOP generation
  time, the closest prior failures are pulled in as few-shot exemplars so
  the model sees both "good" and "previously-wrong" examples.

## Observability

Every model call is wrapped in `app/observability/`:

- `braintrust_client.py` — per-call traces, scoring
- `langsmith_client.py` — chain-level traces
- `timing.py` — explicit timer dict surfaced in `generation_metadata`

Both are no-ops when their API keys are absent, so local dev works
without any account.

## Language system

Two layers, mirrored:

- **Backend** — `app/core/languages.py` is the registry. The SOP
  translation service reads from it. Adding a language means one dict
  entry here.
- **Frontend** — `src/contexts/I18nContext.tsx` mirrors the registry
  and resolves `t('key')` calls. Bundles live in
  `frontend/messages/<code>.json`.

The registry is the single source of truth. Never branch on a literal
locale code outside it.

## Why the closed loop exists

A static prompt regresses the moment a new edge case appears. Hard-coding
fixes drifts the prompt from the ground truth. The closed loop keeps the
prompt frozen *and* responsive: corrections accumulate in failures.jsonl;
the next offline DSPy optimization run picks them up; the next deploy
ships an updated prompt. No prompt tuning at runtime, no silent drift.
