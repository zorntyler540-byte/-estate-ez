import assert from "node:assert/strict";
import test from "node:test";
import { appendStandardDescription, replaceTrailingStandardDescription } from "./standardDescription.ts";

test("standard note is appended once, verbatim", () => {
  const note = "Reserve is $5";
  const once = appendStandardDescription("Silver pitcher.", note);
  assert.equal(once, "Silver pitcher.\n\nReserve is $5");
  assert.equal(appendStandardDescription(once, note), once);
  assert.equal(appendStandardDescription("Silver pitcher.\nReserve is $5", note), "Silver pitcher.\nReserve is $5");
  assert.equal(appendStandardDescription("Reserve is $50", note), "Reserve is $50\n\nReserve is $5");
  assert.equal(appendStandardDescription("", note), note);
});

test("changing the note replaces the trailing copy", () => {
  const body = "Silver pitcher.\n\nReserve is $5";
  assert.equal(
    replaceTrailingStandardDescription(body, "Reserve is $5", "Reserve is $10"),
    "Silver pitcher.\n\nReserve is $10"
  );
  assert.equal(replaceTrailingStandardDescription(body, "Reserve is $5", ""), "Silver pitcher.");
});
