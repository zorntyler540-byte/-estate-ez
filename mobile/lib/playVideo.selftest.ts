import { videoPlayMode } from "./playVideoMode";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(videoPlayMode("") === "none", "empty");
assert(videoPlayMode(null) === "none", "null");
assert(videoPlayMode("https://r2.example/clip.mp4") === "remote", "https");
assert(videoPlayMode("http://localhost/v.mov") === "remote", "http");
assert(videoPlayMode("file:///lot-captures/x/1.mov") === "local", "file");
assert(videoPlayMode("/var/mobile/1.mp4") === "local", "path");
assert(videoPlayMode("content://media/external/video/1") === "local", "content");

console.log("playVideo selftest ok (7 cases)");
