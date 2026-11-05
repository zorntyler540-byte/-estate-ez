# Deploy so anyone can test (first internet version)

This app is **two pieces** plus a database:

| Piece | Role |
|--------|------|
| **API** (`infra/docker/Dockerfile.api`) | FastAPI on port `8000` (or host `PORT`) |
| **Web** (`infra/docker/Dockerfile.web`) | Next.js on port `3000` (or host `PORT`) |
| **Postgres** | Managed database (do not use SQLite for production) |

Docker images are built from the **repository root** (so paths like `COPY backend ./backend` work).

### First-time order (avoids CORS chicken-and-egg)

1. Push this repo to GitHub.  
2. Create **Postgres**, then deploy the **API** (`Dockerfile.api`) with **`ENVIRONMENT=production`**, a 32+ character `SECRET_KEY`, Postgres `DATABASE_URL`, and `CORS_ORIGINS` set to the web URL you are about to create (planned hostname is fine). Do not run a public API in `development`.  
3. Deploy the **Web** service (`Dockerfile.web`) with `NEXT_PUBLIC_API_BASE` = your public API URL; note the **web** `https://…` URL.  
4. If the web URL differs from the `CORS_ORIGINS` you planned, update it on the **API** and redeploy.

---

## 1. Push code to GitHub

Create a repo, commit, and push this project (no secrets in git — use host UI for env vars).

---

## 2. Managed Postgres

Create a **Postgres** instance (Railway “Postgres” plugin, Neon, Supabase, Render Postgres, etc.).

Copy its **`DATABASE_URL`**. The API accepts `postgres://` and normalizes it for SQLAlchemy + psycopg3.

---

## 3. Deploy the API (example: Railway)

1. **New project** → **Empty project** → **GitHub repo** → pick this repo.
2. **New service** → **Dockerfile** (or “Docker” deploy).
3. Set **Dockerfile path** to: `infra/docker/Dockerfile.api`
4. **Root directory** stays the repo root (default).
5. **Variables** (minimum):

| Variable | Notes |
|-----------|--------|
| `DATABASE_URL` | From Postgres (Railway can link the plugin and inject this). |
| `SECRET_KEY` | Long random string (e.g. `openssl rand -hex 32`). **Required in every non-test environment** — missing, `change-me`, `change-me-dev-secret`, or under 32 characters refuses to start. |
| `ENVIRONMENT` | Set to **`production`** on the public API **and** the catalog worker. Never leave a public service on `development`. |
| `GOOGLE_API_KEY` | Gemini / Google AI key. |
| `CORS_ORIGINS` | Your **public web URL** only, e.g. `https://your-web-service.up.railway.app` — **no trailing slash**. Comma-separate if you have several. |
| `TRUSTED_HOSTS` | Optional but recommended: comma-separated **Host** header values for the API (e.g. `your-api.up.railway.app`). Enables `TrustedHostMiddleware`. |
| `ALLOW_OPEN_REGISTRATION` | **Closed by default in production.** Set `true` only to bootstrap the first real account, then set `false` (or unset) and redeploy. |
| `MASTER_ADMIN_EMAILS` | Optional. Comma-separated emails of **existing** users who may manage every tenant. Register/invite of those addresses is blocked until the user already exists. |
| `MASTER_ADMIN_USER_IDS` | Optional. Comma-separated user ids that are master admin even if their email is not listed. Prefer this once you know the user id. |
| `CATALOG_MAX_QUEUED_JOBS_PER_ACCOUNT` | Optional (default `25`). Caps receiving+queued catalog jobs per account. Per-lot jobs the server splits out of a batch do not count. |
| `CATALOG_MAX_PHOTOS_PER_ACCOUNT_PER_DAY` | Optional (default `2000`). Caps photos cataloged or queued per account per UTC day (`GET /jobs/quota` shows usage and `resets_at`; the 429 carries `Retry-After`). |
| `CATALOG_MAX_PHOTOS_PER_JOB` | Optional (default `250`). Caps stills in one multi-lot batch job (checked up front from `expected_photos`). Single-lot jobs and each split lot stay at 40. |
| `CATALOG_BATCH_FANOUT` | Optional (default `true`). After triage a multi-lot batch queues one single-lot job per tape-SKU lot. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Optional (default 43200 = 30 days, max 129600 = 90 days). Lower for tighter security (e.g. `1440`). |
| `META_APP_ID` / `META_APP_SECRET` | Optional. Facebook Page posting. Leave unset to hide Connect/Post in the dashboard. See **§9 Facebook Page**. |
| `META_OAUTH_REDIRECT_URI` | API callback, e.g. `https://<your-api-domain>/integrations/facebook/oauth/callback`. Must match the Meta app exactly. |
| `PUBLIC_WEB_ORIGIN` | Dashboard origin the OAuth callback returns to (Account settings), e.g. `https://your-web.up.railway.app`. Falls back to the first `CORS_ORIGINS` value. |
| `META_INSTAGRAM_PUBLISHING` | Optional, default `false`. `true` on the API **and** worker to publish Push-calendar Instagram feed and Instagram Story slots (needs R2 and the Instagram permissions in **§9**). |
| `META_TOKEN_ENCRYPTION_KEY` | Optional. If unset, Page tokens are encrypted with a key derived from `SECRET_KEY`. |
| Optional | Copy other keys from `backend/.env.example` (SMTP, model flags, etc.). |

