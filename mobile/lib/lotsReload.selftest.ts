import {
  coverUrlsEqual,
  lotDetailSnapshot,
  lotIdsWithChangedPhotoCount,
  lotListSignature,
  lotRowsUnchanged,
  mediaIdsKey,
  mergeCoverUrls,
  saleNameNeedsUpdate,
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

console.log("lotsReload.selftest ok");
