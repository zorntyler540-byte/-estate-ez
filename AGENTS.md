# AGENTS.md

## Cursor Cloud specific instructions

This repo is the **CaringTransitions / Estate-EZ** monorepo (AI-assisted estate-sale
cataloging SaaS). See `README_SAAS.md` for the canonical local-dev commands and
`DEPLOY.md` for production/Railway details. Notes below cover only non-obvious,
cloud-specific gotchas; do not duplicate standard docs.

### Services

| Service | Path | Scope | Dev command | Notes |
|---|---|---|---|---|
| Backend API (FastAPI) | `backend/` | required | `backend/.venv/bin/uvicorn app.main:app --reload --port 8000` (run from `backend/`) | Core REST API all clients depend on. |
| Frontend web (Next.js 15) | `frontend/` | required | `NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev` (run from `frontend/`) | Dashboard UI on port 3000. |
| Catalog worker | `backend/` | optional | `backend/.venv/bin/python -m app.worker` (run from `backend/`) | Only needed if API sets `RUN_CATALOG_JOBS_IN_PROCESS=false`; default runs jobs in-process. |
| Mobile (Expo SDK 54) | `mobile/` | optional | `npm install` then `npx expo start` | Needs a device/emulator + Expo Go; not runnable headless. |
| Chrome extension | `extension/` | optional | load unpacked in Chrome | Vanilla JS, no build/deps; targets `*.ctbids.com`. |
| Playwright-CT scripts | `playwright-ct/` | optional | `npm install && npm run install:browsers` | Automation utilities against a live CTBids account, not a CI test suite. |

### Non-obvious gotchas

- **Python venv lives at `backend/.venv`** and is created by the update script. Always
  invoke it explicitly (`backend/.venv/bin/python`, `.../uvicorn`, `.../pytest`) — there is
  no auto-activation.
- **`main.py` at the repo root is a legacy CLI scanner, NOT the API.** The FastAPI app is
  `app.main:app` and must be started from inside `backend/` (its package is `app`).
- **DB defaults to SQLite in dev** (`backend/app.db`). No Postgres is required locally;
  tables auto-create on API startup (`Base.metadata.create_all`), so Alembic migrations are
  optional in dev. `ENVIRONMENT=production` would reject SQLite — keep it `development`.
- **`backend/.env` is required for local dev** and is git-ignored. If missing, recreate it
  (SQLite `DATABASE_URL`, `ENVIRONMENT=development`, empty `GOOGLE_API_KEY`). Pydantic loads
  the cwd `.env` first then `backend/.env`, so run the API from `backend/`.
- **Frontend `NEXT_PUBLIC_API_BASE` is read at build/start time**, defaulting to
  `http://localhost:8000`. Set it in the shell before `npm run dev` if the API is elsewhere.
- **Gemini AI cataloging needs `GOOGLE_API_KEY`.** Without it the API/worker still start and
  all non-AI flows (auth, sales, lots, exports) work; only actual photo-analysis jobs are
  no-ops. The key is provided as a Cursor Cloud injected secret (an OS env var), which
  pydantic-settings reads with higher priority than `backend/.env`, so no manual wiring is
  needed — just start the API from a shell that has the env var. The photo pipeline entry
  point is `app.core.gemini.analyze_photo_from_bytes` (image bytes -> structured
  `InventoryTableEntry` row).
- **System deps** `python3.12-venv` and `libheif1` (HEIC photo support) are already baked
  into the environment; they are not part of the update script.

### Production architecture & catalog-upload reliability (mobile → API → worker)

The most important production workflow is: mobile app uploads a lot → API → AI catalog pipeline.
In production (Railway) the API, the catalog **worker**, and the Next.js web app run as **separate
services with separate, ephemeral disks**. Non-obvious rules that keep uploads from silently failing:

- **API service must set `RUN_CATALOG_JOBS_IN_PROCESS=false`**; the worker (`python -m app.worker`)
  leaves it `true` (default). If the API is left `true` it also runs Gemini on the web dyno (OOM/restart
  risk) and races the worker. See the comment on this setting in `backend/app/config.py`.
- **R2 must be configured on BOTH the API and the worker, pointing at the same bucket**
  (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT`). Because the services don't
  share a disk, an uploaded batch only reaches the worker via R2 (`job-batches/{account}/{batch}/…`).
  With R2 unset or a wrong key, the worker can't read the files → job fails "Input folder not found."
  All R2 mirror/restore helpers in `app/job_batch_storage.py` no-op when R2 is unconfigured.
- **`POST /jobs/{id}/run` fails loud in worker mode** if the batch isn't actually staged in R2
  (verified via `count_staged_job_files`), so a storage/config problem surfaces as a real error the
  phone retries on instead of a silent "queued" job the worker can never read.
- **Job lifecycle:** `receiving → queued → processing → completed|failed`. `GET /jobs` reconciles stale
  `processing`/`receiving` rows; the worker requeues its own orphans. Single-lot re-runs dedup via
  `_single_lot_batch_already_cataloged`; batch re-runs do **not** dedup (can duplicate lots).
- **Mobile confirms the real outcome** by polling `GET /jobs` after `/run` (see `mobile/lib/queue/sync.ts`);
  it no longer treats a `/run` 200 as "cataloged." A dedicated worker being down/mid-deploy is the main
  way jobs stall in `queued`.
- **Mobile OTA:** EAS Update is configured (`expo-updates`, `updates.url`, `runtimeVersion: appVersion`,
  per-profile channels in `eas.json`). JS-only fixes ship with `eas update --branch <channel>`; native/dep
  changes still require a new `eas build` + TestFlight submission. OTA only works on builds made **after**
  expo-updates was added.

### Testing / lint / build

- Backend tests: `cd backend && ../backend/.venv/bin/python -m pytest`. As of setup, 3
  pre-existing failures in `tests/test_lotting_textile.py` and
  `tests/test_lotting_no_components.py` (lotting-prose assertions, unrelated to environment);
  the other ~116 pass.
- **No linter is configured** anywhere (no ruff/flake8/eslint/pre-commit config, no lint
  script in any `package.json`). There is nothing to run for "lint" unless one is added.
- Frontend has only `dev` / `build` / `start` scripts (no tests/lint). Use `npm run dev`
  for development; `npm run build` for a production build check.