6. **Generate domain** for the API service → note the HTTPS URL, e.g. `https://caring-api.up.railway.app`.

7. Redeploy after changing env vars. On start, the container runs **`alembic upgrade head`** then **uvicorn** (Railpack uses the root `Procfile`; `Dockerfile.api` has the same `CMD`). If you set a custom Start Command in Railway, keep the `python -m alembic upgrade head &&` prefix. The API itself never runs DDL; it only logs `Schema check:` warnings when the database is behind. Migrations hold a Postgres advisory lock, so several API replicas starting together apply them once. The worker does not migrate.

**Health check:** open `https://<your-api-domain>/health` — should return JSON `{"status":"ok"}`.

---

## 4. Deploy the Web app (same platform or another)

1. **Second service** → **Dockerfile** deploy.
2. **Dockerfile path:** `infra/docker/Dockerfile.web`
3. **Build argument / variable** (must be present at **image build** time):

| Name | Value |
|------|--------|
| `NEXT_PUBLIC_API_BASE` | **Exact public API base**, e.g. `https://caring-api.up.railway.app` — no trailing slash. |
| `NEXT_PUBLIC_SALE_SUITE_DOWNLOAD_URL` | Optional. Direct HTTPS URL to `CTBids-Sale-Suite.zip` (OneDrive / R2 / S3). If unset, the **Desktop tools** page uses `/downloads/CTBids-Sale-Suite.zip` from the web image. |

**CTBids Sale Suite (Windows desktop app):** Public download page is **`/desktop`** (no sign-in). Logged-in users also see it under **Desktop tools**. Build the zip with `build_suite.bat` in the sibling Windows Sale Suite project. For a local test, copy `dist\CTBids-Sale-Suite.zip` into `frontend/public/downloads/`. For production, host the zip externally (OneDrive / R2 / S3) and set `NEXT_PUBLIC_SALE_SUITE_DOWNLOAD_URL` at **image build** time — the zip is ~700 MB and should not be committed to git.

On Railway: add `NEXT_PUBLIC_API_BASE` under **Variables**. Railway passes it to the Docker `ARG` during `docker build`. There is **no default**: the build **fails** if it is missing, not `https://`, or contains `localhost` / `127.0.0.1` (and `next build` runs the same check). A failed build leaves the previous deployment serving.

4. **Generate domain** for the web service → this is the link you send testers.

5. Set **`CORS_ORIGINS`** on the API to this web URL (and redeploy the API if you changed it).

---

## 5. Smoke test

1. Open the **web** URL on your phone (Wi‑Fi or cellular).
2. Register → log in → create sale → upload photos → run job → open a lot.

