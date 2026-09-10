import { mapInBatches, QUEUE_OPEN_PHOTO_URIS, queueRowsFromDrafts } from "./loadQueueRows";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

async function main() {
  assert(QUEUE_OPEN_PHOTO_URIS.length === 0, "open Queue with zero photo URIs");

  const rows = queueRowsFromDrafts([
    { id: "a", photo_count: 40 },
    { id: "b", photo_count: 12 }
  ]);
  assert(rows.length === 2, "keeps every draft");
  assert(rows[0]!.photoUris.length === 0, "no URIs on first row");
  assert(rows[1]!.photoUris.length === 0, "no URIs on second row");
  assert(rows[0]!.photoUris === QUEUE_OPEN_PHOTO_URIS, "shared empty array");

  const got = await mapInBatches([1, 2, 3, 4, 5], 2, async (n) => n * 10);
  assert(JSON.stringify(got) === JSON.stringify([10, 20, 30, 40, 50]), "maps all items");

  const empty = await mapInBatches([], 4, async (n: number) => n);
  assert(empty.length === 0, "empty input");

  console.log("loadQueueRows selftest ok");
}

void main();
