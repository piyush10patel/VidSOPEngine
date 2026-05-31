# MLOps — Tier 1 controls

The portfolio-scope MLOps surface. Everything here is free / open-source.

## 1. CI quality gate (every PR)

`.github/workflows/ci.yml` runs on every push + PR.

- **`backend-quality-gate`** — runs [`backend/evals/ci_gate.py`](../backend/evals/ci_gate.py), which scores each fixture's `baseline_sop` against its `expected_sop` using the deterministic metrics in [`evals/offline/metrics/deterministic.py`](../backend/evals/offline/metrics/deterministic.py). No LLM calls, zero cost. Fails the build if any metric falls below the threshold in `DEFAULT_THRESHOLDS`.
- **`frontend-lint-build`** — runs `eslint` + `next build`. Catches TS errors, broken imports, broken page routes.

To run the gate locally:

```bash
cd backend
python -m evals.ci_gate
python -m evals.ci_gate --strict      # tighter floors
python -m evals.ci_gate --min-f1 0.6  # relax for an exploratory branch
```

When a real regression slips through and you need to ratchet a threshold:

1. Drop the offending floor in `DEFAULT_THRESHOLDS`.
2. Commit with a message naming the fixture + metric + suspected cause.
3. Open a follow-up issue to restore the floor once the cause is fixed.

## 2. Audit log on every generated SOP

Every persisted SOP carries:

| Column | What it records |
|---|---|
| `prompt_version` | First 12 chars of the deploy commit SHA (`RENDER_GIT_COMMIT` / `VERCEL_GIT_COMMIT_SHA`), or `dev` for local runs |
| `model_used` | The synthesis model name at generation time (e.g. `Qwen/Qwen3-235B-A22B-Instruct-2507-tput`) |

Read from the `sops` table to attribute a regression to the deploy that produced it. The columns are populated in [`sop_generator_service.save_sop`](../backend/app/services/sop_generator_service.py) via the `_current_prompt_version()` helper.

## 3. Per-user monthly token budget

`TOKEN_BUDGET_MONTHLY_DEFAULT` controls the cap. `0` = unlimited (current production default), any positive integer = hard cap.

Mechanism:

- `users.tokens_used_this_period` increments by `ESTIMATED_TOKENS_PER_GENERATION` after each SOP save.
- `users.period_started_at` is the last reset timestamp; the counter rolls over automatically when a new UTC month is observed (no separate scheduler).
- Upload endpoint calls `assert_within_budget(db, user)` before accepting the file. Over-budget uploads return **HTTP 429** with `error_code=QUOTA_EXCEEDED`.
- Live snapshot: `GET /auth/me/usage` returns `{used, budget, remaining, unlimited, period_started_at}`.

To set a budget on Render: add `TOKEN_BUDGET_MONTHLY_DEFAULT=200000` to the web service env vars. That's roughly 25 SOPs/user/month at 8k tokens each.

## 4. Staging environment

Promote to production via `staging` → `main`. Two branches, two Render services, two Vercel environments.

**One-time setup**

1. **Create the staging branch** locally:
   ```bash
   git checkout -b staging
   git push -u origin staging
   ```

2. **Render** — Dashboard → New + Web Service → repo `VidSOPEngine`:
   - **Name**: `vidsopengine-api-staging`
   - **Branch**: `staging`
   - All other settings identical to production (free plan, inline pipeline, etc.)
   - Use **separate** Neon database + Cloudflare R2 bucket — don't share with prod.

3. **Vercel** — Project Settings → Git → **Production Branch** = `main`. Pushes to `staging` (and any other branch) automatically build as **preview deployments** at `<project>-git-staging-<user>.vercel.app`. No extra project required.

4. **GitHub branch protection** — Settings → Branches → add rule for `main`:
   - Require pull request before merging
   - Require status checks to pass: `backend-quality-gate`, `frontend-lint-build`
   - Block force-push

**Day-to-day flow**

```bash
# work on feature branch
git checkout -b feat/something
git commit -am "…"; git push

# open PR into staging (CI runs)
gh pr create --base staging

# after PR merges + you've eyeballed the staging site:
gh pr create --base main --head staging --title "Promote staging -> main"
```

The same CI gate fires on both PRs. Nothing reaches `main` (and therefore production) without passing the gate twice.

## What's intentionally NOT in Tier 1

- Live A/B routing of production traffic
- Drift detection on input distribution
- Model registry (MLflow / Vertex)
- Auto-rollback on quality drop
- Quality alerting via Slack/Discord
- Automated retraining cron

Those are Tier 2/3. See the README discussion for the roadmap.
