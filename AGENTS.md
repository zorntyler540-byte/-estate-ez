# AGENTS.md

## Cursor Cloud specific instructions

This repo is the **CaringTransitions / Estate-EZ** monorepo (AI-assisted estate-sale
cataloging SaaS). See `README.md` for the canonical local-dev commands and
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
  optional in dev. Existing SQLite files get additive ALTERs in `startup()` (sale zip `kind`,
  lot audience columns, photo listing-sort fields). `ENVIRONMENT=production` would reject
  SQLite — keep it `development`.
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
- **Dashboard leftover FAILED receiving cards are often superseded retries.** If phone Sync dies
  mid-lot, the abandoned `receiving` row is marked failed after 30 minutes (“Upload never
  finished”) even when a later Sync already cataloged the same SKU. `GET /jobs` (and the worker
  sweep) completes those orphan-upload rows when the sale already has that SKU, a completed job
  with the same SKU, or matching `mobile-<draft>-NN.jpg` names. Gemini / Cancelled failures stay
  failed. Dashboard **Retry failed** / per-row **Run again** re-queues those (photos still staged);
  leftover mid-upload / lost-disk failures cannot be retried from the website — delete the job and
  Sync from the phone. The dashboard also treats matching FAILED cards as completed so they leave
  the “active” list. Do not tell staff to Resend all — the lots are already on the website.
  **`GET /jobs` reconcile is debounced (~20s per account).** Dashboard and phone Sync poll this
  endpoint constantly during uploads; running leftover-job healing on every poll exhausted the
  Postgres QueuePool and froze the website on “Loading dashboard…”. The worker sweep still heals
  globally. Do not put R2 HEAD / CT Seller HTTP / Pillow on a `Depends(get_db)` session.
  Photo ingest (`POST /jobs/upload` and `/jobs/{id}/upload-files`) authenticates with a
  short-lived session so multipart receive does not pin a pool connection.
