import {
  coverUrlsEqual,
  lotListSignature,
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

console.log("lotsReload.selftest ok");
