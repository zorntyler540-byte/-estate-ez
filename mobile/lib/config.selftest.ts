import { PRODUCTION_API_BASE, isLocalApiBase, resolveApiBase } from "./config";

const cases: Array<{ env?: string; isDev: boolean; want: string; label: string }> = [
  { env: undefined, isDev: true, want: "http://localhost:8000", label: "dev default" },
  {
    env: "http://localhost:8000",
    isDev: true,
    want: "http://localhost:8000",
    label: "dev keeps local .env"
  },
  { env: undefined, isDev: false, want: PRODUCTION_API_BASE, label: "release empty env" },
  {
    env: "http://localhost:8000",
    isDev: false,
    want: PRODUCTION_API_BASE,
    label: "release ignores local .env localhost"
  },
  {
    env: "http://127.0.0.1:8000/",
    isDev: false,
    want: PRODUCTION_API_BASE,
    label: "release ignores 127.0.0.1"
  },
  {
    env: "https://caringtransitions-ai-production.up.railway.app/",
    isDev: false,
    want: PRODUCTION_API_BASE,
    label: "release keeps Railway URL"
  }
];

for (const c of cases) {
  const got = resolveApiBase(c.env, c.isDev);
  if (got !== c.want) {
    throw new Error(`${c.label}: ${got} !== ${c.want}`);
  }
}

if (!isLocalApiBase("http://localhost:8000")) throw new Error("localhost should be local");
if (isLocalApiBase(PRODUCTION_API_BASE)) throw new Error("Railway URL should not be local");

console.log(`config selftest ok (${cases.length} cases)`);