If the browser console shows **CORS** errors, `CORS_ORIGINS` on the API does not match the **origin** of the page (scheme + host + port exactly).

---

## 6. Uploads and redeploys

### Cloudflare R2 (recommended)

Set these on the **API** Railway service so catalog photos survive redeploys and skip Railway disk egress for durable storage:

| Variable | Example |
|----------|---------|
| `R2_ACCESS_KEY_ID` | from R2 Account API token |
| `R2_SECRET_ACCESS_KEY` | from R2 Account API token |
| `R2_BUCKET` | `estate-catalog` |
| `R2_ENDPOINT` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` (no bucket suffix) |
| `R2_ACCOUNT_ID` | optional, for your notes |

When configured, new catalog photos are uploaded to R2 under the same keys as before (`catalog/{account_id}/{photo_id}.ext`). The API keeps a local cache under `backend/data/uploads` for fast reads; if the cache is missing after a redeploy, it downloads from R2 on demand.

**Migrate existing photos (once):** after R2 vars are live, call while files still exist on the API disk:

`POST /integrations/r2/migrate-catalog` with body `{ "limit": 500 }` (auth as account owner). Re-run until `uploaded` is 0. Check the R2 bucket for `catalog/…` objects. (Ops endpoint only — not shown in the Account UI.)

`GET /health` reports `catalog_r2_configured: true` when the vars are present.

**Signed URLs (lower Railway egress):** the web app requests a short-lived URL (`GET /lots/{id}/media-url` or `…/photos/{photoId}/media-url`), then the browser loads the JPEG from R2. Cached thumbs are stored as `*.thumb.{px}.jpg` beside the original. Keep the bucket **private** — do not enable the public development URL.

**R2 CORS (for photo viewer/edit):** `<img src="signed-url">` works without CORS; `fetch(signed-url)` for crop/rotate needs it. Set once in Cloudflare → R2 → bucket → **Settings → CORS** (Object Read & Write tokens cannot call PutBucketCors). Use your web origin from `CORS_ORIGINS`, plus `http://localhost:3000` / `http://127.0.0.1:3000` if you develop locally. Example:

```json
[
  {
    "AllowedOrigins": ["https://YOUR-WEB.up.railway.app", "http://localhost:3000", "http://127.0.0.1:3000"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
    "MaxAgeSeconds": 3600
  }
]
```

### Local disk / volume (fallback)

Without R2, images are stored only under **`backend/data/uploads`** inside the API container. Many hosts **wipe the filesystem** on redeploy unless you attach a **persistent volume** mounted at `/app/backend/data`.

For a first pilot without R2: avoid redeploying the API unnecessarily, or add a volume in your host’s UI.

**Multiple API replicas:** Without R2 (or a shared volume), uploads can land on instance A while thumbnails hit instance B. Prefer **one replica**, or use R2.

### Background catalog worker (recommended in production)

By default the **API** runs Gemini catalog jobs in background threads inside the same process. Under load that makes the dashboard feel laggy. Prefer a **second Railway service** that only runs the worker:

