import assert from "node:assert/strict";
import test from "node:test";
import { nextThumbStep, thumbFailedMessage } from "./lotPhotoThumbLoad.ts";

test("a signed URL that the browser cannot paint falls back to /thumbnail", () => {
  assert.equal(nextThumbStep("idle", "signed-ok"), "signed");
  assert.equal(nextThumbStep("signed", "img-error"), "thumbnail");
  assert.equal(nextThumbStep("thumbnail", "thumb-ok"), "thumbnail");
  assert.equal(nextThumbStep("thumbnail", "img-error"), "failed");
});

test("missing signed URL goes straight to thumbnail, then a clear failure", () => {
  assert.equal(nextThumbStep("idle", "signed-empty"), "thumbnail");
  assert.equal(nextThumbStep("idle", "signed-fail"), "thumbnail");
  assert.equal(nextThumbStep("thumbnail", "thumb-fail"), "failed");
  assert.equal(thumbFailedMessage(true), "Could not load");
  assert.equal(thumbFailedMessage(false), "Missing on server");
});
