import assert from "node:assert/strict";
import test from "node:test";
import {
  CATALOG_POLL_ACTIVE_MS,
  CATALOG_POLL_IDLE_MS,
  catalogPollIntervalMs,
  catalogStatusHasActive,
  catalogStatusPath,
} from "./catalogPoll.ts";

test("pauses polling while the tab is hidden", () => {
  assert.equal(catalogPollIntervalMs({ hidden: true, hasActive: true }), null);
  assert.equal(catalogPollIntervalMs({ hidden: true, hasActive: false }), null);
});

test("polls about every 6s when any sale is cataloging or queued", () => {
  assert.equal(catalogPollIntervalMs({ hidden: false, hasActive: true }), CATALOG_POLL_ACTIVE_MS);
  assert.ok(CATALOG_POLL_ACTIVE_MS >= 5000 && CATALOG_POLL_ACTIVE_MS <= 7000);
});

test("backs off to 30–60s when nothing is active", () => {
  assert.equal(catalogPollIntervalMs({ hidden: false, hasActive: false }), CATALOG_POLL_IDLE_MS);
  assert.ok(CATALOG_POLL_IDLE_MS >= 30000 && CATALOG_POLL_IDLE_MS <= 60000);
});

test("account-wide status is active only for receiving/queued/processing", () => {
  assert.equal(
    catalogStatusHasActive([{ sale_id: "a", receiving: 0, queued: 0, processing: 0, stuck_count: 2 }]),
    false
  );
  assert.equal(
    catalogStatusHasActive([{ sale_id: "a", receiving: 0, queued: 1, processing: 0, stuck_count: 0 }]),
    true
  );
  assert.equal(
    catalogStatusHasActive([
      { sale_id: "idle", receiving: 0, queued: 0, processing: 0, stuck_count: 0 },
      { sale_id: "busy", receiving: 0, queued: 0, processing: 1, stuck_count: 0 }
    ]),
    true
  );
});

test("catalog-status path is one request, optionally scoped to the open sale", () => {
  assert.equal(catalogStatusPath(""), "/jobs/catalog-status");
  assert.equal(catalogStatusPath("sale 1"), "/jobs/catalog-status?sale_id=sale%201");
});
