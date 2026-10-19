# Estate-EZ security audit report

**Profile:** `standard` (full six-phase Cloudflare security-audit skill).
**Scope:** repository `zorntyler540-byte/-caringtransitions-ai` at commit `c661a8fd15d52afa473d81574ec28308f7ae9fb0` (`backend/`, `frontend/`, `mobile/`, `infra/`, `main.py`, `listings/`, `Procfile`, `DEPLOY.md`, `README_SAAS.md`, `.github/`, `alembic.ini`).
**Budget:** unset (no agent-count cap).
**Execution:** sandboxed source-and-local-only. No OS-enforced sandbox was available on this VM, so **no target code was executed** and **no production, Railway, Expo, Cloudflare, Meta, Gemini, or CT Seller host was contacted**.
**Prior runs:** none. No compatible prior `coverage-ledger.json` or `findings.json`. No carried same-source confirmations and no changed-source revalidations.
**Deferred / out of scope:** none. The final-clean coverage critic returned `stop` with no missing units and no reassignments.
**Run status:** `complete`.

This is a source-review pass, not a confirmed-vulnerability scorecard. A `needs_validation` lead is a real source path whose decisive result was blocked (sandbox or a deployment fact). It is not a confirmed bug and has no severity.

## Security posture

Estate-EZ is a multi-tenant estate-sale catalog: FastAPI + optional worker, Next.js dashboard, Expo field app. The source already binds most staff JWTs to `account_id`, restricts the `client` role with an HTTP allowlist, keeps Facebook **Approve / Post now** on an owner + `confirm` gate, keeps audience hunt owner-only, and mints R2 media URLs only after a tenant-scoped lot lookup. A scan of all 748 commits found **no committed production API secrets** (`AIza…`, `AKIA…`, `EAAG…`, `sk-…`, `ghp_…` all 0 hits). Values that matter live in Railway / Cloudflare / Expo dashboards, not in git.

The remaining risk is fail-open defaults and a few missing last-trusted-point checks: open registration, email-only master admin, an un-chrooted staff `input_folder`, catalogger-triggered Facebook auto-approve, and uncapped Gemini batch spend on the shared operator key. Those are the leads to settle first. None were promoted to `confirmed` because this run could not observe a dummy-tenant result.

## Confirmed findings

| Severity | Title | Boundary | Observed result |
|---|---|---|---|
| — | *None* | — | No record met the confirmed bar (source path plus sandboxed observed result). |

There are no confirmed findings to expand.

## Needs validation

Do not treat the table as a ranked CVE list. Priority is what the owner should check on Railway / Cloudflare / Meta first. Full traces are in `NEEDS-VALIDATION.md`.

