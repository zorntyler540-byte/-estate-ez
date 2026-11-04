import {
  clipUploadName,
  draftIdFromPreparedEntry,
  photoUploadName,
  planUploadNames,
  preparedDirName,
  uploadNameSeq,
  videoExtension
} from "./uploadNames";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const D = "1c86c230-a1c1-4433-b64e-5c2957e79027";

assert(photoUploadName("mobile", D, 1) === `mobile-${D}-01.jpg`, "photo name");
assert(photoUploadName("append", D, 12) === `append-${D}-12.jpg`, "append name");
assert(clipUploadName("mobile", D, 2, "file:///x/1.MOV") === `mobile-${D}-vid-02.mov`, "clip mov");
assert(clipUploadName("append", D, 1, "file:///x/noext") === `append-${D}-vid-01.mp4`, "clip default");
assert(videoExtension("a.webm?x=1") === ".webm", "ext with query");

assert(uploadNameSeq(`mobile-${D}-07.jpg`, "image") === 7, "seq image");
assert(uploadNameSeq(`mobile-${D}-vid-03.mp4`, "video") === 3, "seq video");
assert(uploadNameSeq(`mobile-${D}-vid-03.mp4`, "image") === null, "clip is not an image seq");
assert(uploadNameSeq("IMG_1234.HEIC", "image") === null, "foreign name");
assert(uploadNameSeq(null, "image") === null, "null");

// Fresh lot: names follow capture order, photos and clips numbered separately.
{
  const names = planUploadNames("mobile", D, [
    { id: "a", kind: "image" },
    { id: "v", kind: "video", fileUri: "file:///c/1.mov" },
    { id: "b", kind: "image" }
  ]);
  assert(names.get("a") === `mobile-${D}-01.jpg`, "a=01");
  assert(names.get("b") === `mobile-${D}-02.jpg`, "b=02");
  assert(names.get("v") === `mobile-${D}-vid-01.mov`, "v=vid-01");
}

// After a delete, existing names stay and new photos never reuse a number.
{
  const names = planUploadNames("mobile", D, [
    { id: "a", kind: "image", uploadName: `mobile-${D}-01.jpg` },
    { id: "c", kind: "image", uploadName: `mobile-${D}-03.jpg` },
    { id: "d", kind: "image" },
    { id: "v", kind: "video", uploadName: `mobile-${D}-vid-02.mp4` },
    { id: "w", kind: "video" }
  ]);
  assert(names.size === 2, "only unnamed rows get names");
  assert(names.get("d") === `mobile-${D}-04.jpg`, "next above max, not the deleted 02");
  assert(names.get("w") === `mobile-${D}-vid-03.mp4`, "clip next above max");
}

assert(preparedDirName(D) === `lot-prepared/${D}/`, "prepared dir");
assert(draftIdFromPreparedEntry(`${D}/`) === D, "prepared entry id");
assert(draftIdFromPreparedEntry("../evil") === null, "reject odd entries");

console.log("uploadNames selftest ok");
