"""Application configuration using Pydantic Settings."""
import json
from typing import List
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "VidSOPEngine"
    app_env: str = "development"
    debug: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database - Neon Postgres in production, sqlite for local dev
    database_url: str = "sqlite+aiosqlite:///./data/vidsopengine.db"

    # Redis - Upstash rediss:// in production
    redis_url: str = "redis://localhost:6379/0"

    # Pipeline execution mode:
    #   "rq"     - enqueue to Redis (Upstash) for the vidsopengine-worker
    #              service to consume. Production default. The API stays
    #              responsive; heavy work (ffmpeg, vision, DSPy) runs in
    #              an isolated worker process.
    #   "inline" - run process_video_pipeline in a FastAPI BackgroundTask
    #              inside the API process. Useful for local dev / small
    #              deployments without a separate worker. The API route
    #              auto-falls-back to inline if RQ enqueue fails, so 'rq'
    #              is safe even when the worker is briefly unavailable.
    pipeline_run_mode: str = "inline"

    # Worker-side retry policy for transient failures (Groq, R2, ffmpeg).
    # Permanent failures (invalid video, schema validation) are NOT retried -
    # the worker tasks raise non-retriable exceptions for those.
    pipeline_max_retries: int = 3
    pipeline_retry_backoff_seconds: int = 30  # base; actual = base * 2**attempt

    # Production stability flags
    disable_auto_complexity_classifier: bool = True   # require explicit user choice
    dspy_freeze_prompts: bool = True                  # no runtime DSPy optimization

    # Kill-switch: route every physical video through PhysicalPipeline,
    # ignoring pipeline_complexity. Use this to bypass atomic_simple when it
    # misbehaves - procedural quality is preserved untouched. UI videos are
    # unaffected (they have their own dedicated pipeline).
    force_procedural_only_for_physical: bool = False

    # Failure digest - weekly email to the operator summarising recent failure
    # activity. POST /admin/send-digest sends it; hit manually or via Render's
    # cron jobs feature. Resend is the preferred provider (one HTTP call, no
    # SMTP). When resend_api_key is empty, the endpoint logs and returns stats
    # only - useful for local dev or before the email integration is set up.
    digest_admin_email: str = ""
    digest_email_from: str = "VidSOPEngine <noreply@vidsopengine.in>"
    resend_api_key: str = ""

    # SMTP (used for transactional emails like password-reset OTPs).
    # Works with any provider that speaks SMTP — Gmail (app password),
    # SES SMTP, Mailtrap, Brevo, Postmark, etc. When smtp_host is empty,
    # the email helper logs the message and (in dev mode) returns the
    # OTP inline to the caller so you can still test the flow before
    # plugging in a real mailer.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    # Dev convenience: when True AND smtp_host is empty, /auth/forgot-password
    # returns the OTP in the response body so the frontend can show it.
    # NEVER enable in production — defeats the whole "email-only" check.
    auth_expose_otp_in_dev: bool = False

    # LLM provider + model routing
    llm_provider: str = "groq"  # groq | together; task models can override by prefix
    together_api_key: str = ""
    together_base_url: str = "https://api.together.xyz/v1"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "https://vidsopengine.in"

    # Task-specific defaults. Keep transcription on Groq Whisper by default;
    # route vision and text-heavy synthesis/extraction through Together.
    # These defaults are Together serverless models so the standard Render
    # deployment does not require dedicated endpoints.
    sop_synthesis_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
    sop_verification_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
    workflow_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
    # NOTE: an older default here was "Qwen/Qwen3.5-9B" which Together AI does
    # not serve (Qwen versions are 1.5 / 2 / 2.5 / 3 — there is no 3.5). Every
    # checklist generation call 500'd against that name, so checklists never
    # appeared. Use the same model as workflows — same shape, same vendor.
    checklist_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
    # Training generation has the most demanding output schema of any
    # extractor (sections + practice scenarios + multi-option assessment).
    # Llama-3.3-70B handles it in English but routinely returns an empty
    # output field in Hindi (the JSON adapter fails to extract a parseable
    # object), so the same Qwen model that workflows + checklists use is the
    # safer default. Llama remains available via TRAINING_MODEL override.
    training_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
    # Dedicated model for SOP translation. Llama-3.3-70B handles multilingual
    # operational text reliably and avoids the JSON-mode quirks we hit with
    # the Qwen synthesis model. Override via SOP_TRANSLATION_MODEL.
    sop_translation_model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    sop_ab_test_models: str = (
        "Qwen/Qwen3-235B-A22B-Instruct-2507-tput,"
        "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    )

    # LLM provider resilience (Phase D)
    llm_circuit_breaker_failure_threshold: int = 5      # consecutive failures before opening
    llm_circuit_breaker_cooldown_seconds: float = 60.0  # how long the breaker stays open

    # Per-user monthly token budget. 0 = unlimited (default keeps the door
    # open for pilots; flip a budget on for self-serve customers). Counter
    # is reset by an external cron - there is no auto-reset in code yet.
    token_budget_monthly_default: int = 0

    # Platform bootstrap. Set SUPERADMIN_EMAIL in production to promote an
    # existing account to platform superadmin on boot. If the account does not
    # exist, SUPERADMIN_PASSWORD is required and a new superadmin is created.
    # Existing passwords are not reset unless SUPERADMIN_RESET_PASSWORD_ON_STARTUP
    # is explicitly true.
    superadmin_email: str = ""
    superadmin_password: str = ""
    superadmin_reset_password_on_startup: bool = False

    # Stuck-row watchdog: rows in 'transcribing' / 'sop_generating' for longer
    # than this are auto-failed on the next boot OR by the watchdog (when run).
    stuck_row_ttl_minutes: int = 10

    # LLM response cache (Phase E). Vision calls are the most expensive AND
    # most cacheable - same frame from a re-upload returns the same result.
    # Backend picks Redis when REDIS_URL looks valid, in-memory otherwise.
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = 30 * 24 * 3600  # 30 days

    # File storage - Cloudflare R2 (S3-compatible) in production.
    # When r2_bucket + r2_endpoint are unset, falls back to local disk
    # (settings.upload_dir) so dev keeps working.
    r2_bucket: str = ""
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_public_base_url: str = ""        # optional CDN/public R2 URL prefix
    r2_presigned_ttl_seconds: int = 3600

    # Local working dir - used both as the dev fallback for storage AND as
    # an ephemeral scratch dir for ffmpeg/Whisper when R2 is in use.
    upload_dir: str = "./data/videos"
    max_file_size_mb: int = 500

    # Whisper
    whisper_model_size: str = "base"

    # Groq for Whisper transcription, OpenRouter for vision, Together for text.
    vision_provider: str = "openrouter"
    vision_model: str = "qwen/qwen3-vl-8b-instruct"

    groq_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    groq_vision_model: str = "llama-3.2-11b-vision-preview"

    # Braintrust - eval observability + scoring (no-op if key unset)
    braintrust_api_key: str = ""
    braintrust_project: str = "VidSOPEngine"

    # LangSmith - stage-by-stage tracing + hallucination diagnosis (no-op if key unset)
    langsmith_api_key: str = ""
    langsmith_project: str = "VidSOPEngine"

    # Hallucination guard rails
    sop_confidence_threshold: float = 0.5   # below -> mark needs_review
    sop_self_check_enabled: bool = True     # run verification pass after generation
    sop_self_check_advisory_for_legacy: bool = True
    sop_few_shot_enabled: bool = True       # retrieve examples from failures.jsonl
    physical_sop_synthesis_mode: str = "legacy_detailed"

    # Procedural pipeline frame budget. Used as the floor when adaptive
    # frame counting is disabled or the video length is unknown.
    procedural_frame_count: int = 12

    # Adaptive frame count for the procedural pipeline. A flat 12 frames
    # was over-sampling 9-second clips and badly under-sampling minute-long
    # ones (one frame every 5s — most actions happened between frames, so
    # the synthesis model produced 5-6 collapsed steps for a video that
    # should have had 12-15). When enabled, the extractor probes the
    # video duration first and budgets one frame per
    # ``procedural_seconds_per_frame`` seconds of video, clamped to
    # [procedural_frame_min, procedural_frame_max].
    procedural_adaptive_frames: bool = True
    procedural_seconds_per_frame: float = 3.5
    procedural_frame_min: int = 8
    procedural_frame_max: int = 40

    # Startup wipe - keeps the app cheap by clearing per-deploy video data.
    # When True (set on Render), every startup deletes all video files,
    # frame files, and rows in videos/transcripts/sops/workflows.
    # Users and failures.jsonl are NEVER touched.
    wipe_videos_on_startup: bool = False

    # Adaptive granularity - atomic_simple pipeline tuning
    atomic_action_model: str = "qwen"          # "qwen" routes frame analysis through settings.vision_model
    atomic_simple_dense_frames: int = 16       # 12-24 recommended; 8 for procedural pipeline
    atomic_simple_classifier_threshold: float = 0.6  # below -> fall back to procedural

    # Phase-A.3 heuristic pre-filter — drop near-duplicate frames before
    # the VLM analysis pass. 0.92 = aggressive (drops only frames that
    # look near-identical). 1.0 disables the filter entirely. Tune
    # downward only after measuring synthesis quality — too aggressive
    # and you'll drop the during/after evidence the synthesiser needs.
    prefilter_static_threshold: float = 0.92

    # Action-timeline architecture (atomic_simple only - does NOT affect procedural)
    preserve_micro_actions: bool = True              # block aggressive merging of micro-actions
    atomic_merge_threshold: str = "low"              # "low" | "medium" | "high"
    atomic_confidence_expand_threshold: float = 0.55 # below -> ask LM to expand into finer steps
    atomic_use_transition_sampling: bool = True      # prefer scene-boundary frames for atomic
    atomic_use_timeline_pipeline: bool = True        # master switch for new architecture

    # Visual-grounding upgrade (atomic_simple only)
    atomic_use_adaptive_fps: bool = True             # continuous low-FPS extraction for micro-actions
    atomic_adaptive_fps: float = 1.5                 # frames per second when adaptive_fps is on
    atomic_adaptive_max_frames: int = 24             # hard cap on frames extracted under adaptive FPS
    atomic_strict_vocabulary: bool = True            # reject inferred-intent + generic verbs

    # Post-processing cleanup - keep operational outputs, drop heavy media.
    # Cleanup ONLY runs after persistence + Braintrust logging succeeds.
    #
    # Frame retention defaults to True: SOP step images reference frames
    # via /videos/{id}/frames/{name}. Deleting frames after generation
    # silently breaks the image strip in every saved SOP. Frames are tiny
    # (1280px JPEG, ~100-300 KB each, ~1-3 MB per SOP) so retention is
    # cheap. Source videos can still be deleted - they're the heavy file.
    delete_source_video_after_processing: bool = True
    delete_extracted_frames_after_processing: bool = False
    delete_temp_processing_dirs: bool = True
    retain_preview_keyframes: bool = False           # keep N compressed thumbs
    max_retained_preview_frames: int = 3
    cleanup_stale_processing_files: bool = True      # startup sweep
    cleanup_stale_file_age_hours: int = 6
    # Future-facing: an admin UI / per-user policy can flip this to True to
    # keep originals. Default stays delete-after-processing.
    retain_source_video: bool = False

    # JWT
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60
    refresh_token_expiration_days: int = 30
    auth_cookie_name: str = "vidsopengine_refresh"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"

    # CORS
    cors_origins: List[str] = ["http://localhost:3000"]
    # Optional regex applied alongside cors_origins. Useful for Vercel
    # preview URLs and custom-domain subdomains, e.g.:
    #   https://.*\.vercel\.app
    #   https://(.*\.)?vidsopengine\.in
    cors_origin_regex: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Accept either a JSON array string or a comma-separated string.

        Render's dashboard doesn't escape values, so the JSON-array form is
        easy to typo (smart quotes, missing brackets). The CSV form keeps
        deploys friction-free:
            CORS_ORIGINS=https://a.vercel.app,http://localhost:3000
        """
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return ["http://localhost:3000"]
            if v.startswith("["):
                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def sop_ab_test_model_list(self) -> List[str]:
        return [
            model.strip()
            for model in (self.sop_ab_test_models or "").split(",")
            if model.strip()
        ]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