1. New service → Dockerfile **`infra/docker/Dockerfile.worker`** (no public domain / no `PORT` needed).
2. Copy the same variables as the API (`DATABASE_URL`, `GOOGLE_API_KEY`, R2_*, `SECRET_KEY`, …).
3. On the **API** only, set **`RUN_CATALOG_JOBS_IN_PROCESS=false`** and redeploy the API.
4. Leave that variable unset/true on the worker (the Dockerfile defaults it to true).
5. Optional worker knobs (recommended before onboarding several franchises):
   - `WORKER_MAX_CONCURRENT_JOBS=3` — one replica runs up to 3 isolated child processes
   - `CATALOG_JOB_MAX_CONCURRENT=1` — per-account cap for single-lot uploads (fair round-robin across franchises)
   - `CATALOG_BULK_MAX_CONCURRENT=2` — separate per-account lane for website batches and their per-lot jobs, so phone lots never wait behind a big folder
   - `GEMINI_GLOBAL_CONCURRENCY=8` — cross-process Gemini HTTP cap (advisory locks)
   - `WORKER_CHILD_CRASH_RETRIES=3` — isolated-child OOM/crash cap; then the job is failed and listed by `/jobs/stuck`
   - `WORKER_CHILD_RSS_MB=3000` — parent SIGKILLs a runaway child (do **not** use 1200; a 3-photo lot already exceeded 1.2 GB)
   - `WORKER_CHILD_EXIT_SECONDS=15` — after the job row is saved, the child must `os._exit` even if cleanup hangs
   - `WORKER_CHILD_DONE_KILL_SECONDS=30` — parent SIGKILLs a child still alive after the job is completed/failed
   - `WORKER_CHILD_AS_MB=3000` — child `RLIMIT_AS` in MB, sized with the RSS cap (0 disables)
   - `WORKER_CHILD_EXIT_SECONDS=15` — after the job row is saved, the child must `os._exit` even if cleanup hangs
   - `WORKER_CHILD_DONE_KILL_SECONDS=30` — parent SIGKILLs a child still alive after the job is completed/failed
   - `WORKER_POLL_SECONDS=2`
6. **Memory limits (required for cost):** Railway bills RAM while the replica is sized large. Cataloging used to spike to 20–32 GB. Cap services so one location does not cost ~$10/GB-month:

| Service | Memory limit | Notes |
|---------|----------------|-------|
| Web | 1 GB | Next.js |
| API | 2 GB | Set `RUN_CATALOG_JOBS_IN_PROCESS=false` so Gemini is not on this box |
| Worker | 4 GB | Isolated children exit after each job; idle parent should stay under 300 MB. Peak child RSS on a 3-photo lot exceeded 1.2 GB. RSS cap 3000 MB + 1280px working images. |
| Postgres | host default | Do not bump this for cataloging |

Do **not** leave API or worker at 24–32 GB “just in case.” If the 4 GB worker OOMs, keep `PIPELINE_TRIAGE_PARALLEL=2` and `PIPELINE_WORKING_MAX_EDGE=1280` (defaults). `WORKER_ISOLATE_JOBS=true` is the default. A child that still dies is re-queued with a `[catalog-crash N/3]` marker; after `WORKER_CHILD_CRASH_RETRIES` it is marked failed (`Worker child crashed N times`) and `/jobs/stuck` surfaces it. Retries skip photos already on lots in that sale and reuse unlinked leftover Photo rows so Gemini is not billed twice and the daily photo cap is not double-counted.

**Multiple worker replicas:** 2 replicas is enough for ~5 franchises (`WORKER_MAX_CONCURRENT_JOBS=3` each). Claiming uses `FOR UPDATE SKIP LOCKED` plus the existing per-job advisory lock. Gemini RPM/concurrency is shared via Postgres so two replicas cannot double the API-key budget. Do not run 3+ replicas until you raise `GEMINI_GLOBAL_CONCURRENCY` / Flash+Pro RPM env vars — they default to ~50% of current pre-Vertex paid limits.

**Railway memory graphs sum replicas by default.** The Metrics tab **Sum** view adds every replica (two idle workers at 100 MB each show as 200 MB). Limits apply per replica — switch to **Replica** view before raising the 4 GB cap. Idle parent RSS should stay under 300 MB; leftover children show up as `tree_rss_mb` on the periodic worker memory log line.

Local check: `cd backend && RUN_CATALOG_JOBS_IN_PROCESS=true python -m app.worker`

Jobs stay `queued` until the worker claims them (usually within a couple of seconds). Do **not** run migrations on the worker — only the API image runs Alembic.

New uploads use keys **`catalog/{account_id}/{photo_id}.jpg`**. Deleting photos in the app removes the DB row and the R2/local object for that photo.

---

## 6b. Backups (photos + database)

Postgres backups and photo storage protect **different things**:

