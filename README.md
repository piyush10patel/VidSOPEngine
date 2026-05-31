# VidSOPEngine — Video to SOP (minimal build)

Upload a process video, get back a structured SOP. Login/register, multi-language UI, and the closed-loop ML pipeline behind it — stripped down from the full VidSOPEngine product into a portable portfolio app.

## Scope of this build

Kept:
- Auth (email/password login + register, password reset via OTP)
- Video upload → transcribe (Whisper) → vision frame analysis → SOP synthesis
- Three SOP pipelines: physical / atomic-simple / UI (auto-routed via a classifier)
- Managed SOP library with folders
- SOP translation (English / Hindi / Marathi)
- Closed-loop ML: DSPy modules, failures.jsonl, RAG over prior corrections, Braintrust + LangSmith observability

Removed (lived in the full product):
- Workflows, checklists, training modules
- Tasks, team, organizations, services, operations, orders
- Documents library, notifications, admin / superadmin
- Marketing pages, pricing, pilot facility

## Stack

| Layer | Service |
|---|---|
| Frontend | Next.js 16 + React 19 → Vercel |
| API | FastAPI + uvicorn → Render |
| Database | Neon Postgres (asyncpg) |
| Object storage | Cloudflare R2 (boto3, S3-compat) |
| AI inference | Groq Whisper + OpenRouter vision + Together text |

## Local dev

Backend:
```bash
cd backend
pip install -r requirements-render.txt
uvicorn app.main:app --reload --port 8000
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

Defaults to SQLite + local-disk storage when the relevant URLs are unset, so it runs fully offline.

## Deploy

- API → Render (see [render.yaml](render.yaml); update `CORS_ORIGINS` to your new frontend domain)
- Frontend → Vercel; set `NEXT_PUBLIC_API_URL` to the Render URL

For full architecture / pipeline / ML notes see [docs/architecture.md](docs/architecture.md) and [docs/pipelines.md](docs/pipelines.md). Sections covering workflows, checklists, training, etc. describe the full product and don't apply to this build.
