# Needs validation

These are source-grounded leads. They are **not** confirmed vulnerabilities and have **no severity**.

This run had no OS-enforced sandbox, so no target code was executed and no production host was contacted.

Priority is owner-operational (what to check first), not a severity ranking.

## 1. Master-admin email can be claimed by open registration or team invite

**Fingerprint:** `master-admin-email-claim-via-register-or-invite`

**Claimed root cause:** Master-admin authorization is a case-folded email membership test on the current User row, while self-service register and owner team-invite can mint that email without an enrollment or possession check.

**Description:** All-tenant admin is not a stored role. Any authenticated non-client user whose users.email is in MASTER_ADMIN_EMAILS may list, archive, restore, and delete every Account. POST /auth/register (unauthenticated, unless production and ALLOW_OPEN_REGISTRATION is false) and POST /team/members (any owner, password chosen by that owner) will create a user with that email if it is globally unused, and neither path proves mailbox possession or env-list enrollment. An unused listed address therefore becomes a first-claimer privilege to cross-tenant admin.

### Trace

- `entrypoint` `backend/app/main.py:866` (register): Unauthenticated POST /auth/register creates a new Account and owner User. It blocks only when ENVIRONMENT is production and ALLOW_OPEN_REGISTRATION is false; it never consults MASTER_ADMIN_EMAILS.
- `propagation` `backend/app/main.py:885` (User.email assignment): The new owner's email is stored as payload.email.lower(). Collision control is a global unique lookup (User.email == raw payload.email at line 873 plus the unique column) and does not reserve listed admin addresses.
- `propagation` `backend/app/main.py:978` (create_team_member): Alternate mint: any owner may POST /team/members with role owner or catalogger, a password they choose, and a globally unused email, including an address from MASTER_ADMIN_EMAILS. A client-role mint would be blocked later by enforce_client_http on /admin/*.
- `propagation` `backend/app/auth.py:114` (is_master_admin_email): After login, every request loads User from JWT sub+account_id. Admin is decided only by stripping/lowercasing that DB email and testing membership in settings.master_admin_email_list().
- `sink` `backend/app/main.py:1242` (admin_list_accounts): _require_master_admin(current) is the sole admin gate on GET /admin/accounts (and the sibling archive/restore/delete/analytics routes). A claimed listed email yields cross-tenant account names, owner emails, and destructive admin actions.

### Evidence

- `backend/app/config.py:145` — allow_open_registration defaults true; production register is denied only when this flag is explicitly false.
- `backend/app/config.py:147` — master_admin_emails is a comma-separated env string, not a user-id enrollment table.
- `backend/app/main.py:451` — _require_master_admin checks only is_master_admin_email(current.email); role and account_id are ignored.
- `backend/app/main.py:1420` — DELETE /admin/accounts/{account_id} deletes another tenant's users, sales, lots, photos, jobs, and account row after the same email check (blocked only when account_id equals the caller's own account).
- `backend/app/auth.py:83` — get_current_user does not add an admin claim or second factor; a normal HS256 staff JWT is enough once the email matches.

### Blockers

- This run forbids executing target code and has no OS-enforced sandbox (empty allowlist env, no-network namespace, read-only target, trusted parent-side no-follow promotion), so the register-then-GET /admin/accounts result was not observed on dummy tenants.
- Whether a deployment's MASTER_ADMIN_EMAILS addresses are already enrolled User rows, and whether ALLOW_OPEN_REGISTRATION is false in production, is env-only and not source-visible.

### Validation plan

**Local (sandboxed dummy tenants only):** On an isolated SQLite API with dummy tenants already seeded, set MASTER_ADMIN_EMAILS to an unused mailbox and ALLOW_OPEN_REGISTRATION=true. POST /auth/register with that email, then GET /admin/accounts with the returned JWT and confirm 200 listing the seed tenants. Repeat from a second dummy owner via POST /team/members creating a catalogger with another unused listed email and a password the owner sets, POST /auth/login as that catalogger, and confirm that JWT also passes GET /admin/accounts while a third owner whose email is not listed receives 403.

**Owner-observed deployment:** Owner-observed only: inspect production MASTER_ADMIN_EMAILS and ALLOW_OPEN_REGISTRATION, and whether each listed address already exists as a users.email. Do not register or invite those addresses on a live system.

---

## 2. Open registration defaults on and mints owner JWTs

**Fingerprint:** `unauth.auth.open-registration-fail-open`

**Claimed root cause:** allow_open_registration defaults True and register only 403s when is_production and that flag is false, so a production deploy that omits the flag keeps anonymous owner-JWT minting.

**Description:** POST /auth/register is public. The production close-gate requires both ENVIRONMENT=production and ALLOW_OPEN_REGISTRATION=false. The setting defaults true, .env.example leaves it true, and DEPLOY.md treats closing signup as a post-bootstrap step. A successful call creates an owner tenant and returns an access JWT. The web UI always exposes Register.

### Trace

- `entrypoint` `backend/app/main.py:866` (POST /auth/register): Unauthenticated register endpoint.
- `propagation` `backend/app/config.py:145` (ALLOW_OPEN_REGISTRATION default): allow_open_registration Field default True; production_must_be_safe does not require it false.
- `sink` `backend/app/main.py:893` (owner JWT issuance): After creating Account, owner User, and General sale, register returns create_access_token for that owner.

### Evidence

- `backend/app/main.py:868` — 403 only if is_production and not allow_open_registration.
- `backend/app/config.py:145` — ALLOW_OPEN_REGISTRATION defaults True with comment to set true only to bootstrap.
- `backend/app/config.py:210` — production_must_be_safe rejects weak SECRET_KEY, SQLite, and empty CORS_ORIGINS in production, but does not require allow_open_registration false.
- `backend/.env.example:15` — Example env sets ALLOW_OPEN_REGISTRATION=true.
- `DEPLOY.md:52` — Deploy table says default true and to set false after the first real account.
- `frontend/app/page.tsx:36` — Public login page posts /auth/${mode}, which is /auth/register when the always-visible Register tab is selected.
- `backend/app/auth.py:43` — create_access_token signs sub/account_id/role with HS256 and long default TTL.

### Blockers

- No OS-enforced sandbox is available (empty allowlist env, no-network namespace, read-only target, trusted parent-side no-follow promotion), so TestClient register calls were not executed.
- Live Railway ALLOW_OPEN_REGISTRATION and ENVIRONMENT were not observed; this run must not probe the public API or create accounts.

### Validation plan

**Local (sandboxed dummy tenants only):** In the OS sandbox with dummy data, POST /auth/register under default settings and expect 200 plus access_token. Monkeypatch is_production True while leaving allow_open_registration at default True and expect 200 plus access_token. Then set allow_open_registration False with is_production True and expect 403. Do not call any deployed host.

**Owner-observed deployment:** Owner reads only the Railway API service variables ALLOW_OPEN_REGISTRATION and ENVIRONMENT. If ENVIRONMENT is production/prod and the flag is unset or true, treat the issue as live. Do not POST /auth/register against production.

---

## 3. POST /jobs stores an un-chrooted input_folder that process_job rglobs into Gemini and the caller's catalog

**Fingerprint:** `backend.app.main.create_job.input_folder.missing-upload-root-chroot`

**Claimed root cause:** CreateJobRequest.input_folder is persisted and later used as a filesystem and R2-namespace selector without the account upload-root chroot that _queued_job_upload_folder already implements, and parse_batch_folder derives tenant from that path rather than from ProcessingJob.account_id.

**Description:** An authenticated catalogger or owner can POST /jobs with an arbitrary input_folder. Unlike POST /jobs/{id}/upload-files, create_job does not require that path to sit under UPLOAD_ROOT/{account_id}. The row is inserted as queued, so the catalog worker can claim it without a separate unsafe /run trick. process_job then Path.rglob-s image suffixes, may rewrite files in that tree, copies them into catalog/{job.account_id}, and sends them to Gemini. The job row's account_id filter only authorizes who may own the job; it does not isolate the filesystem or R2 batch key. A path equal to the shared upload root or catalog/ subtree therefore selects other tenants' on-disk images. A path shaped as upload_root/{victim_account}/{victim_batch} makes parse_batch_folder restore that tenant's job-batches/ objects using worker R2 credentials.

### Trace

- `entrypoint` `backend/app/main.py:5506` (create_job): After blocking clients and binding sale_id via _sale_for_user, the handler writes payload.input_folder onto ProcessingJob with status=queued and no UPLOAD_ROOT/account_id relative_to check.
- `propagation` `backend/app/main.py:5585` (run_job): POST /jobs/{id}/run resolves the job by id and account_id, then uses Path(job.input_folder) for R2 staging with no second chroot. create_job already queued the row, so app.worker can also claim it.
- `propagation` `backend/app/job_batch_storage.py:32` (parse_batch_folder): account_id and batch_id are taken from the first two path components under upload_root(), not from the job row, then used as the R2 prefix job-batches/{account}/{batch}/.
- `sink` `backend/app/services/processing.py:882` (_process_job_body): folder.rglob('*') collects IMAGE_EXTS under the attacker-chosen directory; _add_catalog_photo persists copies under job.account_id and the pipeline sends frames to Gemini.

### Evidence

- `backend/app/main.py:5428` — _queued_job_upload_folder is the sibling control: resolve job.input_folder and relative_to(UPLOAD_ROOT/account_id), used only by upload-files, not by create_job.
- `backend/app/main.py:5750` — delete_job only rmtree when the resolved folder is under UPLOAD_ROOT, so destructive cleanup is chrooted while catalog read is not.
- `backend/app/services/processing.py:173` — _add_catalog_photo calls persist_catalog_photo(job.account_id, photo_path), copying whatever rglob selected into the caller's catalog namespace.
- `backend/app/services/image_preprocess.py:203` — normalize_job_folder_image writes re-encoded bytes back under job_folder, so a path into another tenant's batch or catalog directory is a write as well as a read.
- `frontend/app/dashboard/page.tsx:4819` — Dashboard enqueueJob JSON-posts the Staff server-path text field as input_folder, documenting that the API is expected to accept a raw host path.
- `infra/docker/Dockerfile.api:16` — Image layout is /app/backend/data/uploads (WORKDIR /app, uvicorn from backend), so the upload-root and catalog/ trees are a source-visible absolute layout on API and worker images.

### Blockers

- No OS-enforced empty-allowlist sandbox; hunters must not execute target code, so rglob, R2 restore, persist_catalog_photo, and Gemini were not observed on dummy tenants.
- No trusted parent-side scratch promotion procedure, so no local fixture artifact can be retained.
- Whether a given Railway worker disk currently holds catalog/{other_account} files, and whether R2 list/get on job-batches/* succeeds for a path-derived other account, is a deployment fact outside this source review.

### Validation plan

**Local (sandboxed dummy tenants only):** In an isolated dummy two-account fixture with Gemini and R2 mocked, create catalog JPEGs under upload_root/catalog/{B} and a job-batch under upload_root/{B}/{batch}. As catalogger A, insert a ProcessingJob via the create_job request body with input_folder pointing at B's catalog directory and at B's batch directory. Invoke process_job. Confirm A's Photo rows receive B's pixels, B's files may be re-encoded, and restore_job_folder_from_r2 is invoked with job-batches/{B}/{batch}/ rather than A's id. Do not use production data or network.

**Owner-observed deployment:** Owner-observed: inspect one worker replica's backend/data/uploads/catalog layout after mixed-tenant jobs, and confirm application-side R2 list is not additionally scoped by ProcessingJob.account_id. Do not probe a live customer tenant.

---

## 4. Catalogger snapshot ingest publishes to the Page when calendar_auto_approve is on

**Fingerprint:** `facebook.calendar.auto-approve.non-owner-trigger`

**Claimed root cause:** calendar_auto_approve hard-codes confirm=True as a synthetic owner and is triggered by catalogger-reachable snapshot ingest, bypassing require_facebook_poster on the publish routes.

**Description:** HTTP preview, publish, Approve, and Post now call require_facebook_poster and publish_posts refuses confirm=false. force does not drop those gates. A parallel path does: if the owner has set facebook_calendar_auto_approve, sync_facebook_calendar_after_snapshot selects the first owner user and calls publish_posts or the in-memory poster thread with confirm=True. POST /integrations/engagement/snapshots is available to any authenticated staff JWT (owner or catalogger; clients are blocked by enforce_client_http) and always runs that sync, so a catalogger can submit snapshot metrics that score as Push and cause Page schedule/publish without the owner confirm click. PATCH /integrations/facebook/settings is owner-only but also syncs every engagement sale as soon as the flag is enabled.

### Trace

- `entrypoint` `backend/app/engagement_routes.py:28` (ingest_engagement_snapshot): Any authenticated staff user can POST a watcher snapshot; there is no owner gate.
- `propagation` `backend/app/services/engagement_store.py:242` (apply_engagement_snapshot): After commit, every snapshot calls sync_facebook_calendar_after_snapshot.
- `propagation` `backend/app/services/facebook_calendar.py:698` (sync_facebook_calendar_after_snapshot): When facebook_calendar_auto_approve is true, the first owner is loaded and pending slots are published with confirm=True.
- `sink` `backend/app/services/facebook_page.py:747` (publish_posts): create_page_feed_post runs under the synthetic owner without a new HTTP confirm from that owner.

### Evidence

- `backend/app/services/roles.py:58` — can_manage_integrations is owner-only; that is the HTTP Facebook poster gate.
- `backend/app/services/facebook_page.py:589` — publish_posts returns 400 unless confirm is true.
- `backend/app/services/facebook_calendar.py:740` — Auto-approve passes confirm=True into the background poster.
- `backend/app/facebook_routes.py:178` — Enabling the setting immediately syncs every engagement sale.
- `backend/app/facebook_post_tasks.py:166` — The background runner does not call require_facebook_poster before publish_posts.
- `backend/tests/test_facebook_page.py:626` — Catalogger preview and POST /facebook/posts return 403.
- `backend/tests/test_facebook_page.py:932` — Turning calendar_auto_approve on causes Graph feed calls without a per-slot confirm body.

### Blockers

- No OS-enforced sandbox, so the catalogger-snapshot plus auto-approve path was not executed.
- Whether the deployed watcher/extension uses a catalogger token for POST /integrations/engagement/snapshots is not source-provable.

### Validation plan

**Local (sandboxed dummy tenants only):** Using the existing TestClient fixtures, connect a Page, keep an owner User row, set calendar_auto_approve as owner, then authenticate as a catalogger and POST /integrations/engagement/snapshots for a future Push lot with Graph feed mocked. Expect create_page_feed_post to run and a scheduled FacebookPagePost row to appear. Repeat with the flag false and expect no feed call.

**Owner-observed deployment:** Owner confirms which staff role the live watcher ingest uses and whether auto-approve is enabled on production accounts.

---

## 5. Client and catalogger can stack uncapped batch catalog jobs on the shared operator Gemini key

**Fingerprint:** `gemini.jobs.uncapped-batch-queue-spend`

**Claimed root cause:** Photo-count enforcement is tied to single_lot_mode plus non-facebook channel, and there is no per-account job-count or Gemini token/$ quota on the shared key.

**Description:** Catalog /jobs/{id}/run is intentionally allowed for catalogger and client on their jobs. The missing bound is cardinality, not the run permission. POST /jobs/upload defaults to single_lot_mode=false, and the 40-photo CT Bids cap is skipped for batch mode and for facebook listing_channel. upload-files repeats that single-lot-only cap. One /run then Flash-triages every file (plus tape-verify and a Pro author pass per tape segment) using the process-wide GOOGLE_API_KEY. Callers can also enqueue an unbounded number of jobs; only catalog_job_max_concurrent (default 1) and gemini_global_concurrency (default 3, shared across tenants) limit in-flight work. Attacker is a client or catalogger. Affected principal is the operator Gemini bill and other tenants waiting on the global Gemini semaphore. Execution identity is the API/worker with the operator key.

### Trace

- `entrypoint` `backend/app/main.py:5362` (create_job_from_upload): Authenticated client or staff uploads files; single_lot_mode defaults false; the 40-photo reject runs only for single-lot non-facebook jobs.
- `propagation` `backend/app/main.py:5467` (upload-files): Appending files applies CTBIDS_MAX_PHOTOS_PER_LOT only when the job is already single_lot_mode.
- `propagation` `backend/app/main.py:5546` (run_job): Client or catalogger may start their job; no spend quota is checked before queueing Gemini work.
- `sink` `backend/app/services/processing.py:567` (_process_job_batch_pipeline): Every uploaded image is sent through triage_ordered_photo_batch (one Flash call per file) before per-segment Pro inventory, billed to the operator key.

### Evidence

- `backend/app/services/roles.py:111` — Clients may POST /jobs/{id}/run and /jobs/upload.
- `backend/app/catalog_limits.py:9` — The only catalog still-photo count cap is CTBIDS_MAX_PHOTOS_PER_LOT=40; job ingest applies it only for single-lot non-facebook uploads.
- `backend/app/upload_storage.py:30` — save_uploaded_files has no maximum file count.
- `backend/app/job_tasks.py:108` — can_start_job_for_account only caps concurrent running jobs, not queued jobs or tokens.
- `backend/app/core/gemini_slots.py:25` — gemini_slot is a process-wide semaphore (default 3) shared by all accounts.

### Blockers

- No OS-enforced sandbox; executing /jobs/upload and /run would spend the real operator Gemini key or require a mocked pipeline that was not run.
- Deployed Google Cloud/Gemini project quotas, Railway body-size limits, and reverse-proxy request limits are not source-visible.

### Validation plan

**Local (sandboxed dummy tenants only):** With Gemini and R2 mocked, POST /jobs/upload single_lot_mode=false with 41 tiny JPEGs, then POST /jobs/{id}/run. Assert HTTP 200 and that process_job/triage receives 41 paths. Repeat upload+run to show a second queued job is stored. Do not call live Gemini.

**Owner-observed deployment:** Owner checks whether an upstream proxy already rejects large multipart batches and whether the billed Gemini project has a hard daily cap that would contain a stacked client.

---

## 6. Default JWT SECRET_KEY stays accepted whenever ENVIRONMENT is not production

**Fingerprint:** `cloud.config.default-secret-key.skips-production-validator`

**Claimed root cause:** production_must_be_safe is skipped for any non-production ENVIRONMENT, while the in-image default SECRET_KEY and backend/.env.example placeholder remain valid in that mode, and deploy docs start the public API in that mode.

**Description:** The production validator refuses weak SECRET_KEY values, SQLite, and empty CORS_ORIGINS, but only after ENVIRONMENT is production or prod. Settings still default SECRET_KEY to the public placeholder change-me-dev-secret, Dockerfile.api/Procfile do not set SECRET_KEY or ENVIRONMENT, and DEPLOY.md first-time order deploys the API with ENVIRONMENT=development then publishes a domain. If that bootstrap (or a forgotten ENVIRONMENT flip) is live and SECRET_KEY was left unset or set to a documented placeholder, an internet caller can HS256-sign staff JWTs with that key. user_from_access_token still requires a matching active User row (UUID sub plus account_id); the break is issuer impersonation for those existing rows, including re-signing a leaked or expired payload. Live Railway env values were not observed.

### Trace

- `entrypoint` `DEPLOY.md:16` (First-time Railway order): Documented first deploy sets ENVIRONMENT=development on the API so CORS_ORIGINS is not required; the same order then uses a public API URL and later flips to production.
- `propagation` `backend/app/config.py:30` (Settings.secret_key): SECRET_KEY defaults to the well-known placeholder change-me-dev-secret when unset. .env.example is not loaded; the image has no .env.
- `propagation` `backend/app/config.py:211` (Settings.production_must_be_safe): If environment is not production or prod, the function returns without rejecting weak keys, SQLite, or missing CORS_ORIGINS.
- `propagation` `backend/app/auth.py:53` (create_access_token): Access tokens are HS256-signed with settings.secret_key and carry sub, account_id, and role with no audience or issuer.
- `sink` `backend/app/auth.py:58` (user_from_access_token): jwt.decode accepts HS256 with settings.secret_key, require_sub, and verify_aud False, then loads User by payload sub plus account_id.

### Evidence

- `backend/.env.example:1` — Env template sets SECRET_KEY=change-me, which production_must_be_safe would reject only after ENVIRONMENT=production.
- `backend/app/config.py:139` — ENVIRONMENT defaults to development, so is_production is false unless the host overrides it.
- `backend/app/config.py:214` — Weak-key set includes empty, change-me, and change-me-dev-secret, plus any secret shorter than 32 characters.
- `backend/app/auth.py:70` — After a valid signature, authentication still requires User.id==sub and User.account_id==account_id and an active Account.
- `DEPLOY.md:47` — The later variables table asks for a long SECRET_KEY, but that check is not enforced until ENVIRONMENT is switched to production.
- `DEPLOY.md:60` — After the variables table, the API service is told to Generate domain, making the development-mode bind public.

### Blockers

- No OS-enforced sandbox is available (empty allowlist env, no-network namespace, read-only target, trusted parent-side no-follow promotion), so a local dummy JWT forge against the default key was not executed.
- Live Railway API ENVIRONMENT and SECRET_KEY were not observed; this run must not probe Railway.
- Whether the operator already set a long random SECRET_KEY during the development bootstrap is a host-dashboard fact.

### Validation plan

**Local (sandboxed dummy tenants only):** In an isolated empty-env sandbox with no backend/.env, start the FastAPI app with ENVIRONMENT=development and SECRET_KEY unset. Seed one dummy Account and User. Mint an HS256 token with key change-me-dev-secret for that user's id and account_id (and a second token for a non-existent pair). Confirm user_from_access_token returns the seeded user for the first token and raises 401 for the second. Repeat startup with ENVIRONMENT=production and unset SECRET_KEY and confirm Settings() raises ValueError before the server binds.

**Owner-observed deployment:** Owner inspects the Railway API service variables only: if ENVIRONMENT is not production/prod, or SECRET_KEY equals a documented placeholder or is shorter than 32 characters, treat the issue as live. Do not send traffic to the public hostname.

---

## 7. Unauthenticated Facebook OAuth callback writes Page tokens from HS256 state alone

**Fingerprint:** `facebook.oauth.callback.state-only-binding`

**Claimed root cause:** The callback trusts only decode_oauth_state on a SECRET_KEY HS256 JWT and then complete_oauth writes sealed Facebook tokens without session binding, PKCE, audience, or single-use state.

**Description:** GET /integrations/facebook/oauth/callback is unauthenticated. A 15-minute HS256 JWT signed with SECRET_KEY (purpose=meta_oauth, account_id, user_id, exp; no aud, iss, or jti) is enough to exchange a Facebook code and persist user and Page access tokens on the account named in state. There is no PKCE verifier, no one-time state store, and no check that the browser is the owner who called /oauth/start. A signed start URL is therefore a transferable capability: a Facebook admin who completes an attacker-started dialog binds their Page tokens to the attacker tenant, and a stolen owner start URL lets another Facebook user overwrite that tenant's stored Page connection. Staff JWTs are not accepted as state because they lack purpose=meta_oauth. Meta redirect_uri is taken from settings, not Host.

### Trace

- `entrypoint` `backend/app/facebook_routes.py:114` (facebook_oauth_callback): Unauthenticated GET receives Facebook code and state and calls complete_oauth.
- `propagation` `backend/app/services/facebook_page.py:85` (decode_oauth_state): jwt.decode verifies HS256 and exp with verify_aud False; only purpose, account_id, and user_id are checked.
- `propagation` `backend/app/services/facebook_page.py:176` (complete_oauth): State account/user are loaded; is_owner is required, but no current Bearer or cookie is required.
- `sink` `backend/app/services/facebook_page.py:211` (complete_oauth): Facebook user and Page tokens are sealed onto the state-selected account and committed.

### Evidence

- `backend/app/facebook_routes.py:114` — Callback route declares no Depends(get_current_user).
- `backend/app/services/facebook_page.py:73` — State claims are purpose, account_id, user_id, iat, exp; no aud, iss, jti, or nonce.
- `backend/app/services/facebook_page.py:91` — decode options require exp and disable audience verification.
- `backend/app/services/meta_graph.py:49` — oauth_dialog_url sends code flow without code_challenge.
- `backend/app/auth.py:46` — Staff access tokens use the same SECRET_KEY and also set verify_aud False, but omit purpose.
- `backend/tests/test_facebook_page.py:408` — Unit test completes OAuth and stores a Page token using only code and state.

### Blockers

- No OS-enforced sandbox and no parent scratch-promotion procedure, so target code was not executed.
- Facebook authorization-code single-use, consent-screen behavior, and the deployed Meta Valid OAuth Redirect URIs list are not observable from source.

### Validation plan

**Local (sandboxed dummy tenants only):** With Graph helpers mocked as in test_oauth_callback_stores_encrypted_page_token, GET the callback without Authorization using encode_oauth_state for account A and a dummy code; assert A receives the sealed tokens. Repeat with a staff JWT as state and expect 400. Repeat with account A state while the TestClient is authenticated as a different user and assert tokens still land on A.

**Owner-observed deployment:** Owner compares META_OAUTH_REDIRECT_URI to the Meta app allowlist and confirms Facebook will not send a code to any other URI. Confirm production sets PUBLIC_WEB_ORIGIN so the post-callback 302 is not the first CORS origin by accident.

---

## 8. Staff JWT placed in EventSource query string; same Query door authenticates catalog uploads

**Fingerprint:** `unauth.sse.access-token-query`

**Claimed root cause:** get_current_user_sse accepts Query access_token as equivalent to Authorization Bearer, and the dashboard EventSource puts that JWT in the URL. Default uvicorn process logging records the request query.

**Description:** A lower-trust observer of API or proxy access logs, or of the HTTP request target, can recover a staff JWT. The dashboard builds EventSource on /jobs/{id}/events?access_token= plus the live token. get_current_user_sse treats Query access_token as equivalent to Authorization Bearer and mints a full User for GET /jobs/{id}/events, POST /jobs/upload, and POST /jobs/{id}/upload-files. First-party upload clients send the JWT in the Authorization header, not the query; the observed client leak is the EventSource URL. Tokens default to 43200 minutes and user_from_access_token does not bind iat to password changes. Procfile and Dockerfile.api start uvicorn without --no-access-log. Whether the running process or Railway edge actually retains the request-target query was not observed.

### Trace

- `entrypoint` `frontend/app/dashboard/page.tsx:3159` (dashboard EventSource URL): Browser builds /jobs/{id}/events?access_token= plus the staff JWT and opens EventSource on it.
- `propagation` `backend/app/auth.py:96` (get_current_user_sse Query parameter): FastAPI Query access_token is documented as the EventSource JWT and is optional beside HTTP Bearer.
- `sink` `backend/app/auth.py:105` (query token consumed as session): Query value is OR'd with Bearer credentials and passed to user_from_access_token, minting a full User principal for SSE and for POST /jobs/upload and /jobs/{id}/upload-files.

### Evidence

- `frontend/app/dashboard/page.tsx:3159` — EventSource URL concatenates encodeURIComponent(token) into query access_token.
- `backend/app/auth.py:105` — token = Bearer credentials or stripped access_token query; empty raises 401.
- `backend/app/main.py:5784` — job_events_sse Depends(get_current_user_sse) and documents query access_token.
- `backend/app/main.py:5364` — create_job_from_upload Depends(get_current_user_sse), so query JWT also authorizes multipart catalog upload.
- `Procfile:3` — Production API is uvicorn app.main:app without --no-access-log.
- `infra/docker/Dockerfile.api:22` — Container CMD also launches uvicorn without disabling access logs.
- `backend/app/config.py:150` — access_token_expire_minutes defaults to 43200 (30 days).
- `backend/app/main.py:943` — change_own_password updates password_hash only; user_from_access_token still accepts prior JWTs.

### Blockers

- OS-enforced sandbox is unavailable (empty allowlist env, no-network namespace, read-only target, trusted parent-side no-follow promotion); uvicorn access-log request-target capture was not executed.
- Live Railway or proxy access-log retention for /jobs/*/events?access_token= is a host-dashboard fact and was not observed; this run must not fetch production.

