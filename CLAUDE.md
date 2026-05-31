# VidSOPEngine Engineering Principles (minimal build)

This is the portfolio-scope variant of VidSOPEngine: auth + the SOP pipeline
(video → transcribe → vision → synthesize → managed SOP), with the closed-loop
ML stack intact. The full product (workflows, checklists, training, tasks, ops
layer, admin) lives on the `full-version-backup` branch.

The full architecture lives in [docs/architecture.md](docs/architecture.md);
this file is the rules-of-engagement.

---

## Mandatory rules

### External dependencies behind interfaces
- All AI provider calls go through `app/services/llm/`. No direct
  `from groq import Groq` in routers/services/pipelines.
- All object storage goes through `app/services/storage.py` (`Storage`
  Protocol). No direct boto3 calls outside that module.
- All DB queries go through SQLAlchemy models in `app/models/`. No raw
  SQL in routers (services may use `text()` for migrations only).

### Timeouts on every external call
- Groq vision: 30s
- Groq Whisper: 60s
- Groq chat (synthesis): 30s
- R2 upload/download: socket_timeout=10
- ffmpeg subprocess: explicit `timeout=` in subprocess.run

A call without an explicit timeout is a bug, not a missing feature.

### Migration discipline
- Every new column → entry in `app/models/base.py:init_db.migrations` list.
- Each migration runs in its own transaction.
- Default values use Postgres-friendly literals (`FALSE` not `0` for
  BOOLEAN; `CURRENT_TIMESTAMP` not `NOW()` for cross-dialect).

### Per-user data scoping
- Every router uses `Depends(get_current_user)`.
- Every service query filters by `user_id` (or `created_by` / `video.user_id`
  for SOPs) unless the operation is admin-scoped.

---

## Repository conventions

### Layer responsibilities
- `app/routers/` — thin HTTP handlers. No business logic.
- `app/services/` — business logic. No HTTP concerns.
- `app/dspy_modules/` — DSPy signatures + module classes ONLY. Pure
  prompt programs.
- `app/observability/` — Braintrust + LangSmith + scorers + timing.
  Side-effect-free except logging.
- `app/tasks/` — RQ worker entrypoints.
- `app/datasets/` — file-backed dataset access (failures.jsonl).

### Imports
- Absolute imports always (`from app.services.x import y`).
- Lazy-import heavy deps (DSPy, llama-index, boto3) inside functions
  when they're not needed at module-load time.

---

## Known invariants

- **INV-1**: Cleanup never runs before SOP is persisted.
- **INV-3**: `Video.file_path` stores a storage KEY, not a local path.
- **INV-4**: `users` table and `failures.jsonl` are NEVER wiped.
- **INV-7**: Visual evidence is the primary source of truth. Transcripts
  and user context are secondary signals.
- **INV-12**: Prompts are frozen at deploy time. Optimize offline via
  `evals/close_loop.py`.
- **INV-18**: The language registry (`app/core/languages.py` + frontend
  `LANGUAGE_REGISTRY`) is the single source of truth for supported
  languages. Never branch on a literal locale code outside of the registry.

---

## Anti-patterns to refuse

- "Add LLM optimization at runtime" → prompts are frozen (INV-12).
  Optimize offline via `evals/close_loop.py`.
- "Add a new service that talks directly to Groq" → use the LLM provider
  interface in `app/services/llm/`.
- "Wipe `failures.jsonl`" → INV-4. Suggest archiving instead.
- "Skip the migration list and ALTER manually" → round-trip through
  `init_db` so dev/staging matches prod.
- "Use raw user text directly in an LLM prompt" → user context is guidance
  only. Pass through structured channels — never inject verbatim.
- "Hard-code a check like `if locale === 'mr'`" → INV-18. Add a field to
  the registry entry and read it.

---

## When asked to add a new language

Three places to touch, in order:

1. **`backend/app/core/languages.py`** — one dict entry in
   `SUPPORTED_LANGUAGES`.
2. **`frontend/src/contexts/I18nContext.tsx`** — one entry in
   `LANGUAGE_REGISTRY` plus a top-level import of the bundle.
3. **`frontend/messages/<code>.json`** — fill it via the tool:
   ```bash
   cd backend
   python -m tools.translate_locale <code>
   ```

## When asked to "make it faster"

Profile first. Most P95 wins come from frame count tuning, not code changes.

## When asked to "make it cheaper"

Cost is dominated by Groq inference. Order of magnitude per SOP:
- 12 vision calls × ~$0.001 = ~$0.012
- 1 synthesis call × ~$0.005 = ~$0.005
- 1 self-check × ~$0.002 = ~$0.002
- Total: ~$0.02/SOP

To cut: lower frame count (quality drops), disable self-check (hallucination
risk), or move to a cheaper model.

---

## When in doubt

- Read recent commits — they explain why things are the way they are.
- Refuse to ship code that violates an invariant. Surface to the human first.
