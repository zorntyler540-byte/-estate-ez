import { isLocalApiBase, resolveApiBase } from "./api";

const RAILWAY = "https://caringtransitions-ai-production.up.railway.app";

const ok: Array<{ env?: string; nodeEnv: string; want: string; label: string }> = [
  { env: undefined, nodeEnv: "development", want: "http://localhost:8000", label: "dev default" },
  { env: "http://localhost:8000", nodeEnv: "development", want: "http://localhost:8000", label: "dev keeps env" },
  { env: `${RAILWAY}/`, nodeEnv: "production", want: RAILWAY, label: "prod keeps explicit Railway URL" },
  { env: "http://localhost:8000", nodeEnv: "production", want: "http://localhost:8000", label: "local prod build" }
];

const failClosed: Array<{ env?: string; label: string; match: RegExp }> = [
  { env: undefined, label: "prod missing env", match: /NEXT_PUBLIC_API_BASE is not set/ },
  { env: "   ", label: "prod blank env", match: /NEXT_PUBLIC_API_BASE is not set/ },
  { env: "not a url", label: "prod invalid URL", match: /not a valid URL/ },
  { env: "http://api.example.com", label: "prod plain http", match: /must start with https/ },
  { env: "ftp://api.example.com", label: "prod wrong scheme", match: /must start with https/ }
];

for (const c of ok) {
  const got = resolveApiBase(c.env, c.nodeEnv);
  if (got !== c.want) throw new Error(`${c.label}: ${got} !== ${c.want}`);
}

for (const c of failClosed) {
  let message = "";
  try {
    resolveApiBase(c.env, "production");
  } catch (e) {
    message = e instanceof Error ? e.message : String(e);
  }
  if (!c.match.test(message)) throw new Error(`${c.label}: expected ${c.match}, got "${message}"`);
}

if (!isLocalApiBase("http://127.0.0.1:8000")) throw new Error("127.0.0.1 should be local");
if (isLocalApiBase(RAILWAY)) throw new Error("Railway URL should not be local");

console.log(`apiBase selftest ok (${ok.length + failClosed.length} cases)`);
