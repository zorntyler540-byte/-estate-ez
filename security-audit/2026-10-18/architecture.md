# Estate-EZ architecture (source review)

Estate-EZ (repo `zorntyler540-byte/-caringtransitions-ai`, commit `c661a8fd15d52afa473d81574ec28308f7ae9fb0`) is a multi-tenant estate-sale cataloging SaaS: FastAPI API + catalog worker, Next.js dashboard, Expo field app. Operators are franchise staff (account **owner**, **catalogger**) and restricted **clients** who only upload into one assigned sale. A **master admin** is any JWT user whose email is in `MASTER_ADMIN_EMAILS`. Protected resources: estate/client names and addresses, lot photos and videos, Gemini billing, Facebook Page access tokens, CT Seller credentials and session JWTs, R2 object keys, and staff JWTs.

Comparable baseline visible in-repo: unofficial CT Seller REST client, Meta Graph Page posting, Gemini structured vision, Cloudflare R2 (S3). Those products assume a server-held secret and per-account tokens; they do not excuse a missing tenant or owner check.

## Stack and deploy

Python 3.12 FastAPI/SQLAlchemy (`backend/app/main.py`), optional `python -m app.worker`, Next.js 15 (`frontend/`), Expo 54 (`mobile/`). Production docs describe Railway API + web + Postgres + optional worker + R2 (`DEPLOY.md`). Dev default is SQLite. Offline tests: `cd backend && .venv/bin/python -m pytest` with SQLite `tmp_path` and mocked Graph/Gemini. This run has **no OS-enforced sandbox** and **no parent scratch-promotion procedure**, so hunters must not execute target code; behavioral claims stay `needs_validation`.

Deterministic entrypoints: `app.main:app` (`Procfile`, `infra/docker/Dockerfile.api`), `python -m app.worker` (`Dockerfile.worker`), Next standalone (`Dockerfile.web`). Host choice of Dockerfile vs Procfile, live `CORS_ORIGINS`, R2 bucket ACLs, Meta app settings, and injected secrets are not source-provable.

## Entry surfaces and sinks

- Unauthenticated HTTP: `GET /health` (upload-root path), `POST /auth/register|login`, `GET /integrations/facebook/oauth/callback` (signed `state` only). FastAPI default `/docs`/`/redoc`/`/openapi.json` are not disabled (`backend/app/main.py:284`).
- JWT HS256 (`backend/app/auth.py`): Bearer on most routes; SSE/uploads also accept `access_token` query. Role gates: `enforce_client_http`, `_require_owner`, `require_facebook_poster`, `is_master_admin_email`. Tenant filter is `current.account_id`.
- Multipart photo/video → disk/`job-batches/` R2 → Gemini pipeline (`processing.py`, `pipeline/`). `POST /jobs` stores `input_folder` without the upload-root chroot used by `upload-files`.
- Gemini sinks: catalog, regenerate, tape verify, Facebook captions, audience hunt (Pro + Search tool), optional appraiser. Untrusted inputs: photo bytes, SKU text, lot title/description, `scene_note`, env `TAPE_VERIFY_PROMPT_SUFFIX`.
- Outbound: Meta Graph (`graph_request` accepts a full URL if `path` starts with `http`), CT Seller REST, Gemini, SMTP. Engagement `item_url` is stored and copied into captions, not fetched.
- Facebook Page connect/preview/publish and calendar Approve / Post now are owner + `confirm` (`facebook_page.py`, `facebook_routes.py`). `calendar_auto_approve` can publish as a synthetic owner.
- CT Seller: owner stores encrypted password; `POST /integrations/ctseller/session` returns access+refresh JWTs to any authenticated account member.
- Media: authenticated `media-url` mints time-capped R2 presigns. Exports (JSON/CSV/PDF/ZIP) are account-scoped.
- Browser: JWT in `localStorage`; EventSource puts JWT in the query string. Mobile: SecureStore + same API.

## Trust boundaries (strongest source-visible control)

| Boundary | Strongest control in source |
|---|---|
| Anonymous → account | Register gated in production only if `ALLOW_OPEN_REGISTRATION=false`; login bcrypt |
| JWT → tenant data | `account_id` in token must match DB user; queries filter `account_id` |
| Client → catalog | `client_http_allowed` allowlist + assigned-sale checks |
| Catalogger → Facebook/team/hunt | Owner-only helpers; cataloggers may still post CT Seller and obtain CT session JWTs |
| Master admin | Email allowlist env (not a DB role) |
| Facebook token | Fernet seal; XOR fallback if `cryptography` missing |
| Gemini spend | Owner-only audience hunt; catalog jobs runnable by catalogger and client on their jobs |
| Worker | Host env + DB claim; no HTTP identity |

## Starting paths

`backend/app/main.py`, `auth.py`, `config.py`, `services/roles.py`, `facebook_routes.py`, `facebook_page.py`, `facebook_crypto.py`, `meta_graph.py`, `ctseller.py`, `processing.py`, `job_batch_storage.py`, `catalog_storage.py`, `object_storage.py`, `pipeline/pipeline.py`, `core/gemini.py`, `audience_hunt.py`, `engagement_routes.py`, `worker.py`, `frontend/lib/api.ts`, `frontend/app/dashboard/page.tsx`, `mobile/lib/config.ts`, `mobile/lib/queue/sync.ts`, `infra/docker/*`, `DEPLOY.md`.

## Prior runs

No compatible prior `coverage-ledger.json` or `findings.json`. All units `prior_status: none`. No same-source confirmed exclusions.

## Companions selected

Selected because reconnaissance found the matching boundary: `WEB-PROTOCOL-AND-AUTH.md` (JWT, OAuth callback, CSRF-less Bearer, host/CORS), `AI-AND-LLM.md` (photo/listing text → Gemini → listing/Facebook/hunt), `CLIENT-SIDE.md` (SPA token storage, credentialed CORS), `DATA-ISOLATION-AND-LIFECYCLE.md` (account_id, R2 keys, exports), `CLOUD-AND-DEPLOYMENT.md` (Railway/R2/env), `RESOURCE-EXHAUSTION-AND-AVAILABILITY.md` (Gemini/CT spend, uploads), `DESKTOP-MOBILE-AND-LOCAL-IPC.md` (Expo capture/sync), `SUPPLY-CHAIN-AND-RELEASE.md` (Docker/EAS; no in-repo CI), `PROTOCOLS-RPC-AND-MESSAGING.md` (SSE only; no webhook receiver). Excluded: `MEMORY-SAFETY-AND-BINARY.md` (no unsafe native/FFI surface in the SaaS path).
