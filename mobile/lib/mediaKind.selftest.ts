import {
  isVideoDraftRow,
  isVideoUri,
  mediaKindFromName,
  shouldPrepareAsPhoto,
  splitDraftMedia,
  splitMediaUris,
  videoMimeType
} from "./mediaKind";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(mediaKindFromName("clip.mp4") === "video", "mp4 is video");
assert(mediaKindFromName("clip.MOV") === "video", "MOV is video");
assert(mediaKindFromName("hero.jpg") === "image", "jpg is image");
assert(isVideoUri("file:///lot-captures/x/1.mov") === true, "file uri mov");
assert(isVideoUri("file:///lot-captures/x/1.jpg") === false, "file uri jpg");
assert(videoMimeType("a.mov") === "video/quicktime", "mov mime");
const split = splitMediaUris(["a.jpg", "b.mp4", "c.png"]);
assert(split.images.join(",") === "a.jpg,c.png", "split images");
assert(split.videos.join(",") === "b.mp4", "split videos");

assert(
  isVideoDraftRow({ media_kind: "image", file_uri: "file:///lot-captures/x/1.mp4" }) === true,
  "mp4 uri is a clip even when media_kind was left as image"
);
assert(
  isVideoDraftRow({ media_kind: "video", file_uri: "file:///lot-captures/x/1.jpg" }) === true,
  "marked video stays a clip"
);
assert(
  isVideoDraftRow({ media_kind: "image", file_uri: "file:///lot-captures/x/1.jpg" }) === false,
  "still photo"
);
const draftSplit = splitDraftMedia([
  { id: "p1", media_kind: "image", file_uri: "file:///a.jpg" },
  { id: "v1", media_kind: "image", file_uri: "file:///b.mov" },
  { id: "p2", media_kind: "image", file_uri: "file:///c.jpg" },
  { id: "v2", media_kind: "video", file_uri: "file:///d.jpg" }
]);
assert(draftSplit.images.map((r) => r.id).join(",") === "p1,p2", "draft stills");
assert(draftSplit.videos.map((r) => r.id).join(",") === "v1,v2", "draft clips");
assert(shouldPrepareAsPhoto({ uri: "file:///a.jpg", name: "mobile-d-01.jpg" }) === true, "prepare still");
assert(
  shouldPrepareAsPhoto({ uri: "file:///lot-captures/x/1.mp4", name: "mobile-d-01.jpg" }) === false,
  "do not prepare a clip as a jpeg"
);

console.log("mediaKind.selftest ok");
