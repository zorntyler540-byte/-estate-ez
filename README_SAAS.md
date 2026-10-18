# CaringTransitions SaaS MVP

This repository now contains:

- `main.py` legacy local workflow (desktop-style processing)
- `backend/` FastAPI multi-user API + Postgres schema + worker
- `frontend/` Next.js dashboard for login, jobs, lot review/edit
- `mobile/` Expo field capture app (native camera, offline queue)

## 1) Backend setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r ..\requirements.txt
copy .env.example .env
```

Run API:

```powershell
uvicorn app.main:app --reload --port 8000
```

Run worker:

```powershell
python -m app.worker
```

Apply migration (optional if using startup auto-create):

```powershell
alembic upgrade head
```

## 2) Frontend setup

```powershell
cd frontend
npm install
npm run dev
```

Set API base in shell if needed:

```powershell
$env:NEXT_PUBLIC_API_BASE="http://localhost:8000"
```

## 3) Mobile capture app (Expo)

```powershell
cd mobile
copy .env.example .env
# Set EXPO_PUBLIC_API_BASE to the same API as the web app
npm install
npx expo start
```

See `mobile/README.md` for EAS iOS builds and offline queue behavior.

## 4) Main API endpoints

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `GET /lots`
- `PATCH /lots/{lot_id}`
- `POST /jobs` (queue processing by folder path)
- `POST /jobs/{job_id}/run` (run queued job)
- `GET /exports/markdown`
- `GET /exports/json`
- `GET /exports/csv`

## 5) Facebook Page posting (optional)

Leave `META_APP_ID` empty to hide the feature. To enable it:

1. Create a **Business** Meta app, add **Facebook Login**, and set the Valid OAuth Redirect URI to  
   `META_OAUTH_REDIRECT_URI` (local default `http://localhost:8000/integrations/facebook/oauth/callback`).
2. Add the Page owner as an **app Admin/Developer**. They must also admin the Facebook Page. That keeps v1 off App Review.
3. Set `META_APP_ID`, `META_APP_SECRET`, `META_OAUTH_REDIRECT_URI`, and `PUBLIC_WEB_ORIGIN` (see `backend/.env.example` and `DEPLOY.md` §9).
4. Dashboard: **Account → Facebook Page → Connect**, then an **account owner** posts from a lot, selected dashboard lots, Push-queue lots, or **approved Push-calendar slots** (every spreadsheet time, currently as Facebook Page feed posts). Captions are drafted with Gemini; **nothing posts until they click Post/Schedule on Page** (auto-approve is off by default). Cataloggers cannot post. Multi-lot batches run in the background.

Not in v1: native Instagram / Stories / TikTok / Groups / Marketplace publishers. Calendar rows for those platforms still schedule as Page posts until a native publisher is added.

## Notes

- For production, use object storage + actual upload endpoints (instead of raw server folder path).
- Tenant isolation is enforced by account-scoped JWT claims and account filters on queries.