| What | Where | Action |
|------|--------|--------|
| Titles, SKUs, photo rows, paths | **Postgres** | Postgres service → **Backups** |
| JPEG files | **R2** (if configured) or **API volume** | R2 is durable by default; optional volume still helps local cache |

**Recommended:**

1. Configure **R2** (§6) on the API service.
2. Enable **Postgres** automated backups.
3. Periodically download a **sale photo ZIP** from the dashboard after major catalog days.
4. Keep a copy of original camera/upload folders on your PC until the sale is published.

**After a mistake:** Restoring **only** Postgres brings back listing text but not JPEGs if objects were deleted from R2. Re-upload photos or restore objects if you have a backup copy.

---

## 7. Updating after launch

Same as local: edit code → commit → push → the host rebuilds/redeploys. Run new **Alembic** migrations when you add them (`upgrade head` already runs on API container start).

---

## 8. Security checklist (production API)

- **`ENVIRONMENT=production`** — Required on the public API and worker. Refuses weak `SECRET_KEY`, SQLite URLs, and missing `CORS_ORIGINS` at startup. Weak `SECRET_KEY` is also refused in development.
- **Secrets only in the host dashboard** — Never commit `.env` (see `.gitignore`).
- **Keep open signup off** — Production registration is closed unless you explicitly set `ALLOW_OPEN_REGISTRATION=true` to bootstrap.
- **`TRUSTED_HOSTS`** — Set to your API hostname(s) to reduce Host-header attacks.
- **Repo hygiene** — `Images/`, `*.db`, and `backend/data/` are gitignored so estate photos and databases do not land on GitHub.
- **JWT lifetime** — Default is 30 days (`43200` minutes). Tune `ACCESS_TOKEN_EXPIRE_MINUTES` on Railway if you want shorter or longer sessions (max 90 days).

---

## Other hosts

- **Fly.io**, **Render**, **Google Cloud Run**, **a single VPS + Docker Compose** — same two images and the same env ideas; only the clicking differs.

If you tell me which host you picked, we can narrow this to exact clicks for that product only.

---

## Troubleshooting

### `Attribute "app" not found in module "main"`

The repo has a **legacy** `main.py` at the **root** (old CLI photo scanner). It is **not** the FastAPI app. Railpack/Nixpacks often defaults to `uvicorn main:app`, which hits that file and crashes.

**Fix (pick one):**

1. **Preferred:** In the service **Settings**, set the builder to **Dockerfile** and **Dockerfile path** `infra/docker/Dockerfile.api`.  
2. **Or** use automatic build: a **`Procfile`** at the repo root tells Railway to run `uvicorn app.main:app` from the `backend/` folder (this repo includes one). Redeploy after pulling latest `main`.

### Deploy still shows `python@3.x` instead of Docker

You are not using the Docker image. Switch the service to the **Dockerfile** builder and set `infra/docker/Dockerfile.api` (see §3).

---

## 9. Facebook Page posting (optional)

Estate-EZ can post **multi-photo feed posts to a Facebook Page** the account owner connects (per franchise). v1 is aimed at the owner’s own Page. Other franchises can connect later; that will need Meta **App Review** and business verification (out of scope here).

Push-calendar **Instagram** slots publish to the Instagram professional account linked to the Page, and **Stories** slots publish a Page photo story plus an Instagram story (see **Instagram and Stories** below). TikTok slots are copy only. Groups and Marketplace are **not** implemented. There is no Groups posting API.

### Create the Meta app (no App Review for the owner’s Page)

