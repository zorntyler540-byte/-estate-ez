import { PRODUCTION_API_BASE, isLocalApiBase, resolveApiBase } from "./api";

const cases: Array<{ env?: string; nodeEnv: string; want: string; label: string }> = [
  { env: undefined, nodeEnv: "development", want: "http://localhost:8000", label: "dev default" },
  {
    env: "http://localhost:8000",
    nodeEnv: "development",
    want: "http://localhost:8000",
    label: "dev keeps env"
  },
  { env: undefined, nodeEnv: "production", want: PRODUCTION_API_BASE, label: "prod empty env" },
  {
    env: "http://localhost:8000",
    nodeEnv: "production",
    want: PRODUCTION_API_BASE,
    label: "prod ignores localhost env"
  },
  {
    env: "https://caringtransitions-ai-production.up.railway.app/",
    nodeEnv: "production",
    want: PRODUCTION_API_BASE,
    label: "prod keeps Railway URL"
  }
];

for (const c of cases) {
  const got = resolveApiBase(c.env, c.nodeEnv);
  if (got !== c.want) {
    throw new Error(`${c.label}: ${got} !== ${c.want}`);
  }
}

if (!isLocalApiBase("http://127.0.0.1:8000")) throw new Error("127.0.0.1 should be local");
if (isLocalApiBase(PRODUCTION_API_BASE)) throw new Error("Railway URL should not be local");

console.log(`apiBase selftest ok (${cases.length} cases)`);
