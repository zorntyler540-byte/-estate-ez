import { mapInBatches, QUEUE_PHOTO_LOAD_BATCH, QUEUE_VISIBLE_THUMBS } from "./loadQueueRows";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

async function main() {
  assert(QUEUE_PHOTO_LOAD_BATCH === 4, "batch size");
  assert(QUEUE_VISIBLE_THUMBS === 6, "visible thumbs");

  const got = await mapInBatches([1, 2, 3, 4, 5], 2, async (n) => n * 10);
  assert(JSON.stringify(got) === JSON.stringify([10, 20, 30, 40, 50]), "maps all items");

  const empty = await mapInBatches([], 4, async (n: number) => n);
  assert(empty.length === 0, "empty input");

  console.log("loadQueueRows selftest ok");
}

void main();
