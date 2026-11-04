import { parseSalesCache, serializeSalesCache } from "./salesCache";
import type { Sale } from "./types";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const sale = (id: string, name: string, closed: string | null = null): Sale => ({
  id,
  account_id: "acct",
  user_id: "u",
  name,
  closed_at: closed,
  created_at: "2026-01-01T00:00:00Z"
});

const raw = serializeSalesCache("Staff@Example.com ", [sale("s1", "Smith Estate"), sale("s2", "Old", "2026-02-01")]);
assert(raw, "serialized");
const back = parseSalesCache(raw, "staff@example.com");
assert(back.length === 1 && back[0]!.id === "s1" && back[0]!.name === "Smith Estate", "open sales round-trip");
assert(parseSalesCache(raw, "other@example.com").length === 0, "another account never sees the cache");
assert(parseSalesCache(raw, null).length === 0, "no owner → nothing");
assert(serializeSalesCache(null, [sale("s1", "x")]) === null, "no owner → not cached");
assert(parseSalesCache("{not json", "staff@example.com").length === 0, "corrupt cache ignored");
assert(parseSalesCache(JSON.stringify({ owner: "staff@example.com", sales: [{ id: 3 }] }), "staff@example.com").length === 0, "bad rows dropped");
assert(parseSalesCache(null, "staff@example.com").length === 0, "missing cache");

console.log("salesCache selftest ok");
