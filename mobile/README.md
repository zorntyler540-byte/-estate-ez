# Caring Transitions — mobile (Expo)

Field capture app: **native camera**, **offline lot storage**, and **upload queue** to the existing FastAPI backend (`/jobs/upload` single-lot mode).

The web dashboard (`../frontend`) remains the catalog review and admin UI.

## Quick test on your iPhone (two paths)

### A. Expo Go (same day, no Apple build)

```powershell
cd mobile
copy .env.example .env   # if needed
# .env already points at Railway API in this repo
npm install
npx expo start
```

Install **Expo Go** from the App Store, scan the QR code.

### B. Real app install (Apple Developer — recommended for camera field test)

Uses EAS Build + ad hoc install (or TestFlight). From `mobile/`:

```powershell
npm install -g eas-cli
eas login
eas build:configure
eas device:create
eas build --platform ios --profile preview
```

1. `eas login` — Expo account (free).
2. `eas build:configure` — links this project on expo.dev (creates `projectId` in `app.json`).
3. `eas device:create` — register your iPhone (opens a URL/QR; follow prompts). New Apple accounts can take **24–72 hours** before a new UDID is usable.
4. `eas build --platform ios --profile preview` — sign in with **Apple ID** when asked so EAS can create certificates/profiles. When the build finishes, open the install URL on the registered iPhone.

**TestFlight** (team updates — preferred once smoke test works):

```powershell
cd C:\Users\User\Documents\CaringTransitions_AI\mobile
eas build --platform ios --profile production
```

When that finishes (accept App Store credentials / let EAS manage them):

```powershell
eas submit --platform ios --latest
```

Sign in with your **Apple Developer** Apple ID when prompted. After Apple processes the build (~10–15 min), open [App Store Connect](https://appstoreconnect.apple.com) → your app → **TestFlight**, add Internal Testers (or invite by email). Employees install the **TestFlight** app and accept the invite — no Developer Mode, no per-device rebuilds.

## Push a JS-only fix (EAS Update) — no new TestFlight build

Use this when the change is only JavaScript/TypeScript (Queue screen, sync logic, copy). The phones already have the native app from TestFlight; this just swaps the JS bundle.

You do **not** need Apple login for this. You do need your **Expo** login (the same account as `eas build`), **or** a Cursor/Cloud Agent with a valid `EXPO_TOKEN`.

### Cursor Cloud: give the agent the Expo token

The phone publish command is `eas update`. It authenticates with env var **`EXPO_TOKEN`**, not Apple, and not your Expo password.

1. Sign in at [expo.dev](https://expo.dev) as **`vtz1900`** (that is `expo.owner` in `app.json`).
2. Open [Access tokens](https://expo.dev/settings/access-tokens).
3. **Create token** (personal access token). Copy it once.
4. In Cursor, set / replace the secret named **`EXPO_TOKEN`** with that value (same name the agent already reads). Revoke the old token on Expo if Expo said it was invalid.
5. Do not paste the token into chat or commit it. Tell the agent “token is updated”.
6. A Cloud Agent that is **already running** keeps the token it started with. After you replace `EXPO_TOKEN`, start a **new** Cloud Agent so it boots with the new value. Then it can run `eas whoami` and `bash scripts/publish-eas-update.sh`.

```powershell
cd C:\Users\User\Documents\CaringTransitions_AI\mobile
npm install -g eas-cli
eas login
git pull
# ``eas.json`` build.env is NOT applied to ``eas update``. SDK 54 reads local ``.env``.
# Always set the Railway URL so a leftover localhost .env cannot ship to phones.
$env:EXPO_PUBLIC_API_BASE="https://caringtransitions-ai-production.up.railway.app"
eas update --channel production --message "Queue: review all photos in taken order"
```

1. `eas login` — browser or email/password for Expo (`vtz1900` / owner in `app.json`).
2. `eas update --channel production` — TestFlight builds listen to the **`production`** channel (see `eas.json`). Do **not** omit `EXPO_PUBLIC_API_BASE`; the release bundle also ignores localhost, but set the Railway URL anyway.
3. Wait until the command prints a URL on expo.dev. That means phones can download it.
4. On each iPhone: **fully quit** Estate-EZ Capture (swipe up from the app switcher) and open it again. Updates check on launch (`ON_LOAD` in `app.json`).

If a phone was installed from an **ad hoc / preview** build instead of TestFlight, use `--channel preview`.

**Do not use EAS Update** when you changed native code or added a native dependency (camera, sqlite, expo-updates itself). Those still need `eas build` + `eas submit` + a new TestFlight version.

## Setup (Windows)

**SDK 54** — matches the **App Store Expo Go** app. When you’re ready for newer SDKs / `eas go`, upgrade with `npx expo install expo@^55.0.0 --fix`.

```powershell
cd mobile
copy .env.example .env
# Edit .env — set EXPO_PUBLIC_API_BASE to your Railway API URL
npm install
npx expo start
```

Scan the QR code with **Expo Go** on your iPhone, or press `a` for Android emulator.

### API URL tips

| Where you run the app | API base |
|-----------------------|----------|
| Railway production | `https://your-api.up.railway.app` |
| Local backend on PC | `http://YOUR_LAN_IP:8000` (not `localhost` from a phone) |
| Android emulator | `http://10.0.2.2:8000` |

EAS **build** profiles bake `EXPO_PUBLIC_API_BASE` into the native binary (see `eas.json`). **EAS Update does not read that `env` block** — it uses local `.env` / the shell. Release JS (`__DEV__ === false`) ignores localhost and falls back to the Railway API in `mobile/lib/config.ts`. Local Expo Go (`__DEV__`) still uses `mobile/.env` / localhost.

## App flow (MVP scaffold)

1. **Sign in** — same email/password as web (`POST /auth/login`).
2. **Sale tab** — pick an open sale (`GET /sales`).
3. **Capture tab** — start lot session → native camera → **Snap** / **Done**.
4. **Queue tab** — **Queue for upload** on drafts → **Sync queued lots** when online.

Photos are stored under the app documents directory and indexed in SQLite until upload succeeds.

## Project layout

```
mobile/
  app/                 Expo Router screens
  context/AuthContext  Secure token + selected sale
  lib/api.ts           FastAPI client (login, sales, jobs/upload)
  lib/queue/db.ts      SQLite lot drafts + photo paths
  lib/queue/sync.ts    Upload + run job when online
```

## Next steps (not in scaffold)

- Tap-to-focus, optical zoom, burst capture polish
- Background sync when network returns
- CTBids photo cap enforcement
- Push notification when catalog job completes