- **Mobile OTA:** EAS Update is configured (`expo-updates`, `updates.url`). TestFlight
  binaries before lot videos use runtime `1.0.0`. The video native build uses runtime
  **`1.1.0`** (`mobile/app.json`) so the old production OTA cannot overwrite the new app.
  per-profile channels in `eas.json`). TestFlight builds listen to the **`production`** channel.
  Human steps (Expo login + `eas update --channel production`) are in `mobile/README.md`
  under “Push a JS-only fix”. Native/dep changes still need `eas build` + `eas submit`.
  OTA only works on builds made **after** expo-updates was added.
  Cloud Agents publish with the injected secret **`EXPO_TOKEN`** (Expo personal access token
  for account `vtz1900`, created at https://expo.dev/settings/access-tokens). The secret
  name is already wired; if `eas whoami` says the bearer token is invalid, replace the
  Cursor secret with a freshly created token (do not put it in git or chat). Then:
  `cd mobile && EXPO_PUBLIC_API_BASE=https://caringtransitions-ai-production.up.railway.app eas update --channel production --non-interactive --message "…"`.
  **`eas.json` `build.env` is not applied to `eas update`.** SDK 54 reads local `mobile/.env`.
  Always set `EXPO_PUBLIC_API_BASE` to the Railway API in the same command. Release bundles
  (`__DEV__ === false`) also drop localhost in `mobile/lib/config.ts` so a leftover `.env`
  cannot ship `http://localhost:8000` to field phones again.
- **Mobile Queue photo review is capture order.** Queue and capture-session preview walk
  `lot_photos.sort_order` (the order shots were taken). That is independent of website listing
  sort. Do not reorder queue thumbs for listing presentation.
- **Photo gallery order is not the same as capture order.** After per-photo triage, one extra
  Flash call (`plan_listing_gallery`) sees all frames as 512px thumbs and returns shopper order
  plus corrected Group/Item/Detail tags. Cost is on the order of half a cent per lot at current
  Flash rates (not Pro). If that call fails or the lot has 1 or >40 photos, `_finalize_open_lot`
  falls back to deterministic chapter sort: true group shots, then each item as a chapter, then
  labels, then measuring-tape, then SKU tape. Dashboard **Sort for listing** reapplies the
  deterministic sort (it does not re-call Gemini). Do not bring back naive hero-first-only
  reordering or a separate “Fix photo order” capture-order reset.
- **SKU-tape frames are never the lot hero.** After Stage-1 triage, `select_analysis_photos`
  coerces dedicated tape shots to `photo_role=tape` (including a mis-tagged `hero` that has a
  lot-tape marking but is not a wide group shot) and excludes tape from hero selection even
  when `shows_whole_lot` is true. Persisted roles feed listing sort, CT Seller cover, and the
  Hero shots ZIP. A group/table shot with tape at the *edge* can still be hero. Already-cataloged
  lots keep stored roles until they are cataloged again — do not Resend all just to refresh heroes.
- **Lots can move between open Estate-EZ sales.** `POST /lots/move-to-sale` and
  `POST /lots/{id}/move` change `lot.sale_id` only (photos/videos stay on the lot).
  Cover slots on the old sale that used that lot's photos are cleared. Closed sales
  cannot be destinations. Posted CT Seller items are not edited — staff still fix
  those on seller.ctbids.com if needed. Dashboard **Move** and the lot Details
  **Sale** panel both call this. Do not Resend-all after a move.
- **Lot videos are extra media, not catalog frames.** The phone capture session has a Photo /
  Video toggle (`expo-camera` `recordAsync`, 45s / 80 MB / 8 per lot). Clips upload to
  `POST /lots/{id}/videos/upload` after the lot exists (append immediately; new lots wait until
  catalog finishes). They are **not** sent through Gemini and **not** posted to CT Bids.
  Still photos are still required to catalog a lot. Playing uses a signed R2 URL
  (`GET …/videos/{id}/media-url`). Microphone permission in `app.json` needs a **new native
  build** (runtime `1.1.0`). Do not publish video JS to the `1.0.0` production channel.
  Do not put videos in `/jobs/upload` folders.
- **CT Bids / CT Seller posts omit SKU-tape frames.** Dashboard **Post to CT Seller** drops
  `photo_role=tape`. Tape stays in the Estate-Ez gallery for SKU reference. Lots that were
  already posted keep whatever photos are on CT Bids until someone edits that item there;
  do not Resend all. Posting is dashboard-only — there is no browser extension.
- **Find groups is public groups only (Facebook tab).** `POST /lots/{id}/audience-hunt` and
  `POST /sales/{id}/audience-hunt` use Gemini Pro + Google Search (`tools=[GoogleSearch()]`),
  then a separate structured JSON call (schema + search tools 400 together). That is how real
  Facebook group names/URLs are found — not Meta's Graph API (which cannot search Groups).
  Results are stored on the lot; `GET /sales/{id}/facebook-groups` aggregates them and adds
  facebook.com/search/groups fallback links. Primary target is **national / US-wide collector
  and hobby groups** (items ship nationwide). Local sale-city groups are a secondary extra,
  never a filter. It does not scrape members, harvest emails, or auto-post. **United States
  groups only** (drop UK/Canada/EU/worldwide clubs). Everyday household lots skip the model unless
  `force=true`. `audience_hold` makes single/bulk CT Seller post skip the lot until staff un-holds.
  Hunt copies `GOOGLE_API_KEY` from app settings into `os.environ` (same as catalog jobs) because
  `pipeline._get_client` does not read `.env` itself.
  UI: Facebook tab **Find groups** (sale-wide sequential hunt). Lot page and dashboard link there.
  Sale-wide progress is `GET /sales/{id}/audience-hunt` (in-memory in the API process; poll ~1.5s).
  Lot rows include `audience_hunting` + `audience_hunt_phase` (`queued` / `searching` / `structuring`).
  Each hunt/skip writes `audience_hunt_events` (metered Pro tokens). Admin analytics
  (`GET /admin/analytics`) includes hunt counts and hunt Pro $ in the 30-day totals.

### Testing / lint / build

- Backend tests: `cd backend && ../backend/.venv/bin/python -m pytest`.
  Current baseline (Oct 2026): full suite passes in this environment
  (`383 passed, 5 warnings`).
- **No linter is configured** anywhere (no ruff/flake8/eslint/pre-commit config, no lint
  script in any `package.json`). There is nothing to run for "lint" unless one is added.
- Frontend has only `dev` / `build` / `start` scripts (no tests/lint). Use `npm run dev`
  for development; `npm run build` for a production build check.
