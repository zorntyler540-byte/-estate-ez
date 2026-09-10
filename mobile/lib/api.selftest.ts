import { isProxyBusyError, parseApiError } from "./apiError";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(parseApiError('{"detail":"Lot not found"}') === "Lot not found", "json detail");
assert(
  parseApiError("upstream error") === "The website was busy. Open this lot again — do not Resend.",
  "plain upstream"
);
assert(
  parseApiError("no healthy upstream") === "The website was busy. Open this lot again — do not Resend.",
  "envoy upstream"
);
assert(
  parseApiError('{"detail":"upstream error"}') ===
    "The website was busy. Open this lot again — do not Resend.",
  "json upstream"
);
assert(isProxyBusyError("upstream error") === true, "proxy flag");
assert(isProxyBusyError("Lot not found") === false, "real 404 is not proxy");

console.log("api selftest ok (6 cases)");
