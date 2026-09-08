import { normalizeSaleName, selectedSaleNeedsSync } from "./selectedSale";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(normalizeSaleName("  Spring Sale  ") === "Spring Sale", "trim name");
assert(normalizeSaleName("   ") === null, "whitespace-only name");
assert(normalizeSaleName(undefined) === null, "missing name");

assert(
  selectedSaleNeedsSync("sale-1", "Spring", "sale-1", "Spring") === false,
  "same id+name is a no-op"
);
assert(
  selectedSaleNeedsSync("sale-1", "Spring", "sale-1", "  Spring  ") === false,
  "trimmed same name is a no-op"
);
assert(
  selectedSaleNeedsSync("sale-1", "Spring", "sale-1", "Spring Estate") === true,
  "name change syncs"
);
assert(
  selectedSaleNeedsSync("sale-1", "Spring", "sale-2", "Fall") === true,
  "id change syncs"
);
assert(
  selectedSaleNeedsSync(null, null, "sale-1", "Spring") === true,
  "first selection syncs"
);
assert(
  selectedSaleNeedsSync("sale-1", "Spring", null) === true,
  "clear selection syncs"
);
assert(
  selectedSaleNeedsSync(null, null, null) === false,
  "already cleared is a no-op"
);
assert(
  selectedSaleNeedsSync("sale-1", null, "sale-1", "Spring") === true,
  "backfill missing stored name"
);

console.log("selectedSale selftest ok (9 cases)");
