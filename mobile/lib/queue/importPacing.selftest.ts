import { isPacedMessage, PACED_PREFIX, RunPacer, type CatalogQuota } from "./importPacing";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const quota = (over: Partial<CatalogQuota> = {}): CatalogQuota => ({
  queued_jobs: 0,
  max_queued_jobs: 25,
  photos_today: 0,
  max_photos_per_day: 2000,
  photos_remaining_today: 2000,
  resets_at: "2026-11-06T00:00:00+00:00",
  ...over
});

// Older API (no /jobs/quota): never paced.
{
  const p = new RunPacer(null);
  for (let i = 0; i < 100; i++) assert(p.admit(40).ok, "unpaced");
  assert(!p.active, "inactive");
}

// 25 queued cap, 3 kept for others: a 50-lot import starts 22 now, the rest wait.
{
  const p = new RunPacer(quota());
  let ok = 0;
  let waiting = 0;
  for (let i = 0; i < 50; i++) {
    const d = p.admit(5);
    if (d.ok) ok += 1;
    else {
      waiting += 1;
      assert(isPacedMessage(d.reason) && /cataloging earlier lots/.test(d.reason), "queue reason");
    }
  }
  assert(ok === 22 && waiting === 28, `queue pacing ${ok}/${waiting}`);
}

// Jobs already queued by the website count against the budget.
{
  const p = new RunPacer(quota({ queued_jobs: 21 }));
  assert(p.admit(1).ok && !p.admit(1).ok, "only 1 slot left");
  p.release(1);
  assert(p.admit(1).ok, "released slot is reusable");
}

// Daily photo budget: a lot that does not fit waits for the reset, smaller ones still go.
{
  const p = new RunPacer(quota({ photos_remaining_today: 30 }));
  assert(p.admit(20).ok, "20 fits");
  const big = p.admit(20);
  assert(!big.ok && /daily photo limit/.test(big.reason), "daily reason");
  assert(p.admit(10).ok, "10 still fits");
}

assert(isPacedMessage(`${PACED_PREFIX} x`) && !isPacedMessage("Paused — offline"), "prefix");

console.log("importPacing selftest ok");
