import assert from "node:assert/strict";
import test from "node:test";
import { isWebUploadName, newWebUploadBatchId, webUploadName } from "./webUploadNames.ts";

test("batch ids are 12 hex chars and random", () => {
  const a = newWebUploadBatchId();
  const b = newWebUploadBatchId();
  assert.match(a, /^[0-9a-f]{12}$/);
  assert.notEqual(a, b);
});

test("repeated camera names become distinct stable names in pick order", () => {
  const batch = "0123456789ab";
  const names = ["image.jpg", "image.jpg", "IMG_1234.JPG"].map((n, i) => webUploadName(batch, i + 1, n));
  assert.deepEqual(names, [
    "web-0123456789ab-0001-image.jpg",
    "web-0123456789ab-0002-image.jpg",
    "web-0123456789ab-0003-IMG_1234.jpg"
  ]);
  assert.equal(new Set(names).size, 3);
  assert.deepEqual([...names].sort(), names);
  for (const n of names) assert.ok(isWebUploadName(n), n);
});

test("subfolders are kept so folder lot boundaries survive", () => {
  assert.equal(
    webUploadName("0123456789ab", 7, "Estate/035/IMG 0001 (copy).HEIC"),
    "Estate/035/web-0123456789ab-0007-IMG_0001_copy.heic"
  );
});

test("odd or missing extensions fall back to .jpg and stay server-valid", () => {
  const n = webUploadName("0123456789ab", 12, "weird.name.with.dots.verylongext");
  assert.ok(n.endsWith(".jpg"));
  assert.ok(isWebUploadName(n), n);
  assert.ok(isWebUploadName(webUploadName("0123456789ab", 1, "")));
  assert.ok(isWebUploadName(webUploadName("0123456789ab", 2, "ü.jpg")));
});

test("camera names are not web names", () => {
  for (const n of ["image.jpg", "IMG_1234.JPG", "web-image.jpg", "web-0123-0001.jpg"]) {
    assert.equal(isWebUploadName(n), false, n);
  }
});
