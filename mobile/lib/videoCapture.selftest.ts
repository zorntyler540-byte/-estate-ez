import { captureModesFor } from "./clientCapture";
import {
  MAX_VIDEO_UPLOAD_BYTES,
  MICROPHONE_OFF_NOTE,
  VIDEO_BITRATE,
  VIDEO_CODEC,
  VIDEO_MAX_FILE_BYTES,
  microphoneRecordPlan,
  videoRecordOptions,
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

const iosRecord = videoRecordOptions("ios");
assert(iosRecord.codec === VIDEO_CODEC, "ios sets the codec so the bitrate cap applies");
assert(iosRecord.maxDuration === MAX_VIDEO_DURATION_SEC, "ios keeps the duration cap");
assert(iosRecord.maxFileSize === VIDEO_MAX_FILE_BYTES, "ios keeps the file cap");
const androidRecord = videoRecordOptions("android");
assert(androidRecord.codec === undefined, "android omits the ios-only codec");
assert(androidRecord.maxDuration === MAX_VIDEO_DURATION_SEC, "android keeps the duration cap");
assert(androidRecord.maxFileSize === VIDEO_MAX_FILE_BYTES, "android keeps the file cap");
assert(microphoneRecordPlan(true).mute === false && microphoneRecordPlan(true).note === null, "granted mic records sound");
const silent = microphoneRecordPlan(false);
assert(silent.mute === true && silent.note === MICROPHONE_OFF_NOTE, "denied mic stays silent and says so");

console.log("videoCapture selftest ok");