| Priority | Title | Source trace (entry → sink) | Exact blocker | Local next step | Owner-observed check |
|---|---|---|---|---|---|
| 1 | Master-admin email can be claimed by open registration or team invite | `backend/app/main.py:866` → `:1242` | No sandbox; whether listed emails already exist is env-only | Dummy register/invite of an unused `MASTER_ADMIN_EMAILS` address, then `GET /admin/accounts` | Read Railway `MASTER_ADMIN_EMAILS` and `ALLOW_OPEN_REGISTRATION`; confirm each listed address already exists as `users.email`. Do not register those addresses live. |
| 2 | Open registration defaults on and mints owner JWTs | `backend/app/main.py:866` → `:893` | No sandbox; live flag not observed | Dummy `POST /auth/register` under default vs production+flag-false | Read Railway `ALLOW_OPEN_REGISTRATION` and `ENVIRONMENT` only. Do not register on production. |
| 3 | `POST /jobs` stores an un-chrooted `input_folder` | `backend/app/main.py:5506` → `processing.py:882` | No sandbox; live worker disk / R2 prefix isolation not observed | Two dummy accounts; point A's job at B's catalog or batch folder with Gemini/R2 mocked | Inspect a worker `uploads/catalog` layout and whether R2 list is scoped by `ProcessingJob.account_id`. Do not probe a customer tenant. |
| 4 | Catalogger snapshot ingest publishes when `calendar_auto_approve` is on | `engagement_routes.py:28` → `facebook_page.py:747` | No sandbox; live watcher role and flag not observed | Catalogger `POST /integrations/engagement/snapshots` with Graph mocked and the flag on | Confirm the live watcher role and whether auto-approve is enabled. |
| 5 | Client/catalogger can stack uncapped batch catalog jobs on the shared Gemini key | `backend/app/main.py:5362` → `processing.py:567` | No sandbox; live Gemini/proxy caps not observed | Mocked upload of 41 photos in batch mode, then `/run` | Check Gemini project daily cap and any ingress body limit. Do not flood production. |
| 6 | Default JWT `SECRET_KEY` stays accepted when `ENVIRONMENT` is not production | `DEPLOY.md:16` → `auth.py:58` | No sandbox; live Railway values not observed | Start API with unset `SECRET_KEY` in development; mint dummy HS256 | Read Railway `ENVIRONMENT` and `SECRET_KEY` length/placeholder only. Do not send traffic to the hostname. |
| 7 | Unauthenticated Facebook OAuth callback writes Page tokens from HS256 state alone | `facebook_routes.py:114` → `facebook_page.py:211` | No sandbox; Meta redirect allowlist not in source | Mocked callback with account-A state and no Bearer | Compare `META_OAUTH_REDIRECT_URI` to the Meta app allowlist. Confirm `PUBLIC_WEB_ORIGIN`. |
| 8 | Staff JWT placed in EventSource query string | `frontend/app/dashboard/page.tsx:3159` → `auth.py:105` | No sandbox; live access-log retention not observed | Dummy uvicorn access log of `/jobs/{id}/events?access_token=` | Inspect existing Railway/API logs for that query name. Do not replay tokens. |
| 9 | Catalogger JWT can invoke leftover owner-documented R2 migrate, ping, and CORS-read | `backend/app/main.py:1491` → `object_storage.py:167` | No sandbox; must not write the live bucket | Mocked catalogger vs owner vs client on `/integrations/r2/*` | Confirm those leftover routes are still mounted. Do not invoke ping on production (it writes `_health/r2-ping.txt`). |
| 10 | Catalog privacy depends on an unencoded private-bucket / r2.dev-off policy | `catalog_storage.py:52` → `object_storage.py:86` | Cloudflare ACL / public-URL toggle not in repo | Reconfirm puts omit ACL and no `r2.dev` URL is built | In Cloudflare R2 Settings only: Public development URL off, no public custom domain. Do not GET catalog keys from the internet. |
| 11 | Watcher `item_url` is copied unsanitized into Facebook captions | `engagement_routes.py:29` → `facebook_caption.py:214` | No sandbox | Dummy snapshot `item_url=https://evil.example/...` into `template_caption` | Confirm whether calendar auto-approve is on (then this also rides the publish path). |
| 12 | Facebook and CT Seller sealing fall open to `SECRET_KEY` XOR if Fernet import fails | `facebook_page.py:211` → `facebook_crypto.py:28` | No sandbox; live `import cryptography` not observed | Monkeypatch `ImportError`; assert `xor:` prefix on dummy strings | In API and worker images, `import cryptography` and check token columns do not start with `xor:`. |
| 13 | Password change and team reset do not invalidate access JWTs | `backend/app/main.py:929` → `auth.py:56` | No sandbox; extra gateway denylist not in source | Dummy JWT A after `PATCH /auth/password` (source predicts 200) | Confirm no API-gateway JWT denylist. Do not test with live staff tokens. |
| 14 | Remembered staff password stored in backup-eligible SecureStore | `mobile/context/AuthContext.tsx:110` → `secureStorage.ts:13` | No sandbox; shipped IPA/APK backup XML is a packaging fact | Dummy device backup restore of `ct_mobile_login_credentials` | Inspect EAS APK/IPA `allowBackup` and iOS keychain accessibility. Do not export live passwords. |
| 15 | Catalog multipart ingest buffers entire parts with no photo byte cap | `backend/app/main.py:5362` → `upload_storage.py:90` | No sandbox; Railway body limit not in source | Oversize dummy part under process memory limits | Record whether Railway/ingress caps multipart before uvicorn allocates. Do not flood production. |
| 16 | FastAPI `/docs`, `/redoc`, `/openapi.json` remain enabled on the public bind | `backend/app/main.py:284` → `Dockerfile.api:22` | Live edge path filter not in repo | Isolated loopback GET of those routes | From the Railway dashboard, note whether an edge rule denies `/docs` `/redoc` `/openapi.json`. Do not fetch the public hostname from this audit. |

