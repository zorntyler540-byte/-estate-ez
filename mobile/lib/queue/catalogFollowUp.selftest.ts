import { CATALOG_FOLLOWUP_MS, shouldFollowUpCatalogCheck } from "./catalogFollowUp";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(CATALOG_FOLLOWUP_MS === 8_000, "follow-up is 8s while a job is active");
assert(
  shouldFollowUpCatalogCheck({ leftoverCatalogingNotice: true }) === true,
  "leftover cataloging notice follows up"
);
assert(
  shouldFollowUpCatalogCheck({ hasPendingLocalVideos: true }) === true,
  "unsent clip follows up so archive can finish"
);
assert(
  shouldFollowUpCatalogCheck({ leftoverCatalogingNotice: false, hasPendingLocalVideos: false }) ===
    false,
  "clean queue does not poll"
);

console.log("catalogFollowUp selftest ok (4 cases)");
