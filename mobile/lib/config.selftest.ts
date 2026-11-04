import { isLocalApiBase, resolveApiBase } from "./config";

const RAILWAY = "https://caringtransitions-ai-production.up.railway.app";

const ok: Array<{ env?: string; isDev: boolean; want: string; label: string }> = [
  { env: undefined, isDev: true, want: "http://localhost:8000", label: "dev default" },
  { env: "http://localhost:8000", isDev: true, want: "http://localhost:8000", label: "dev keeps local .env" },
  { env: "http://192.168.1.20:8000", isDev: true, want: "http://192.168.1.20:8000", label: "dev keeps LAN URL" },
  { env: `${RAILWAY}/`, isDev: false, want: RAILWAY, label: "release keeps explicit Railway URL" }
];

const failClosed: Array<{ env?: string; label: string; match: RegExp }> = [
  { env: undefined, label: "release missing env", match: /EXPO_PUBLIC_API_BASE is not set/ },
  { env: "http://localhost:8000", label: "release localhost .env", match: /points at this device/ },
  { env: "http://127.0.0.1:8000/", label: "release 127.0.0.1", match: /points at this device/ },
  { env: "http://10.0.2.2:8000", label: "release Android emulator host", match: /points at this device/ },
  { env: "http://api.example.com", label: "release plain http", match: /must start with https/ },
  { env: "railway", label: "release invalid URL", match: /not a valid URL/ }
];

for (const c of ok) {
  const got = resolveApiBase(c.env, c.isDev);
  if (got !== c.want) throw new Error(`${c.label}: ${got} !== ${c.want}`);
}

for (const c of failClosed) {
  let message = "";
  try {
    resolveApiBase(c.env, false);
  } catch (e) {
    message = e instanceof Error ? e.message : String(e);
  }
  if (!c.match.test(message)) throw new Error(`${c.label}: expected ${c.match}, got "${message}"`);
}

if (!isLocalApiBase("http://localhost:8000")) throw new Error("localhost should be local");
if (isLocalApiBase(RAILWAY)) throw new Error("Railway URL should not be local");

console.log(`config selftest ok (${ok.length + failClosed.length} cases)`);