1. Go to [developers.facebook.com](https://developers.facebook.com/) → **My Apps** → **Create App**.
2. Choose type **Business**.
3. Add the **Facebook Login** product (Facebook Login for Business is fine).
4. **Facebook Login → Settings → Valid OAuth Redirect URIs**: add the API callback  
   `https://<your-api-domain>/integrations/facebook/oauth/callback`  
   (local: `http://localhost:8000/integrations/facebook/oauth/callback`).
5. App Dashboard → **App settings → Basic**: copy **App ID** and **App Secret**.
6. Add the Page owner (Tyler’s aunt) as an **Admin** or **Developer** on the app (App roles).  
   She must also be an admin of the Facebook Page. Tokens for Pages owned/admin’d by app roles work **without App Review**.
7. Permissions requested: `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`.
8. Set the Railway **API** variables from the table in §3 (`META_APP_ID`, `META_APP_SECRET`, `META_OAUTH_REDIRECT_URI`, `PUBLIC_WEB_ORIGIN`). Redeploy the API.
9. In Estate-EZ: **Account → Facebook Page → Connect Facebook Page**. Pick the Page if she admins more than one.

Graph API version is pinned in code (`GRAPH_API_VERSION` in `backend/app/services/meta_graph.py`, currently `v26.0`). Bump that constant when Meta sunsets the version.

**If Connect is missing:** `META_APP_ID` is unset — the UI hides the feature on purpose.

**If posting says reconnect:** Graph returned a real token error (revoked/invalid Page token). Connect again from Account settings. A still-valid Page token does not show this warning just because the user token’s 60-day expiry passed, or because a Page has not been picked yet.

Owners/admins post; cataloggers do not. Posts go through a durable publish queue in Postgres (migration `0053_social_publish_queue`): the API only enqueues, and the **catalog worker** publishes in a `python -m app.worker --social-publish` child within a few seconds. Queued posts survive API and worker restarts, transient Meta errors retry up to 3 times, and a request that times out after Meta may have posted is never retried (the slot says to check the Page). **Deploy the API and worker together** for this change, and keep the worker running — with `RUN_CATALOG_JOBS_IN_PROCESS=false` on the API, nothing posts while the worker is down (the dashboard says it is waiting for the publisher).

The Push tab **Social calendar** is computed from the latest Watcher snapshot (not a stored slot table). Each spreadsheet slot goes to its own platform after an owner approves it (optional per-account auto-approve, off by default): 9:00 Facebook and the closing-day 17:40 countdown → scheduled Page feed post; 12:15 Instagram → Instagram feed; 16:30 Stories → Page story + Instagram story; 18:00 TikTok → copy only (never posted). Instagram and Stories cannot be scheduled through Meta, so approving reserves the slot and Estate-EZ publishes it at slot time (skipped if the publisher is more than 2 hours late). Captions and photos vary by slot. Missed slots are skipped. Once a slot is scheduled or posted it is pinned: later snapshots keep it on the same day and time and never delete, move, or edit the Graph post. If its lot leaves the sale, the slot is flagged for the owner, who can Unapprove it until 2 minutes before it goes live.

### Instagram and Stories

1. Set `META_INSTAGRAM_PUBLISHING=true` on the **API and worker**. While it is off, Instagram slots are copy only and Stories slots post to the Page only. Connect does not ask for Instagram permissions.
2. R2 (`R2_*`) must be configured on the **API and worker**. Instagram fetches images by URL, so Estate-EZ uploads a copy to `social-media/{account}/{post}/…` in the bucket and passes Meta a 2-hour presigned URL. The copies are deleted after the post finishes.
3. Link an **Instagram professional (Business or Creator) account** to the Facebook Page (Meta Business Suite → Settings → Instagram accounts).
4. Meta app: add the **Instagram Graph API** product. Connect then also requests `instagram_basic` and `instagram_content_publish`. For the owner's own Page with app roles no App Review is needed; any other franchise needs **App Review for `instagram_content_publish` and `instagram_basic`** (plus `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, which Page stories also use).
5. After step 1 is deployed, the owner must **Reconnect Facebook** (Account settings) to grant the new scopes. Then use **Check Instagram link** on Account settings or the Push tab.

Instagram feed posts are a single image or a carousel of up to 10 photos (center-cropped into Instagram's 4:5–1.91:1 range) with the caption cut to 2,200 characters. Stories use the first listing photo.

Slot times use the franchise time zone (Push tab → **Calendar time zone**; default Pacific). Run migration `0052_account_timezone` before this API deploys. Calendar auto-approve is swept by the catalog worker (`--social-sweep` child), so deploy the API and worker together.
