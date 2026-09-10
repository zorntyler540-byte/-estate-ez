import {
  countPhotoJobsPending,
  draftKeepsSyncAlive,
  draftNeedsVideoAttach,
  videoUploadName
} from "./attachVideos";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(
  videoUploadName("AbC", "file:///lot-captures/x/1.MOV", 0) === "mobile-AbC-vid-01.mov",
  "mov name"
);
assert(
  videoUploadName("d1", "file:///lot-captures/x/clip.mp4", 2) === "mobile-d1-vid-03.mp4",
  "mp4 index"
);
assert(videoUploadName("d1", "file:///no-ext", 0) === "mobile-d1-vid-01.mp4", "default ext");

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
