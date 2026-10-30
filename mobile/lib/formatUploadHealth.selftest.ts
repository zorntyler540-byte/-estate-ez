import { formatUploadHealthSummary } from "./formatUploadHealth";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(formatUploadHealthSummary(null) === null, "null health");
assert(formatUploadHealthSummary({ cataloging_count: 0, stuck_count: 0 }) === null, "zeros");
assert(
  formatUploadHealthSummary({ cataloging_count: 3, stuck_count: 1 }) ===
    "3 cataloging, 1 stuck — open Jobs on the website",
  "both"
);
assert(formatUploadHealthSummary({ cataloging_count: 2, stuck_count: 0 }) === "2 still cataloging", "cataloging only");
assert(
  formatUploadHealthSummary({ cataloging_count: 0, stuck_count: 1 }) === "1 stuck — open Jobs on the website",
  "stuck only"
);

console.log("formatUploadHealth selftest ok (5 cases)");
