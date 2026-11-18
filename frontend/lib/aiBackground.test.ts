import assert from "node:assert/strict";
import test from "node:test";
import { defaultAiBackgroundPhoto, photoCanBeLotCover } from "./aiBackground.ts";

test("default photo is the hero, otherwise the first listing still", () => {
  const photos = [
    { id: "tape", photo_role: "tape", original_name: "tape.jpg" },
    { id: "item", photo_role: "item_front", original_name: "item.jpg" },
    { id: "hero", photo_role: "hero", original_name: "group.jpg" },
    { id: "measure", photo_role: "angle_only", shows_measuring_scale: true, original_name: "tape-measure.jpg" }
  ];
  assert.equal(defaultAiBackgroundPhoto(photos)?.id, "hero");
  assert.equal(
    defaultAiBackgroundPhoto([
      { id: "first", photo_role: null, original_name: "a.jpg" },
      { id: "second", photo_role: "item_front", original_name: "b.jpg" }
    ])?.id,
    "first"
  );
  assert.equal(defaultAiBackgroundPhoto([]), null);
  assert.equal(photoCanBeLotCover(photos[0]), false);
  assert.equal(photoCanBeLotCover(photos[2]), true);
  assert.equal(photoCanBeLotCover(photos[3]), false);
});
