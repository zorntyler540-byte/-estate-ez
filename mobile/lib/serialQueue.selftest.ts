import { SerialQueue } from "./serialQueue";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

void (async () => {
  const q = new SerialQueue();
  const order: string[] = [];
  let running = 0;
  let maxRunning = 0;
  const job = (name: string, ms: number) => async () => {
    running += 1;
    maxRunning = Math.max(maxRunning, running);
    await sleep(ms);
    order.push(name);
    running -= 1;
    return name;
  };

  const a = q.enqueue(job("a", 20));
  const b = q.enqueue(job("b", 1));
  assert(q.size === 2, "size counts queued jobs");
  assert((await a) === "a" && (await b) === "b", "each job resolves with its own result");
  assert(order.join(",") === "a,b", "jobs run in enqueue order even when a later one is faster");
  assert(maxRunning === 1, "never two jobs at once");

  const failing = q.enqueue(async () => {
    throw new Error("bake failed");
  });
  const after = q.enqueue(job("after", 1));
  let threw = false;
  try {
    await failing;
  } catch {
    threw = true;
  }
  assert(threw, "a failing job rejects to its caller");
  assert((await after) === "after", "a failing job does not stall the next one");

  q.enqueue(job("c", 5));
  await q.idle();
  assert(order[order.length - 1] === "c", "idle waits for queued work");
  assert(q.size === 0, "size drops to zero when idle");

  console.log("serialQueue selftest ok (8 cases)");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
