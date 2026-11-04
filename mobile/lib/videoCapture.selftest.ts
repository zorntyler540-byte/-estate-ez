import { captureModesFor } from "./clientCapture";
import {
  MAX_VIDEO_UPLOAD_BYTES,
  VIDEO_BITRATE,
  VIDEO_MAX_FILE_BYTES,
  videoSizeProblem
} from "./videoCapture";
import { MAX_VIDEO_DURATION_SEC } from "./mediaKind";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(VIDEO_MAX_FILE_BYTES < MAX_VIDEO_UPLOAD_BYTES, "recorder stops below the upload cap");
assert(
  (VIDEO_BITRATE / 8) * MAX_VIDEO_DURATION_SEC < VIDEO_MAX_FILE_BYTES,
  "a full-length clip at the bitrate cap fits"
);
assert(videoSizeProblem(25 * 1024 * 1024) === null, "normal clip ok");
assert(videoSizeProblem(MAX_VIDEO_UPLOAD_BYTES) === null, "exactly at cap ok");
assert(videoSizeProblem(MAX_VIDEO_UPLOAD_BYTES + 1)?.includes("shorter clip"), "over cap refused");
assert(videoSizeProblem(0)?.includes("empty"), "empty refused");
assert(videoSizeProblem(undefined)?.includes("empty"), "unknown size refused");

assert(captureModesFor("client").join() === "photo", "clients shoot stills only");
assert(captureModesFor("Client").join() === "photo", "role is case-insensitive");
assert(captureModesFor("owner").join() === "photo,video", "staff can record");
assert(captureModesFor(null).join() === "photo,video", "unknown role keeps video");

console.log("videoCapture selftest ok");
