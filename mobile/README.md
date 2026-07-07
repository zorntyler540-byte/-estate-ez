# Caring Transitions — mobile (Expo)

Field capture app: **native camera**, **offline lot storage**, and **upload queue** to the existing FastAPI backend (`/jobs/upload` single-lot mode).

The web dashboard (`../frontend`) remains the catalog review and admin UI.

## Setup (Windows)

**SDK 54** — matches the **App Store Expo Go** app (no Apple Developer account needed). When you’re ready for newer SDKs / `eas go`, upgrade with `npx expo install expo@^55.0.0 --fix`.

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

## EAS builds (no Mac required for iOS)

When ready for TestFlight:

1. `npm install -g eas-cli`
2. `eas login`
3. Apple Developer account ($99/yr)
4. `eas build --platform ios --profile preview`

See [Expo EAS Build](https://docs.expo.dev/build/introduction/).

## Next steps (not in scaffold)

- Tap-to-focus, optical zoom, burst capture polish
- Background sync when network returns
- CTBids photo cap enforcement
- Push notification when catalog job completes
