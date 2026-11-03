import assert from "node:assert/strict";
import test from "node:test";
import { isVideoLikePhotoName, lotEditorGallery, photosForEditorGrid } from "./lotMediaGallery.ts";

test("video-named stills never stay in the photo grid", () => {
  assert.equal(isVideoLikePhotoName("mobile-draft-01.jpg"), false);
  assert.equal(isVideoLikePhotoName("mobile-draft-vid-01.mp4"), true);
  assert.equal(isVideoLikePhotoName("walk.MOV"), true);
  const photos = [
    { id: "a", original_name: "mobile-x-01.jpg" },
    { id: "b", original_name: "mobile-x-vid-01.mp4" },
    { id: "c", original_name: "mobile-x-02.jpg" }
  ];
  assert.deepEqual(
    photosForEditorGrid(photos).map((p) => p.id),
    ["a", "c"]
  );
});

test("gallery keeps four stills then the clip", () => {
  const photos = [1, 2, 3, 4].map((n) => ({
    id: `p${n}`,
    original_name: `mobile-d-0${n}.jpg`
  }));
  const videos = [{ id: "v1", original_name: "mobile-d-vid-01.mp4" }];
  const items = lotEditorGallery(photos, videos);
  assert.equal(items.length, 5);
  assert.deepEqual(
    items.map((item) => (item.kind === "photo" ? item.photo.id : item.video.id)),
    ["p1", "p2", "p3", "p4", "v1"]
  );
  assert.equal(items[4]?.kind, "video");
});