### Validation plan

**Local (sandboxed dummy tenants only):** In the required OS sandbox with dummy tenants, mint a JWT, then start uvicorn (not TestClient) and GET /jobs/{id}/events?access_token=<dummy-jwt> plus POST /jobs/upload?access_token=<dummy-jwt>. Confirm the query door authenticates and that the uvicorn access-log request target contains the access_token query name. Redact token values in any retained log excerpt.

**Owner-observed deployment:** Owner inspects existing Railway or API access-log settings and any retained /jobs/*/events?access_token= lines; do not replay live tokens or probe production.

---

## 9. Catalogger JWT can invoke leftover owner-documented R2 migrate, ping, and CORS-read ops

**Fingerprint:** `r2.mgmt.catalogger-jwt-owner-documented-ops`

**Claimed root cause:** Leftover R2 management handlers gate on get_current_user only, while the documented and sibling owner control (_require_owner / can_manage_integrations / POST cors is_owner check) is omitted, so a catalogger JWT exercises the shared-bucket credential for ping write, CORS read, and own-account migrate.

**Description:** UI-less R2 management routes are authenticated but not owner-authorized. DEPLOY.md says POST /integrations/r2/migrate-catalog is account-owner ops. Sibling POST /integrations/r2/cors and can_manage_integrations require owner (cors also allows master-admin email). migrate_catalog_to_r2, r2_ping, and r2_cors_get stop at get_current_user. Clients are 403 via the HTTP allowlist. A same-account catalogger JWT can therefore (1) write and delete the shared-bucket key _health/r2-ping.txt through the API Account token, outside catalog/{account_id}/, (2) read the bucket-wide CORS rules and AllowedOrigins, and (3) start a local-to-R2 migrate of that account. Cross-tenant migrate still requires master-admin email. No handler was executed in this run.

### Trace

- `entrypoint` `backend/app/main.py:1491` (GET /integrations/r2/ping): r2_ping depends on get_current_user only and then calls object_storage.ping when R2 vars are set.
- `propagation` `backend/app/auth.py:83` (get_current_user): JWT is bound to User via sub plus account_id; enforce_client_http runs, then the User is returned with no owner or can_manage_integrations check.
- `propagation` `backend/app/services/roles.py:84` (client_http_allowed): No /integrations/r2 path is allowlisted, so clients are 403; catalogger and owner are not clients and continue.
- `sink` `backend/app/object_storage.py:167` (ping): ping put_bytes then delete_object on _health/r2-ping.txt using the API R2 identity; that key is not under catalog/{account_id}/.

### Evidence

- `DEPLOY.md:116` — Migrate is documented as POST /integrations/r2/migrate-catalog with auth as account owner and as an ops endpoint not shown in the Account UI.
- `backend/app/main.py:1447` — migrate_catalog_to_r2 also Depends(get_current_user) only; is_owner is not called before migrate_local_photos_to_r2.
- `backend/app/main.py:1506` — r2_cors_get is get_current_user only and returns CatalogR2CorsOut origins plus the raw bucket CORSRules.
- `backend/app/main.py:1532` — Sibling POST /integrations/r2/cors requires is_owner(current) or is_master_admin_email before put_bucket_cors.
- `backend/app/services/roles.py:58` — can_manage_integrations is owner-only; leftover R2 GET/migrate handlers do not call it.
- `backend/app/main.py:430` — _require_owner exists and is used on other account-admin routes but is not invoked by migrate, ping, or GET cors.
- `backend/app/schemas.py:657` — CatalogR2MigrateIn.account_id is commented as master admin only; owners always migrate their own account — the schema comment does not mention cataloggers, and the handler does not reject them.
- `backend/app/object_storage.py:169` — The ping probe key is the literal _health/r2-ping.txt on the shared bucket.

### Blockers

- This run has no OS-enforced sandbox and forbids executing target code, so catalogger versus owner versus client status codes were not observed.
- Whether a live tenant has catalogger users and R2 configured is an environment fact; this run must not call the public API or write the shared bucket.
- No parent scratch-promotion procedure is available, so a local dummy-JWT check could not be recorded as an artifact.

### Validation plan

**Local (sandboxed dummy tenants only):** In an isolated dummy two-role fixture with R2 mocked (put_bytes/delete_object/get_bucket_cors/migrate_local_photos_to_r2), mint catalogger, owner, and client JWTs. GET /integrations/r2/ping, GET /integrations/r2/cors, and POST /integrations/r2/migrate-catalog as each role. Confirm client is 403; record whether catalogger is 200 while POST /integrations/r2/cors is 403. Assert the mocked ping key is _health/r2-ping.txt. Do not use live R2 credentials.

**Owner-observed deployment:** Owner-observed only: confirm leftover /integrations/r2/migrate-catalog, /ping, and GET /cors are still mounted and whether any catalogger is expected to call them. Do not invoke ping against the production bucket (it writes a shared key).

---

## 10. Catalog privacy depends on an unencoded private-bucket and r2.dev-off policy

**Fingerprint:** `r2.bucket.public-development-url-policy`

**Claimed root cause:** Object privacy is a Cloudflare bucket setting that source neither encodes nor enforces: puts omit ACL, reads assume presign-only access, and the documented private / no-public-development-URL control lives only in DEPLOY.md.

**Description:** The app stores catalog/{account_id}/…, job-batches/{account_id}/{batch_id}/…, thumbs, and a _health/r2-ping.txt probe (put then best-effort delete) with no object ACL and serves staff browsers via JWT-gated media-url GetObject presigns (ExpiresIn clamped 60..86400; handlers pass 3600; lot/photo queries bind Lot.account_id == current.account_id). DEPLOY.md tells operators to keep the bucket private and not enable Cloudflare's public development URL. No bucket policy, public-access block, or r2.dev toggle exists in source. Source never constructs an r2.dev URL. If that dashboard switch (or a public custom domain / public-read ACL) is on, an anonymous caller can GET those keys without a JWT or presign. Keys are deterministic from account_id plus photo or batch id. R2 CORS AllowedOrigins come from cors_allow_origins (always includes localhost; stars dropped) and are not caller-chosen; CORS does not publish object bytes. Live ACL and public-URL state were not observed and must not be probed.

### Trace

- `entrypoint` `backend/app/catalog_storage.py:52` (persist_catalog_photo): Authenticated catalog persist copies tenant photos to catalog/{account_id}/{photo_id}.ext and put_file's that key to the shared bucket with no ACL.
- `propagation` `backend/app/object_storage.py:76` (put_bytes): put_object is called with Bucket, Key, Body, and ContentType only; put_file at line 83 is the same kwargs. No ACL, Grant, or private-enforcement argument is set on catalog, thumb, job-batch, or ping writes.
- `propagation` `backend/app/job_batch_storage.py:40` (r2_key_for_file): Job upload mirrors use job-batches/{account_id}/{batch_id}/… on the same bucket, so a public origin would also expose staged upload batches.
- `sink` `backend/app/object_storage.py:86` (presigned_get_url): The only in-repo browser read path is a time-capped GetObject presign. Source never constructs an r2.dev URL; a public bucket origin would serve the same keys without that presign.

### Evidence

- `DEPLOY.md:120` — Operators are told to keep the bucket private and not enable the public development URL so browsers use short-lived media-url presigns.
- `backend/app/config.py:170` — R2 settings are account id, access key, secret, bucket, and endpoint only; there is no public-URL or ACL field.
- `backend/app/object_storage.py:190` — put_bucket_cors applies GET/HEAD for cors_allow_origins and drops any origin containing *; it does not set bucket ACL or disable a public development URL.
- `backend/app/config.py:235` — cors_allow_origins always includes http://127.0.0.1:3000 and http://localhost:3000, so an owner POST /integrations/r2/cors writes those localhost origins onto the bucket CORS rule even in production.
- `backend/app/main.py:3519` — GET /lots/{lot_id}/media-url requires get_current_user and loads the lot with Lot.account_id == current.account_id before minting a presign; that gate does not bind a public r2.dev GET of the same key.
- `DEPLOY.md:106` — Deploy docs recommend an R2 Account API token on the API (and later the worker) for the shared estate-catalog bucket; least-privilege prefix keys are not encoded.

### Blockers

- Live Cloudflare R2 Public development URL, custom domain, and object/bucket ACL are dashboard facts and are not in this repository.
- No IaC or bucket-policy render exists to evaluate locally.
- This run must not fetch pub-*.r2.dev, a custom R2 domain, or any catalog/job-batch key.
- No OS-enforced sandbox was available to inspect put_object kwargs at runtime beyond source.

### Validation plan

**Local (sandboxed dummy tenants only):** In a network-disabled review, confirm put_bytes/put_file kwargs still omit ACL and that no code path concatenates r2.dev. That only reconfirms source; it does not settle live exposure.

**Owner-observed deployment:** Owner, in the Cloudflare R2 bucket Settings only, confirms Public development URL is disabled, no public custom domain is attached, and access remains private/presign. Do not request catalog/ or job-batches/ objects from the internet.

---

## 11. Watcher item_url is stored unsanitized and deterministically copied into Gemini Facebook captions

**Fingerprint:** `gemini.engagement.untrusted-item-url-caption-sink`

**Claimed root cause:** Engagement ingest accepts unconstrained http(s) item_url/auction_url and later caption builders treat snapshot.item_url as the lot link without a CT Bids host check.

**Description:** A catalogger (Watcher JWT on the same account) can POST /integrations/engagement/snapshots with an arbitrary item_url and auction_url. Those strings are stored, preferred over constructed CT Bids URLs, concatenated unlabeled into the Gemini Flash caption prompt, and then appended to the caption by arrange_facebook_caption / template_caption even if the model ignores them. normalize_ctbids_sale_url will also turn https://attacker.example/estate-sale/1/item/2 into a See the whole sale line on that host. Attacker is the catalogger. Affected principal is the account owner (Gemini caption spend and the Page caption the owner later posts). Execution identity is the API process holding GOOGLE_API_KEY and, if facebook_calendar_auto_approve is on, the Page token as a synthetic owner. This is not a claim that cataloggers bypass the Facebook owner publish gate (peer hunter-facebook); the code-level failure is the missing URL allowlist plus the deterministic caption sink.

### Trace

- `entrypoint` `backend/app/engagement_routes.py:29` (ingest_engagement_snapshot): Any authenticated non-client account member can POST a snapshot; get_current_user only blocks clients, and there is no owner or Facebook-poster check.
- `propagation` `backend/app/services/engagement_store.py:214` (apply_engagement_snapshot): item_url is stored as (item.item_url or '')[:600] with no scheme or host validation.
- `propagation` `backend/app/services/facebook_page.py:317` (ctbids_lot_url): A non-empty snapshot.item_url replaces the constructed ctbids.com sale/item URL.
- `propagation` `backend/app/services/facebook_caption.py:412` (_draft_with_gemini): Lot link and sale link are interpolated into the Flash user prompt without an untrusted label.
- `sink` `backend/app/services/facebook_caption.py:214` (arrange_facebook_caption): The stored lot_url is appended to the caption after any model or template body, so the Watcher URL reaches the text that preview, publish, and auto-approve consume.

### Evidence

- `backend/app/schemas.py:1036` — EngagementItemIn.item_url is str with extra=forbid only; not HttpUrl and not host-limited.
- `backend/app/services/ctbids_urls.py:16` — normalize_ctbids_sale_url strips /item/ but never requires ctbids.com.
- `backend/app/services/facebook_calendar.py:579` — _draft_item calls draft_facebook_caption with ctbids_lot_url(lot, snapshot=snapshot).
- `backend/app/services/facebook_calendar.py:698` — If facebook_calendar_auto_approve is set, snapshot ingest drafts and publishes as the first owner user; publish auth is peer-owned.

### Blockers

- No OS-enforced sandbox and no parent scratch-promotion procedure; this run forbids target execution, so template_caption/arrange_facebook_caption were not executed against a dummy URL.
- Whether a production owner enabled facebook_calendar_auto_approve is a deployment fact; the URL-to-caption sink does not depend on it.

### Validation plan

**Local (sandboxed dummy tenants only):** In an isolated pytest with settings.google_api_key unset, seed a catalogger JWT and a lot with ctseller_sale_id/ctseller_item_id. POST /integrations/engagement/snapshots with item_url=https://evil.example/estate-sale/9/item/8 (matching item id). Load the snapshot and call ctbids_lot_url(lot, snapshot=row), then template_caption and arrange_facebook_caption with that return value. Assert both the lot URL host and 'See the whole sale: https://evil.example/estate-sale/9' appear. Call normalize_ctbids_sale_url on the same string. Do not start the API as a server, and do not call Meta or live Gemini.

**Owner-observed deployment:** Owner confirms whether facebook_calendar_auto_approve is enabled in production; if it is, treat snapshot ingest as also a Facebook publish confused-deputy under the Facebook owner-gate unit. Do not POST snapshots or captions to the live API, Page, or Gemini.

---

## 12. Facebook and CT Seller sealing fall open to SECRET_KEY XOR when Fernet import fails

**Fingerprint:** `facebook.crypto.xor-import-fallback`

**Claimed root cause:** Token seal functions catch ImportError and persist unauthenticated XOR under a xor: prefix keyed from SECRET_KEY, and unseal accepts that prefix unconditionally.

**Description:** seal_facebook_secret intends Fernet with a key derived from META_TOKEN_ENCRYPTION_KEY or SECRET_KEY. If cryptography cannot be imported, it writes xor: plus a repeating XOR of sha256(meta-page:SECRET_KEY), ignoring META_TOKEN_ENCRYPTION_KEY. unseal_facebook_secret always decrypts xor: blobs with that SECRET_KEY material even when Fernet is available. ctseller.seal_secret has the same ImportError fallback; its XOR key is sha256(SECRET_KEY) without the ctseller: prefix used by Fernet. Dockerfiles install cryptography>=42.0.0, so the branch is a fail-open for a missing or broken import rather than the default image path. API schemas do not return unsealed tokens.

### Trace

- `entrypoint` `backend/app/services/facebook_page.py:211` (complete_oauth): Fresh Facebook user and Page tokens are passed to seal_facebook_secret before commit.
- `propagation` `backend/app/services/facebook_crypto.py:20` (seal_facebook_secret): Fernet is attempted with sha256(meta-page: dedicated-or-SECRET_KEY).
- `sink` `backend/app/services/facebook_crypto.py:28` (seal_facebook_secret): ImportError stores xor: ciphertext derived only from SECRET_KEY.

### Evidence

- `backend/app/services/facebook_crypto.py:14` — Fernet key uses META_TOKEN_ENCRYPTION_KEY when set; XOR does not.
- `backend/app/services/facebook_crypto.py:38` — unseal always handles xor: with SECRET_KEY material.
- `backend/app/services/ctseller.py:91` — CT Seller secrets use the same ImportError XOR fallback.
- `backend/app/config.py:177` — Comments document optional META_TOKEN_ENCRYPTION_KEY with SECRET_KEY derivation.
- `requirements.txt:20` — cryptography is a declared dependency.
- `infra/docker/Dockerfile.api:10` — API image pip-installs requirements.txt, including cryptography.

### Blockers

- No OS-enforced sandbox, so ImportError was not forced at runtime.
- Whether every deployed process (Railway API, worker, and any non-Docker host) can import cryptography is not source-provable.

### Validation plan

**Local (sandboxed dummy tenants only):** On an isolated checkout with dummy strings only, monkeypatch cryptography.fernet import to raise ImportError, call seal_facebook_secret and ctseller.seal_secret, assert xor: prefixes, then unseal and compare to the inputs. Set META_TOKEN_ENCRYPTION_KEY to a distinct dummy and assert Facebook XOR still follows SECRET_KEY. Restore the import and assert Fernet output has no xor: prefix, then unseal a previously written xor: blob while Fernet imports successfully. Do not call Meta, CT Seller, or a running API.

**Owner-observed deployment:** Owner-observed only: in the production API and worker images, run a one-shot import cryptography check and SELECT whether any accounts.facebook_user_token_enc, facebook_page_token_enc, ctseller_password_enc, ctseller_access_token_enc, or ctseller_refresh_token_enc values start with xor:. Do not post to Facebook or CT Seller and do not rotate live tokens for this check.

---

## 13. Password change and team reset do not invalidate access JWTs

**Fingerprint:** `unauth.auth.jwt-no-revocation`

**Claimed root cause:** User has no token_version; password-change and reset paths never rotate or revoke outstanding JWTs, and user_from_access_token only checks signature, exp, user row, and Account.is_active.

**Description:** Access tokens are stateless HS256 JWTs with no jti or server denylist. PATCH /auth/password and owner team-member reset update password_hash only. There is no logout. A stolen token remains valid until exp (default 30 days, max 90) after the password is changed. Account disable is checked at verification; password change is not.

### Trace

- `entrypoint` `backend/app/main.py:929` (PATCH /auth/password): Authenticated password change rewrites password_hash and commits with no token revocation.
- `propagation` `backend/app/models.py:75` (User session state): User columns have no token_version, jti list, or password_changed_at used at verify time.
- `sink` `backend/app/auth.py:56` (user_from_access_token): Pre-change JWTs still decode and load the same User until exp as long as the account is active.

### Evidence

- `backend/app/main.py:943` — change_own_password sets password_hash and db.commit only.
- `backend/app/main.py:1035` — reset_team_member_password also only hashes the new password.
- `backend/app/auth.py:46` — create_access_token payload is sub, account_id, role, iat, exp; no jti or token_version.
- `backend/app/auth.py:70` — Verification loads User by id+account_id; no password-epoch or denylist check.
- `backend/app/config.py:150` — Default ACCESS_TOKEN_EXPIRE_MINUTES is 43200 (30 days), max 129600.

### Blockers

- OS-enforced sandbox and parent artifact promotion unavailable (empty allowlist env, no-network namespace, read-only target, trusted parent-side no-follow promotion); password-change-then-reuse of JWT A was not executed.
- Whether a deployment adds an extra API-gateway JWT denylist is host-only and not source-visible.

### Validation plan

**Local (sandboxed dummy tenants only):** In the OS sandbox on an isolated SQLite API, register a dummy owner and a dummy catalogger. Save JWT A from login. PATCH /auth/password as the owner, then GET /auth/me with JWT A (source predicts 200; intended revocation is 401). Repeat with the catalogger JWT after POST /team/members/{id}/reset-password. Confirm GET /auth/me still 403s when Account.is_active is false. Do not use production users.

**Owner-observed deployment:** Owner confirms production has no extra API gateway JWT denylist; do not test with live staff tokens.

---

## 14. Remembered staff password stored in backup-eligible SecureStore

**Fingerprint:** `mobile.securestore.remembered-password-backup`

**Claimed root cause:** loginWithPassword persists the raw password in expo-secure-store using AFTER_FIRST_UNLOCK rather than a ThisDeviceOnly accessibility class or a refresh token, and app.json neither disables Android allowBackup nor pins SecureStore backup rules.

**Description:** A backup or other-OS-profile restorer—not another app on the live profile—may recover the reusable Estate-EZ password. Each mobile sign-in writes email+password under ct_mobile_login_credentials with iOS AFTER_FIRST_UNLOCK and without requireAuthentication. That item can mint a new staff JWT via silent re-login. Same-app use on the signed-in OS user is out of scope.

### Trace

- `entrypoint` `mobile/context/AuthContext.tsx:110` (loginWithPassword credential persist): After POST /auth/login, the app writes JSON {email, password} to SecureStore key ct_mobile_login_credentials with no remember-me flag.
- `propagation` `mobile/lib/secureStorage.ts:5` (STORE_OPTS keychainAccessible): All secureSet calls, including the password, use AFTER_FIRST_UNLOCK and do not set requireAuthentication or a ThisDeviceOnly constant.
- `sink` `mobile/lib/secureStorage.ts:13` (SecureStore.setItemAsync): The password string is stored in the OS keychain/keystore-backed store. Boot and silentRefresh later read it with getItemAsync(key) and call login again.

### Evidence

- `mobile/context/AuthContext.tsx:21` — CREDENTIALS_KEY is ct_mobile_login_credentials, documented as email+password for silent re-login when the JWT expires.
- `mobile/context/AuthContext.tsx:112` — Stored value is JSON.stringify({ email, password }) of the staff password, not a refresh token.
- `mobile/lib/secureStorage.ts:5` — keychainAccessible is AFTER_FIRST_UNLOCK, which is backup-eligible on iOS; AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY is not used.
- `mobile/lib/secureStorage.ts:9` — secureGet calls getItemAsync(key) with no requireAuthentication options.
- `mobile/app.json:28` — android package com.caringtransitions.capture sets no allowBackup false and no backup/dataExtraction rules.
- `mobile/app.json:49` — plugins includes expo-secure-store as a string with no configureAndroidBackup object.

### Blockers

- OS-enforced sandbox and parent artifact promotion unavailable (empty allowlist env, no-network namespace, read-only target, trusted parent-side no-follow promotion); target code was not executed
- No android/ or ios/ trees; EAS merged AndroidManifest allowBackup plus SecureStore backup XML, and the shipped IPA keychain accessibility, are packaging facts
- Encrypted iCloud/Finder/Google/MDM backup restore of dummy ct_mobile_login_credentials onto a second dummy device or OS profile was not observed

### Validation plan

**Local (sandboxed dummy tenants only):** On an isolated simulator/emulator with a dummy tenant, sign in, then read the written item's iOS accessibility class and Android backup XML/EncryptedSharedPreferences binding. Restore an encrypted backup onto a second dummy device or profile and see whether the dummy password silently logs in. Do not use live staff accounts.

**Owner-observed deployment:** Owner inspects the EAS production APK/IPA AndroidManifest allowBackup and expo-secure-store backup rules, and the iOS keychain accessibility for com.caringtransitions.capture. Do not export or replay live staff passwords.

---

## 15. Catalog multipart ingest buffers entire parts in the shared API process with no photo byte cap

**Fingerprint:** `backend.app.upload_storage.save_uploaded_files.unbounded-multipart-buffer`

**Claimed root cause:** Multipart handlers treat UploadFile.read() as the size gate: photos never have a byte budget, and videos check MAX_VIDEO_BYTES only after the full buffer exists. There is no source-visible Starlette/FastAPI max body, per-request file-count cap for batch jobs, or streaming discard.

**Description:** Restricted clients and staff upload photos through POST /jobs/upload and POST /jobs/{id}/upload-files into save_uploaded_files, which await-reads each UploadFile fully, optionally runs Pillow, writes the bytes, and mirrors to R2. JWT, enforce_client_http, account_id, and assigned-sale checks gate who may call those routes, but they do not bound part size. catalog_limits defines MAX_VIDEO_BYTES and a 40-photo CTBids cap, but no photo-byte ceiling; the 40-file check runs only in single_lot_mode (and only after multipart parse). Lot video upload does have an 80 MiB cap, but only after the entire part is already in memory. FastAPI is constructed with default options (no request-size limit in source; only security-headers, CORS, and optional TrustedHost middleware). Concurrent saves use a semaphore of 12, so several large parts occupy RAM together on the single API process that also serves other tenants' dashboard and pool checkouts.

### Trace

- `entrypoint` `backend/app/main.py:5362` (create_job_from_upload): Authenticated client or catalogger posts files: list[UploadFile] with no length or byte Form constraint; CTBIDS_MAX_PHOTOS_PER_LOT applies only when single_lot_mode is true.
- `propagation` `backend/app/upload_storage.py:50` (save_uploaded_files._save_one): body = await uf.read() materializes the entire part. concurrency=12 gather runs many such reads. Pillow compress runs after this allocation; non-image names skip compress and keep raw bytes.
- `sink` `backend/app/upload_storage.py:90` (save_uploaded_files._save_one): target.write_bytes(body) plus mirror_local_file_to_r2 retain the object on local disk and operator-owned R2. The same process hosts other tenants' HTTP and QueuePool.

### Evidence

- `backend/app/catalog_limits.py:13` — MAX_VIDEO_BYTES is the only ingest byte constant; there is no MAX_PHOTO_BYTES. CTBIDS_MAX_PHOTOS_PER_LOT is a listing count, not a transfer-size cap.
- `backend/app/main.py:3825` — upload_lot_videos does raw = await file.read() then lot_video_size_error(len(raw)), so the advertised 80 MiB bound is after unbounded buffering.
- `backend/app/main.py:284` — app = FastAPI(title=settings.app_name) installs no max request size, body limit, or upload middleware.
- `backend/app/services/roles.py:95` — client_http_allowed includes POST /jobs/upload, so the unbounded buffer is reachable by the client role, not only owners.
- `frontend/app/dashboard/page.tsx:129` — UPLOAD_FILE_CHUNK = 48 is a browser timeout workaround, not an API cardinality or byte limit; additional chunks append to the same job.

### Blockers

- No OS-enforced sandbox is available (empty allowlist env, no-network namespace, read-only target, trusted parent-side no-follow promotion), so peak RSS and disk growth were not measured with a small oversized fixture.
- Railway/proxy/client_max_body_size and platform multipart limits are not declared in this repository; an upstream cap would change shared impact and must be owner-observed.
- Paid R2 put and Gemini spend from a huge accepted batch were not exercised (mocked-only local check required).

### Validation plan

**Local (sandboxed dummy tenants only):** With Gemini and R2 mocked and strict process memory/time limits, post one oversize JPEG and one oversize non-image part to save_uploaded_files / create_job_from_upload, and one oversize video to upload_lot_videos. Observe that photo parts are fully buffered with no 413, and that video rejection happens only after len(raw) is known. Repeat with a large files list while single_lot_mode is false. Do not stress a shared host beyond the fixture limit.

**Owner-observed deployment:** Owner-observed: record whether Railway/ingress enforces a request body ceiling in front of the API service, and whether that ceiling applies to multipart parts before uvicorn allocates them. Do not flood the production API.

---

## 16. FastAPI docs and OpenAPI remain enabled on the 0.0.0.0 API bind

**Fingerprint:** `cloud.ingress.fastapi-openapi-docs`

**Claimed root cause:** FastAPI() is created without docs_url, redoc_url, or openapi_url disabled, and no production overlay or ingress manifest in repo strips those paths before the public bind.

**Description:** The API app is constructed with FastAPI defaults, so /docs, /redoc, and /openapi.json are registered in development and production. Docker and Procfile bind uvicorn on 0.0.0.0, and DEPLOY.md publishes an API domain for /health. Those routes disclose the full management surface (auth, R2 migrate, admin delete, CT Seller, Facebook). Whether a Railway edge or other proxy currently blocks them is not in source and was not probed.

### Trace

- `entrypoint` `backend/app/main.py:284` (FastAPI constructor): app = FastAPI(title=settings.app_name) keeps default documentation and OpenAPI URLs for every environment.
- `propagation` `backend/app/config.py:209` (production_must_be_safe): Production startup checks SECRET_KEY, SQLite, and CORS_ORIGINS but does not disable docs or require TRUSTED_HOSTS.
- `sink` `infra/docker/Dockerfile.api:22` (uvicorn CMD): Container starts uvicorn app.main:app --host 0.0.0.0, so any attached public domain forwards to the default docs routes unless a proxy blocks them.

### Evidence

- `backend/app/main.py:284` — No docs_url=None, redoc_url=None, or openapi_url=None on the FastAPI constructor.
- `Procfile:3` — Nixpacks/Procfile path also runs uvicorn on 0.0.0.0 bound to $PORT.
- `DEPLOY.md:60` — Operators are told to generate a public API domain and to hit /health on that hostname.
- `backend/app/main.py:559` — TrustedHostMiddleware is added only when TRUSTED_HOSTS is non-empty; empty is the documented default.

### Blockers

- Live Railway ingress, private-networking flags, and any path filter in front of the API are not in this repo.
- This run must not fetch the public API /docs or /openapi.json.
- No sandbox was available to boot uvicorn locally and observe the default routes.

### Validation plan

**Local (sandboxed dummy tenants only):** In an isolated loopback sandbox, start app.main:app and confirm unauthenticated GET /docs, /redoc, and /openapi.json return 200 with the operation list. That only proves the application route, not live exposure.

**Owner-observed deployment:** Owner, from the Railway dashboard or an already-authorized admin path, notes whether the API service has a public domain and whether an edge rule denies /docs, /redoc, and /openapi.json. Do not probe the hostname from this agent.

---
