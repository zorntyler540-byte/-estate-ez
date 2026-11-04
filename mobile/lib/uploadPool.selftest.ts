import {
  LOTS_AT_ONCE,
  mapPool,
  runUploadPipeline,
  uploadConcurrencyFor
} from "./uploadPool";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  assert(uploadConcurrencyFor("wifi") === 3, "wifi = 3");
  assert(uploadConcurrencyFor("ethernet") === 3, "ethernet = 3");
  assert(uploadConcurrencyFor("cellular") === 1, "cellular = 1");
  assert(uploadConcurrencyFor("unknown") === 1, "unknown stays serial");
  assert(uploadConcurrencyFor(null) === 1, "null stays serial");
  assert(LOTS_AT_ONCE === 2, "two lots at once");

  // Never more than `concurrency` uploads in flight; prepare runs while uploads are pending.
  {
    const gates = new Map<number, ReturnType<typeof deferred>>();
    const events: string[] = [];
    let inflight = 0;
    let peak = 0;
    const run = runUploadPipeline({
      items: [1, 2, 3, 4, 5],
      concurrency: 3,
      prepare: async (n) => {
        events.push(`prep${n}`);
        return n;
      },
      upload: async (n) => {
        inflight += 1;
        peak = Math.max(peak, inflight);
        const g = deferred();
        gates.set(n, g);
        await g.promise;
        inflight -= 1;
      }
    });
    for (let i = 0; i < 5; i++) await tick();
    assert(peak === 3, `peak ${peak} should be 3`);
    assert(events.includes("prep4"), "4th photo compressed while 3 upload");
    assert(!gates.has(4), "4th upload waits for a slot");
    gates.get(2)!.resolve();
    for (let i = 0; i < 5; i++) await tick();
    assert(gates.has(4), "slot freed → 4th starts");
    for (const n of [1, 3, 4]) gates.get(n)!.resolve();
    for (let i = 0; i < 5; i++) await tick();
    gates.get(5)!.resolve();
    const result = await run;
    assert(result.uploaded === 5, "all uploaded");
    assert(peak === 3, "cap held");
  }

  // Serial when concurrency is 1 (cellular).
  {
    let inflight = 0;
    let peak = 0;
    await runUploadPipeline({
      items: [1, 2, 3],
      concurrency: 1,
      prepare: async (n) => n,
      upload: async () => {
        inflight += 1;
        peak = Math.max(peak, inflight);
        await tick();
        inflight -= 1;
      }
    });
    assert(peak === 1, "cellular serial");
  }

  // Skipped items (unreadable photo) are reported, not uploaded.
  {
    const sent: number[] = [];
    const result = await runUploadPipeline({
      items: [1, 2, 3],
      concurrency: 2,
      prepare: async (n) => (n === 2 ? null : n),
      upload: async (n) => {
        sent.push(n);
      }
    });
    assert(result.uploaded === 2 && result.skipped.join() === "2", "skip reported");
    assert(sent.sort().join() === "1,3", "skip not sent");
  }

  // First failure stops new uploads, lets in-flight finish, then throws.
  {
    const started: number[] = [];
    let finishedSlow = false;
    let threw: Error | null = null;
    try {
      await runUploadPipeline({
        items: [1, 2, 3, 4, 5],
        concurrency: 2,
        prepare: async (n) => n,
        upload: async (n) => {
          started.push(n);
          if (n === 1) {
            await new Promise((r) => setTimeout(r, 5));
            finishedSlow = true;
            return;
          }
          if (n === 2) throw new Error("boom");
        }
      });
    } catch (e) {
      threw = e as Error;
    }
    assert(threw?.message === "boom", "first error thrown");
    assert(finishedSlow, "in-flight upload allowed to finish");
    assert(!started.includes(4) && !started.includes(5), `no new uploads after failure: ${started}`);
  }

  // mapPool: bounded, runs every item, rethrows the first error at the end.
  {
    let inflight = 0;
    let peak = 0;
    const seen: number[] = [];
    let threw: Error | null = null;
    try {
      await mapPool([1, 2, 3, 4], 2, async (n) => {
        inflight += 1;
        peak = Math.max(peak, inflight);
        await tick();
        inflight -= 1;
        seen.push(n);
        if (n === 2) throw new Error("lot 2");
        return n;
      });
    } catch (e) {
      threw = e as Error;
    }
    assert(peak === 2, "two lots at once");
    assert(seen.sort().join() === "1,2,3,4", "every lot ran");
    assert(threw?.message === "lot 2", "error rethrown");
    const ok = await mapPool([3, 1, 2], 2, async (n) => n * 10);
    assert(ok.join() === "30,10,20", "results keep input order");
  }

  console.log("uploadPool selftest ok");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
