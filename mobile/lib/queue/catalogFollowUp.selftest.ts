import {
  CATALOG_FOLLOWUP_GIVE_UP_MS,
  CATALOG_FOLLOWUP_MAX_MS,
  CATALOG_FOLLOWUP_MS,
  advanceFollowUp,
  followUpDelayMs,
  followUpNeedsSync,
  planFollowUp,
  shouldFollowUpCatalogCheck,
  stopFollowUp
} from "./catalogFollowUp";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

let cases = 0;
function check(cond: unknown, msg: string) {
  assert(cond, msg);
  cases += 1;
}

check(CATALOG_FOLLOWUP_MS === 8_000, "first follow-up is 8s");
check(CATALOG_FOLLOWUP_MAX_MS === 300_000, "backoff caps at 5 min");
check(CATALOG_FOLLOWUP_GIVE_UP_MS === 1_800_000, "gives up after 30 min");
check(shouldFollowUpCatalogCheck({ leftoverCatalogingNotice: true }), "leftover cataloging notice follows up");
check(shouldFollowUpCatalogCheck({ hasPendingLocalVideos: true }), "unsent clip follows up");
check(!shouldFollowUpCatalogCheck({}), "clean queue does not poll");

check(followUpDelayMs(0) === 8_000, "attempt 0 = 8s");
check(followUpDelayMs(1) === 16_000, "attempt 1 = 16s");
check(followUpDelayMs(5) === 256_000, "attempt 5 = 256s");
check(followUpDelayMs(6) === 300_000, "attempt 6 caps at 5 min");
check(followUpDelayMs(500) === 300_000, "huge attempt stays capped");

// Walk the schedule: delays grow, then stop around 30 min.
{
  const t0 = 1_000_000;
  let now = t0;
  let { plan, delayMs } = planFollowUp(null, ["B", "a"], now);
  check(plan?.key === "a,b" && delayMs === 8_000, "new set starts at 8s");
  const delays: number[] = [];
  let guard = 0;
  while (delayMs != null && guard++ < 100) {
    delays.push(delayMs);
    now += delayMs;
    plan = advanceFollowUp(plan!);
    ({ plan, delayMs } = planFollowUp(plan, ["a", "b"], now));
  }
  check(guard < 100, "follow-up stops on its own");
  check(plan?.stopped === true, "plan marks itself stopped");
  check(now - t0 === CATALOG_FOLLOWUP_GIVE_UP_MS, "last check lands at the 30 min mark");
  check(delays.slice(0, 7).join(",") === "8000,16000,32000,64000,128000,256000,300000", "exponential then capped");
  check(delays.length <= 12, `bounded number of checks (${delays.length})`);

  const again = planFollowUp(plan, ["a", "b"], now + 60_000);
  check(again.delayMs === null, "stopped plan stays stopped for the same lots");
  const fresh = planFollowUp(plan, ["a", "b", "c"], now + 60_000);
  check(fresh.delayMs === 8_000 && fresh.plan?.attempt === 0, "a different set of lots starts a fresh window");
}

check(planFollowUp(null, [], 0).delayMs === null, "nothing to follow up = no timer");
{
  const { plan } = planFollowUp(null, ["a"], 0);
  check(planFollowUp(stopFollowUp(plan!), ["a"], 10).delayMs === null, "permanent error stop sticks");
}

// Whether the status answer needs a full Sync.
const d = (id: string, extra: Record<string, unknown> = {}) => ({ id, ...extra });
check(
  !followUpNeedsSync([d("a")], new Map([["a", { job_status: "processing" }]])),
  "still processing just waits"
);
check(
  !followUpNeedsSync([d("a")], new Map([["a", { job_status: "queued" }]])),
  "still queued just waits"
);
check(
  followUpNeedsSync([d("a")], new Map([["a", { job_status: "completed", cataloged: true }]])),
  "completed runs Sync (archive / clear notice)"
);
check(
  followUpNeedsSync([d("a", { has_pending_clip: true })], new Map([["a", { job_status: "processing", lot_id: "lot-1" }]])),
  "lot id for a waiting clip runs Sync"
);
check(followUpNeedsSync([d("a")], new Map([["a", { job_status: "failed" }]])), "failed job runs Sync");
check(followUpNeedsSync([d("a")], new Map([["a", { job_status: null }]])), "job gone runs Sync");
check(followUpNeedsSync([d("a")], new Map()), "missing row runs Sync");

console.log(`catalogFollowUp selftest ok (${cases} cases)`);
