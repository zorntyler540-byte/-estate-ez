import assert from "node:assert/strict";
import test from "node:test";
import {
  arrangedRel,
  arrangeStateFromIndexes,
  mergeArrangeLots,
  moveArrangePhoto,
  moveToNewLot,
  splitArrangeLot
} from "./arrangeLots.ts";

test("arrange state keeps divider photos out of the lots", () => {
  const state = arrangeStateFromIndexes([{ indexes: [0, 1] }, { indexes: [3, 4] }], [2]);
  assert.deepEqual(state.lots, [
    [0, 1],
    [3, 4]
  ]);
  assert.deepEqual(state.omitted, [2]);
});

test("moving a photo into another lot fixes the group", () => {
  const start = arrangeStateFromIndexes([{ indexes: [0, 1, 2] }, { indexes: [3] }], []);
  const moved = moveArrangePhoto(start, { where: "lot", lot: 0, pos: 2 }, { where: "lot", lot: 1, pos: 0 });
  assert.deepEqual(moved.lots, [
    [0, 1],
    [2, 3]
  ]);
});

test("dragging a photo out of a lot leaves it out of the upload", () => {
  const start = arrangeStateFromIndexes([{ indexes: [0, 1] }], []);
  const moved = moveArrangePhoto(start, { where: "lot", lot: 0, pos: 0 }, { where: "omitted", pos: 0 });
  assert.deepEqual(moved.lots, [[1]]);
  assert.deepEqual(moved.omitted, [0]);
});

test("split and merge lots", () => {
  const start = arrangeStateFromIndexes([{ indexes: [0, 1, 2, 3] }], []);
  const split = splitArrangeLot(start, 0, 2);
  assert.deepEqual(split.lots, [
    [0, 1],
    [2, 3]
  ]);
  assert.deepEqual(mergeArrangeLots(split, 0).lots, [[0, 1, 2, 3]]);
});

test("a photo can start a new lot on its own", () => {
  const start = arrangeStateFromIndexes([{ indexes: [0, 1, 2] }], []);
  const next = moveToNewLot(start, { where: "lot", lot: 0, pos: 1 });
  assert.deepEqual(next.lots, [
    [0, 2],
    [1]
  ]);
});

test("arranged paths use the lot number and the leaf name", () => {
  assert.equal(arrangedRel(2, "Sale/035/IMG_2461.jpg"), "arranged/lot-2/IMG_2461.jpg");
});
