# VidSOPEngine

Turn a process video into a structured Standard Operating Procedure.

Upload a recording (a workflow, a task, a screen capture), and the pipeline
transcribes the audio, samples representative frames, asks a vision model to
describe each one, then synthesizes a step-by-step SOP grounded in both
signals. Every step cites a source frame and is verified against the
evidence before being saved.

A closed loop learns from human edits: corrections accumulate in a JSONL
dataset that an offline optimizer uses to improve the prompts at the next
deploy. Prompts are frozen at runtime — no silent drift.

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, Chakra UI, Tailwind v4 |
| API | FastAPI, uvicorn, SQLAlchemy (async) |
| Database | Neon Postgres (asyncpg) |
| Object storage | Cloudflare R2 (boto3, S3-compatible) |
| Transcription | Groq Whisper-large-v3 |
| Vision | OpenRouter Qwen3-VL-8B |
| Text synthesis | Together Qwen3-235B + Llama-3.3-70B |
| ML stack | DSPy, llama-index BM25, Braintrust, LangSmith |
| Frame extraction | PySceneDetect, OpenCV, ffmpeg |

## Local dev

**Backend** (requires `ffmpeg` on PATH):

```bash
cd backend
pip install -r requirements-render.txt
uvicorn app.main:app --reload --port 8000
```

Defaults to SQLite + local disk when LLM / R2 / Postgres URLs are unset, so
the auth flow and manual SOP editor work offline with zero configuration.
For the full AI pipeline, drop a `.env` in `backend/` with `GROQ_API_KEY`,
`TOGETHER_API_KEY`, and `OPENROUTER_API_KEY`.

**Frontend**:

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
```

Open http://localhost:3000.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the pipeline diagram,
provider routing, and how the closed loop feeds back into the next prompt
optimization run.

## Deploy

- **API** → Render web service. `render.yaml` configures Python 3.11,
  inline pipeline mode, and the full env-var contract.
- **Frontend** → Vercel. Set `NEXT_PUBLIC_API_URL` to the Render URL;
  Vercel auto-detects Next.js.
- **DB** → Neon (paste the pooled connection string as `DATABASE_URL`).
- **Storage** → Cloudflare R2 (paste credentials as `R2_*`).

## Project layout

```
backend/
  app/
    routers/         # FastAPI HTTP handlers
    services/
      sop_pipelines/ # physical / atomic_simple / ui pipeline classes
      llm/           # provider abstraction + routing + cache
    dspy_modules/    # DSPy signatures + compiled programs
    observability/   # Braintrust + LangSmith wiring
    models/          # SQLAlchemy ORM
    datasets/        # failures.jsonl access layer
  evals/             # offline prompt optimization + golden datasets
frontend/
  src/app/           # Next.js App Router pages
  src/components/    # SOPViewer, SOPEditor, UploadForm, …
  src/contexts/      # i18n + auth contexts
  messages/          # en / hi / mr translation bundles
```
