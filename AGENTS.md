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
- **Live dashboard progress is stored in Postgres** (`job_sse_events` + `processing_jobs.progress_*`).
  Gemini runs on the worker; the dashboard EventSource hits the API. In-memory pub/sub cannot cross
  those processes, so the worker persists each SSE payload and the API replays/polls it. Do not
  revert `job_events.publish` to memory-only. Deploy **API and worker together** after this change;
  wait until the catalog queue is idle so a worker restart does not kill a processing lot.
- **Mobile confirms the real outcome** by polling `GET /jobs` after `/run` (see `mobile/lib/queue/sync.ts`);
  it no longer treats a `/run` 200 as "cataloged." A dedicated worker being down/mid-deploy is the main
  way jobs stall in `queued`. After `/run` the draft is marked **uploaded** immediately. Sync must
  **adopt** (not delete + re-upload) a `queued`/`processing`/`completed` job — retrying created
  duplicate lots and a second Gemini bill. Server-side, `process_job` skips Gemini when the same
  `mobile-<draft>-NN.jpg` names already exist on a lot in that sale (`same_sale=True`).
- **Queue leftover “failed / didn’t finish” notices are often false.** A full DB pool can time out
  the phone after `/run` while the worker still catalogs. Sync Phase 0 calls
  `GET /sales/{id}/cataloged-mobile-drafts` and marks matching drafts **uploaded** (clears the
  notice). Do not tell the user to Resend all when lots are already on the website. This needs
  an API deploy **and** an EAS Update (`production` channel) before phones stop showing the old copy.
- **Mobile OTA:** EAS Update is configured (`expo-updates`, `updates.url`, `runtimeVersion: appVersion`,
  per-profile channels in `eas.json`). TestFlight builds listen to the **`production`** channel.
  Human steps (Expo login + `eas update --channel production`) are in `mobile/README.md`
  under “Push a JS-only fix”. Native/dep changes still need `eas build` + `eas submit`.
  OTA only works on builds made **after** expo-updates was added. Do not run `eas update`
  from Cloud Agents unless a valid Expo token is available (this environment’s token is not).
  **`eas.json` `build.env` is not applied to `eas update`.** SDK 54 reads local `mobile/.env`.
  Always set `EXPO_PUBLIC_API_BASE` to the Railway API in the same command. Release bundles
  (`__DEV__ === false`) also drop localhost in `mobile/lib/config.ts` so a leftover `.env`
  cannot ship `http://localhost:8000` to field phones again.
- **Mobile Queue photo review is capture order.** Queue and capture-session preview walk
  `lot_photos.sort_order` (the order shots were taken). That is independent of website listing
  sort. Do not reorder queue thumbs for listing presentation.
- **Photo gallery order is not the same as capture order.** After catalog, `_finalize_open_lot`
  groups photos by triage `photo_role` (hero → item_front → angle_only → leftover → unusable → tape)
  while keeping capture order *within* each role. Dashboard **Sort for listing** reapplies that;
  **Fix photo order** restores capture order from `original_name` (`mobile-<draft>-NN.jpg`).
  Do not bring back naive hero-first-only reordering — that scrambled the rest of the gallery.
- **SKU-tape frames are never the lot hero.** After Stage-1 triage, `select_analysis_photos`
  coerces dedicated tape shots to `photo_role=tape` (including a mis-tagged `hero` that has a
  lot-tape marking but is not a wide group shot) and excludes tape from hero selection even
  when `shows_whole_lot` is true. Persisted roles feed listing sort, CT Seller cover, and the
  Hero shots ZIP. A group/table shot with tape at the *edge* can still be hero. Already-cataloged
  lots keep stored roles until they are cataloged again — do not Resend all just to refresh heroes.
- **CT Bids / CT Seller posts omit SKU-tape frames.** Dashboard **Post to CT Seller** drops
  `photo_role=tape`. Tape stays in the Estate-Ez gallery for SKU reference. Lots that were
  already posted keep whatever photos are on CT Bids until someone edits that item there;
  do not Resend all. Posting is dashboard-only — there is no browser extension.

### Testing / lint / build

- Backend tests: `cd backend && ../backend/.venv/bin/python -m pytest`. As of setup, 3
  pre-existing failures in `tests/test_lotting_textile.py` and
  `tests/test_lotting_no_components.py` (lotting-prose assertions, unrelated to environment);
  the other ~116 pass.
- **No linter is configured** anywhere (no ruff/flake8/eslint/pre-commit config, no lint
  script in any `package.json`). There is nothing to run for "lint" unless one is added.
- Frontend has only `dev` / `build` / `start` scripts (no tests/lint). Use `npm run dev`
  for development; `npm run build` for a production build check.
