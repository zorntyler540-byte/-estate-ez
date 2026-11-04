import {
  catalogPhotoFileName,
  catalogUploadPhotoRows,
  countDraftMedia,
  formatMediaCounts,
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

const fourPlusClip = [
  { id: "p1", media_kind: "image", file_uri: "file:///a.jpg" },
  { id: "p2", media_kind: "image", file_uri: "file:///b.jpg" },
  { id: "v1", media_kind: "video", file_uri: "file:///c.mp4" },
  { id: "p3", media_kind: "image", file_uri: "file:///d.jpg" },
  { id: "p4", media_kind: "image", file_uri: "file:///e.jpg" }
];
const counts = countDraftMedia(fourPlusClip);
assert(counts.photoCount === 4 && counts.videoCount === 1, "count stills vs clips");
assert(formatMediaCounts(4, 1) === "4 photos · 1 video", "queue title media");
assert(formatMediaCounts(1, 0) === "1 photo", "one photo");
assert(formatMediaCounts(0, 2) === "2 videos", "videos only");
const uploadRows = catalogUploadPhotoRows(fourPlusClip);
assert(uploadRows.map((r) => r.id).join(",") === "p1,p2,p3,p4", "clip does not take a photo slot");
assert(
  uploadRows.map((_, i) => catalogPhotoFileName("draft-1", i)).join(",") ===
    "mobile-draft-1-01.jpg,mobile-draft-1-02.jpg,mobile-draft-1-03.jpg,mobile-draft-1-04.jpg",
  "stills keep 01-04 when a clip is present"
);

console.log("mediaKind.selftest ok");