## Hardening notes (not findings)

These are defense-in-depth notes from hunters. They did not independently meet the candidate gate.

- Bind master admin to enrolled user ids (or reject register/invite of `MASTER_ADMIN_EMAILS`) instead of first-claimer email membership.
- Reuse `_queued_job_upload_folder` inside `create_job`, and compare `parse_batch_folder`'s path `account_id` to `ProcessingJob.account_id` before R2 list/get.
- `production_must_be_safe` does not require `TRUSTED_HOSTS`, `ALLOW_OPEN_REGISTRATION=false`, docs disabled, or `RUN_CATALOG_JOBS_IN_PROCESS=false`.
- Keep JWTs out of EventSource query strings (short-lived SSE ticket or cookie).
- Treat `POST /integrations/ctseller/session` access+refresh JWTs as owner-equivalent on seller.ctbids.com; if Watcher should be owner-only, gate with `can_manage_integrations`.
- Call `_require_owner` on leftover `/integrations/r2/migrate-catalog`, `/ping`, and `GET /cors`.
- `cors_allow_origins()` always includes `http://localhost:3000` and `http://127.0.0.1:3000`, including production, and `CORSMiddleware` sets `allow_credentials=True`. Unused today (no cookies; browser fetch omits credentials) but should be dropped in production.
- Pin Docker base digests and a Python lockfile; bind EAS production channel publishes to a git SHA in CI. Rejected as findings (no lower-trust writer) but still good release hygiene.
- `graph_request` accepts a full URL if `path` starts with `http`; current callers only pass `/me`-style paths. Reject unused absolute URLs.
- After a lot/photo/video `account_id` lookup, refuse to sign or read `storage_path` unless the object key is under `catalog/{account_id}/` (or `videos/`).

## Positive source patterns

- Access JWTs pin `algorithms=["HS256"]`, require `sub`, and load `User` by `id` + `account_id`. Inactive accounts are 403. OAuth state JWTs lack `sub` and require `purpose=meta_oauth`, so they are not usable as staff tokens.
- `enforce_client_http` plus `client_http_allowed` keeps the restricted client role off admin, Facebook, R2, team, and hunt routes.
- HTTP Facebook preview/publish and dashboard **Approve / Post now** call `require_facebook_poster` and refuse `confirm=false`.
- Audience hunt is owner-only and splits Pro+Search from the structured JSON call (schema + search tools 400 together).
- `GET /lots/{id}/media-url` loads the lot with `Lot.account_id == current.account_id` before minting a time-capped GetObject presign.
- R2 `put_bucket_cors` drops origins containing `*`.
- Facebook Page and CT Seller secrets are Fernet-sealed in the default image path (`cryptography` is in `requirements.txt`).
- There is no inbound webhook receiver, so there is no missing webhook-signature check on that surface.
- Git history contains no production Gemini, AWS, Facebook, OpenAI, or GitHub token literals.

## Coverage summary

| Ledger status | Count |
|---|---|
| Total units | 32 (28 wave 1 + 4 wave 2) |
| `covered` | 18 |
| `candidate` | 14 (linked to the 16 `needs_validation` fingerprints; two units carry two fingerprints) |
| `blocked` | 0 |
| `deferred` | 0 |
| `out_of_scope` | 0 |

**Final-clean critic:** `stop` (no missing units, no reassignments). Post-wave-2 critic also stopped.

**Rejected fingerprints** (not findings; kept so a later run does not re-open the same claim without new evidence): `supply.docker.unpinned-pip-floating-base-rembg`, `supply.eas.production-ota-unbound-from-git`, `gemini.pro-search.missing-owner-gate`, `gemini.hunt.unlabeled-listing-search-args`, `unauth.health.upload-root-disclosure`, `unauth.auth.register-master-admin-email` (duplicate of the master-admin lead), `unauth.fastapi.default-docs` (duplicate of the OpenAPI-docs lead).

**Agents spent (approximate):** 4 reconnaissance + 8 wave-1 hunters + 4 wave-2 hunters + 2 critics + 21 Phase-3 verifiers + 16 Phase-5 verifiers. No budget was set.

One standard-profile pass does not exhaust the target. A later run should re-open the 14 `candidate` units after a sandboxed local fixture exists, and should re-check any unit whose cited source changed.
