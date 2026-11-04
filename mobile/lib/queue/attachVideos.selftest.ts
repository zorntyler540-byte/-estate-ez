import {
  countPhotoJobsPending,
  draftKeepsSyncAlive,
  draftNeedsVideoAttach
} from "./attachVideos";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(draftNeedsVideoAttach({ hasPendingLocalVideos: true, lotId: "lot-1" }) === true, "ready");
assert(draftNeedsVideoAttach({ hasPendingLocalVideos: true, lotId: null }) === false, "no lot");
assert(draftNeedsVideoAttach({ hasPendingLocalVideos: false, lotId: "lot-1" }) === false, "no clip");

assert(
  draftKeepsSyncAlive({ status: "uploaded", hasPendingLocalVideos: true }) === true,
  "unsent clip keeps Sync alive"
);
assert(
  draftKeepsSyncAlive({ status: "uploaded", errorMessage: null, hasPendingLocalVideos: false }) ===
    false,
  "clean uploaded does not keep Sync alive"
);
assert(
  draftKeepsSyncAlive({
    status: "uploaded",
    errorMessage: "Still cataloging on server…",
    hasPendingLocalVideos: false
  }) === true,
  "leftover cataloging notice still syncs"
);
assert(draftKeepsSyncAlive({ status: "queued" }) === true, "queued");
assert(countPhotoJobsPending({ status: "queued" }) === true, "photo job");
assert(countPhotoJobsPending({ status: "uploaded" }) === false, "uploaded is not a photo job");

console.log("attachVideos selftest ok (12 cases)");
