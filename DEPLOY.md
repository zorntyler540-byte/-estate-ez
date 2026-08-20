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
2. Create **Postgres**, then deploy the **API** (`Dockerfile.api`) with `ENVIRONMENT=development` first so you do not need `CORS_ORIGINS` until the web URL exists.  
3. Deploy the **Web** service (`Dockerfile.web`) with `NEXT_PUBLIC_API_BASE` = your public API URL; note the **web** `https://…` URL.  
4. On the **API**, set `CORS_ORIGINS` to that web URL (no trailing slash), redeploy; then set `ENVIRONMENT=production` and redeploy again when ready for strict checks.

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
| `SECRET_KEY` | Long random string (e.g. `openssl rand -hex 32`). **Required length ≥32** when `ENVIRONMENT=production`. |
| `ENVIRONMENT` | Set to **`production`** on the public API so unsafe defaults are rejected and responses are hardened. |
| `GOOGLE_API_KEY` | Gemini / Google AI key. |
| `CORS_ORIGINS` | Your **public web URL** only, e.g. `https://your-web-service.up.railway.app` — **no trailing slash**. Comma-separate if you have several. |
| `TRUSTED_HOSTS` | Optional but recommended: comma-separated **Host** header values for the API (e.g. `your-api.up.railway.app`). Enables `TrustedHostMiddleware`. |
| `ALLOW_OPEN_REGISTRATION` | Default `true`. After you create the first real account, set **`false`** and redeploy so strangers cannot register. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Optional (default 43200 = 30 days, max 129600 = 90 days). Lower for tighter security (e.g. `1440`). |
| Optional | Copy other keys from `backend/.env.example` (SMTP, model flags, etc.). |

6. **Generate domain** for the API service → note the HTTPS URL, e.g. `https://caring-api.up.railway.app`.

7. Redeploy after changing env vars. On start, the container runs **`alembic upgrade head`** then **uvicorn**.

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

**CTBids Sale Suite (Windows desktop app):** Public download page is **`/desktop`** (no sign-in). Logged-in users also see it under **Desktop tools**. Build the zip with `build_suite.bat` in the `coins ale` project. For a local test, copy `dist\CTBids-Sale-Suite.zip` into `frontend/public/downloads/`. For production, host the zip externally (OneDrive / R2 / S3) and set `NEXT_PUBLIC_SALE_SUITE_DOWNLOAD_URL` at **image build** time — the zip is ~700 MB and should not be committed to git.

On Railway: add `NEXT_PUBLIC_API_BASE` under **Variables**, then enable **“Available at build time”** so the Docker `ARG` receives it during `docker build`. The Dockerfile defaults to the live Railway API and **fails the build** if the value contains `localhost` / `127.0.0.1`.

4. **Generate domain** for the web service → this is the link you send testers.

5. Set **`CORS_ORIGINS`** on the API to this web URL (and redeploy the API if you changed it).

---

## 5. Smoke test

1. Open the **web** URL on your phone (Wi‑Fi or cellular).
2. Register → log in → create sale → upload photos → run job → open a lot.

If the browser console shows **CORS** errors, `CORS_ORIGINS` on the API does not match the **origin** of the page (scheme + host + port exactly).

---

## 6. Chrome extension (optional)

`extension/manifest.json` only allows `localhost` APIs today. For production, add a **host permission** for your public API origin, e.g. `https://caring-api.up.railway.app/*`, reload the extension in `chrome://extensions`, and set the API base in the popup to that URL.

---

## 7. Uploads and redeploys

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
5. Optional: `WORKER_POLL_SECONDS=2`, `CATALOG_JOB_MAX_CONCURRENT=1`.

Local check: `cd backend && RUN_CATALOG_JOBS_IN_PROCESS=true python -m app.worker`

Jobs stay `queued` until the worker claims them (usually within a couple of seconds). Do **not** run migrations on the worker — only the API image runs Alembic.

New uploads use keys **`catalog/{account_id}/{photo_id}.jpg`**. Deleting photos in the app removes the DB row and the R2/local object for that photo.

---

## 7b. Backups (photos + database)

Postgres backups and photo storage protect **different things**:

| What | Where | Action |
|------|--------|--------|
| Titles, SKUs, photo rows, paths | **Postgres** | Postgres service → **Backups** |
| JPEG files | **R2** (if configured) or **API volume** | R2 is durable by default; optional volume still helps local cache |

**Recommended:**

1. Configure **R2** (§7) on the API service.
2. Enable **Postgres** automated backups.
3. Periodically download a **sale photo ZIP** from the dashboard after major catalog days.
4. Keep a copy of original camera/upload folders on your PC until the sale is published.

**After a mistake:** Restoring **only** Postgres brings back listing text but not JPEGs if objects were deleted from R2. Re-upload photos or restore objects if you have a backup copy.

---

## 8. Updating after launch

Same as local: edit code → commit → push → the host rebuilds/redeploys. Run new **Alembic** migrations when you add them (`upgrade head` already runs on API container start).

---

## 9. Security checklist (production API)

- **`ENVIRONMENT=production`** — Refuses weak `SECRET_KEY`, SQLite URLs, and missing `CORS_ORIGINS` at startup.
- **Secrets only in the host dashboard** — Never commit `.env` (see `.gitignore`).
- **Turn off open signup** — Set `ALLOW_OPEN_REGISTRATION=false` after your pilot accounts exist.
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
