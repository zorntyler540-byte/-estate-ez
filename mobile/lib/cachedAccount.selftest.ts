import { accountLock, parseCachedAccount, serializeAccount } from "./cachedAccount";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const raw = serializeAccount({
  email: " Client@Example.com ",
  role: "client",
  assigned_sale_id: "sale-9",
  assigned_sale_name: "Maple St"
});
const back = parseCachedAccount(raw, "client@example.com");
assert(back?.role === "client", "round-trips role");
assert(back?.assigned_sale_id === "sale-9", "round-trips sale lock");
assert(back?.assigned_sale_name === "Maple St", "round-trips sale name");

assert(parseCachedAccount(raw, "someone-else@example.com") === null, "never applies another user's cache");
assert(parseCachedAccount(raw, null)?.role === "client", "token-only session still uses the cache");
assert(parseCachedAccount(null) === null, "missing cache");
assert(parseCachedAccount("{not json") === null, "malformed cache");
assert(parseCachedAccount(JSON.stringify({ email: "a@b.c" })) === null, "cache without a role is ignored");

const locked = accountLock(back!);
assert(locked.kind === "client-sale" && locked.saleId === "sale-9", "client with a sale is locked to it");
assert(accountLock({ role: "CLIENT" }).kind === "client-no-sale", "client without a sale clears the sale");
assert(accountLock({ role: "owner", assigned_sale_id: "x" }).kind === "staff", "staff are never sale-locked");
assert(accountLock({ role: null }).kind === "staff", "unknown role is staff (server decides on next /auth/me)");

console.log("cachedAccount selftest ok (12 cases)");
