import { isVideoUri, mediaKindFromName, splitMediaUris, videoMimeType } from "./mediaKind";

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
console.log("mediaKind.selftest ok");
