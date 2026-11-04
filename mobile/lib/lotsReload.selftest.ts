import {
  coverUrlsEqual,
  fetchAllLotPages,
  lotDetailSnapshot,
  lotIdsWithChangedPhotoCount,
  lotListSignature,
  lotRowsUnchanged,
  mediaIdsKey,
  mergeCoverUrls,
  queueRowsUnchanged,
  saleNameNeedsUpdate,
  saleRowsUnchanged,
  selectedSaleUnchanged,
  shouldRefreshCoverUrls,
  shouldSkipLotsFocusReload
} from "./lotsReload";

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

assert(shouldSkipLotsFocusReload(1000, 2000, true) === true, "skip bounce reload when lots exist");
assert(shouldSkipLotsFocusReload(1000, 4000, true) === false, "reload after interval");
assert(shouldSkipLotsFocusReload(1000, 1200, false) === false, "always load when empty");
assert(shouldSkipLotsFocusReload(0, 500, true) === false, "first load is not a bounce");

assert(lotListSignature([{ id: "a", photo_count: 2 }, { id: "b", photo_count: 0 }]) === "a:2,b:0", "signature");
assert(lotListSignature([]) === "", "empty signature");

assert(coverUrlsEqual({ a: "1" }, { a: "1" }) === true, "same covers");
assert(coverUrlsEqual({ a: "1" }, { a: "2" }) === false, "new signed url");
assert(coverUrlsEqual({ a: "1" }, { a: "1", b: "2" }) === false, "added cover");

assert(shouldRefreshCoverUrls("a:1", "a:1", 3) === false, "keep covers when list unchanged");
assert(shouldRefreshCoverUrls("a:1", "a:2", 3) === true, "refresh when photo count changes");
assert(shouldRefreshCoverUrls("", "a:1", 0) === true, "fetch covers on first fill");
assert(shouldRefreshCoverUrls("a:1", "a:1", 0) === true, "fetch covers if thumbs missing");

assert(saleNameNeedsUpdate("Saturday Sale", "Saturday Sale") === false, "same name");
assert(saleNameNeedsUpdate(" Saturday Sale ", "Saturday Sale") === false, "trim match");
assert(saleNameNeedsUpdate("Saturday Sale", "Saturday Sale #2") === true, "name changed");
assert(saleNameNeedsUpdate(null, "Saturday Sale") === true, "missing stored name");

assert(
  lotRowsUnchanged(
    [{ id: "a", photo_count: 2, auction_title: "Lamp" }],
    [{ id: "a", photo_count: 2, auction_title: "Lamp" }]
  ) === true,
  "same painted row"
);
assert(
  lotRowsUnchanged(
    [{ id: "a", photo_count: 2, auction_title: "Lamp" }],
    [{ id: "a", photo_count: 3, auction_title: "Lamp" }]
  ) === false,
  "photo count change is a real update"
);
assert(
  lotRowsUnchanged(
    [{ id: "a", photo_count: 2, video_count: 0, auction_title: "Lamp" }],
    [{ id: "a", photo_count: 2, video_count: 1, auction_title: "Lamp" }]
  ) === false,
  "video count change is a real update"
);

const changed = lotIdsWithChangedPhotoCount("a:2,b:1", "a:3,b:1");
assert(changed.has("a") && !changed.has("b"), "only the lot that gained photos");

const merged = mergeCoverUrls({ a: "old-a", b: "old-b" }, { a: "new-a", b: "new-b" }, new Set(["a"]));
assert(merged.a === "new-a" && merged.b === "old-b", "keep stable thumbs after add photos");
{
  const prev = { a: "old-a", b: "old-b" };
  assert(mergeCoverUrls(prev, { a: "new-a", b: "new-b" }, new Set()) === prev, "reuse prev map when all kept");
}

assert(
  lotDetailSnapshot({ id: "a", photo_count: 2, auction_title: "Lamp", condition_text: "Good" }) ===
    lotDetailSnapshot({ id: "a", photo_count: 2, auction_title: "Lamp", condition_text: "Good" }),
  "same lot details snapshot"
);
assert(
  lotDetailSnapshot({ id: "a", photo_count: 2, auction_title: "Lamp" }) !==
    lotDetailSnapshot({ id: "a", photo_count: 3, auction_title: "Lamp" }),
  "details snapshot changes with photo count"
);
assert(mediaIdsKey([{ id: "p1" }, { id: "p2" }]) === "p1,p2", "photo ids key");
assert(mediaIdsKey([{ id: "p1" }]) !== mediaIdsKey([{ id: "p1" }, { id: "p2" }]), "new photo is a change");

assert(selectedSaleUnchanged("s1", "Saturday", "s1", "Saturday") === true, "same selected sale");
assert(selectedSaleUnchanged("s1", " Saturday ", "s1", "Saturday") === true, "trim selected sale");
assert(selectedSaleUnchanged("s1", "Saturday", "s1", "Sunday") === false, "sale name changed");
assert(selectedSaleUnchanged("s1", "Saturday", "s2", "Saturday") === false, "sale id changed");

assert(
  saleRowsUnchanged(
    [{ id: "s1", name: "A" }],
    [{ id: "s1", name: "A" }]
  ) === true,
  "same sale rows"
);
assert(
  saleRowsUnchanged(
    [{ id: "s1", name: "A" }],
    [{ id: "s1", name: "B" }]
  ) === false,
  "renamed sale row"
);

assert(
  queueRowsUnchanged(
    [{ id: "d1", status: "uploaded", photoUris: ["a"] }],
    [{ id: "d1", status: "uploaded", photoUris: ["a"] }]
  ) === true,
  "same queue row"
);
assert(
  queueRowsUnchanged(
    [{ id: "d1", status: "uploading", photoUris: ["a"] }],
    [{ id: "d1", status: "uploaded", photoUris: ["a"] }]
  ) === false,
  "queue status change"
);

void (async () => {
  const all = Array.from({ length: 1203 }, (_, i) => ({ id: `lot-${i}` }));
  const offsets: number[] = [];
  const full = await fetchAllLotPages(async (offset, limit) => {
    offsets.push(offset);
    return all.slice(offset, offset + limit);
  });
  assert(full.rows.length === 1203 && !full.capped, "reads past the 500-row API cap");
  assert(offsets.join(",") === "0,500,1000", "pages by offset until a short page");

  const exact = await fetchAllLotPages(async (offset, limit) => all.slice(0, 1000).slice(offset, offset + limit));
  assert(exact.rows.length === 1000 && !exact.capped, "an empty last page ends the read");

  const capped = await fetchAllLotPages(async (offset, limit) => all.slice(offset, offset + limit), {
    pageSize: 100,
    maxPages: 3
  });
  assert(capped.rows.length === 300 && capped.capped, "stops at maxPages and reports it");

  const shifted = await fetchAllLotPages(
    async (offset) => (offset === 0 ? [{ id: "a" }, { id: "b" }] : [{ id: "b" }]),
    { pageSize: 2 }
  );
  assert(shifted.rows.map((r) => r.id).join(",") === "a,b", "a row repeated across pages is kept once");

  console.log("lotsReload.selftest ok");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
