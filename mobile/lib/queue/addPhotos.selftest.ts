import { pickCatalogLotForDraft, queueAddPhotosIsLocal } from "./addPhotos";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const mapped = [
  {
    draftId: "1c86c230-a1c1-4433-b64e-5c2957e79027",
    lotId: "lot-1",
    sku: "LV101",
    photoCount: 8,
    title: "Lamp"
  }
];

assert(
  pickCatalogLotForDraft({
    draftId: "1c86c230-a1c1-4433-b64e-5c2957e79027",
    mapped
  })?.lotId === "lot-1",
  "maps draft id to catalog lot"
);

assert(
  pickCatalogLotForDraft({
    draftId: "other",
    cachedLotId: "lot-cached",
    mapped
  })?.lotId === "lot-cached",
  "uses cached lot id when the map misses"
);

assert(
  pickCatalogLotForDraft({
    draftId: "other",
    manualSku: "LV101",
    mapped: [],
    lotsBySku: [{ id: "lot-sku", sku: "lv101", photo_count: 3, auction_title: "Lamp" } as never]
  })?.lotId === "lot-sku",
  "falls back to a unique SKU match"
);

assert(
  pickCatalogLotForDraft({
    draftId: "other",
    manualSku: "LV101",
    mapped: [],
    lotsBySku: [
      { id: "a", sku: "LV101", photo_count: 1, auction_title: "A" } as never,
      { id: "b", sku: "LV101", photo_count: 1, auction_title: "B" } as never
    ]
  }) === null,
  "does not guess when two lots share a SKU"
);

assert(queueAddPhotosIsLocal("draft"), "draft stays on the phone");
assert(queueAddPhotosIsLocal("failed"), "failed stays on the phone");
assert(!queueAddPhotosIsLocal("uploaded"), "uploaded must append on the website");
assert(!queueAddPhotosIsLocal("uploading"), "uploading waits");

console.log("addPhotos.selftest ok");
