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
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Optional (default 1440 = 24h). Lower for tighter security (e.g. `480`). |
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

On Railway: add `NEXT_PUBLIC_API_BASE` under **Variables**, then enable **“Available at build time”** (or equivalent) so the Docker `ARG` receives it during `docker build`.

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

Images are stored under **`backend/data/uploads`** inside the API container. Many hosts **wipe the filesystem** on redeploy unless you attach a **persistent volume** mounted at `/app/backend/data` (or move to S3/R2 later).

For a first pilot: avoid redeploying the API unnecessarily, or add a volume in your host’s UI.

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
- **JWT lifetime** — Tune `ACCESS_TOKEN_EXPIRE_MINUTES` if you want shorter-lived sessions.

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
