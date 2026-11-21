import assert from "node:assert/strict";
import test from "node:test";
import {
  frameIsConfidentBlank,
  inheritedFolderSku,
  lotFolderKey,
  lotSplitSummary,
  previewLotSplits,
  probeFromGray
} from "./lotSplitPreview.ts";

test("lot folder is the directory under the folder the user picked", () => {
  assert.equal(lotFolderKey("Sale/035/IMG_1.jpg"), "035");
  assert.equal(lotFolderKey("035/IMG_1.jpg"), "035");
  assert.equal(lotFolderKey("Sale/IMG_1.jpg"), "Sale");
  assert.equal(lotFolderKey("IMG_1.jpg"), "");
  assert.equal(inheritedFolderSku("035"), "035");
  assert.equal(inheritedFolderSku("Sale"), null);
  assert.equal(inheritedFolderSku("7"), null);
});

test("folders and blank sheets split lots; the sheet is left out", () => {
  const labels = [
    "Sale/035/20261120_091500.jpg",
    "Sale/035/20261120_120000.jpg",
    "Sale/035/blank.jpg",
    "Sale/036/IMG_9.jpg",
    "Sale/036/IMG_10.jpg"
  ];
  const splits = previewLotSplits(labels, {
    dividerIndexes: new Set([2]),
    inheritSku: true
  });
  assert.deepEqual(splits, [
    { kind: "lot", lotNumber: 1, sku: "035", indexes: [0, 1] },
    { kind: "divider", index: 2 },
    { kind: "lot", lotNumber: 2, sku: "036", indexes: [3, 4] }
  ]);
});

test("leading, trailing, and repeated dividers do not make empty lots", () => {
  const splits = previewLotSplits(["a.jpg", "blank.jpg", "blank2.jpg", "b.jpg", "blank3.jpg"], {
    dividerIndexes: new Set([0, 1, 4])
  });
  assert.deepEqual(
    splits.map((split) => (split.kind === "lot" ? split.indexes : `d${split.index}`)),
    ["d0", "d1", [2, 3], "d4"]
  );
});

test("one-lot uploads stay one lot", () => {
  const splits = previewLotSplits(["Sale/035/a.jpg", "Sale/036/b.jpg"], {
    singleLot: true,
    dividerIndexes: new Set([0]),
    inheritSku: true
  });
  assert.deepEqual(splits, [{ kind: "lot", lotNumber: 1, sku: null, indexes: [0, 1] }]);
});

test("summary names the lots, blank sheets, and the tape caveat", () => {
  assert.match(lotSplitSummary({ lotCount: 2, dividerCount: 1 }), /2 lots from folders.*1 blank sheet left out/);
  assert.match(lotSplitSummary({ lotCount: 1, dividerCount: 0, checkingBlank: true }), /Checking blank sheets/);
  assert.match(lotSplitSummary({ lotCount: 1, dividerCount: 0, singleLot: true }), /stay one lot/);
});

test("uniform paper is a blank sheet and a two-tone frame is not", () => {
  const white = new Uint8Array(40 * 40).fill(245);
  const paper = probeFromGray(white, 40, 40);
  assert.equal(frameIsConfidentBlank(paper), true);

  const dark = new Uint8Array(40 * 40).fill(8);
  assert.equal(frameIsConfidentBlank(probeFromGray(dark, 40, 40)), true);

  const mixed = new Uint8Array(40 * 40);
  for (let i = 0; i < mixed.length; i += 1) mixed[i] = i % 40 < 20 ? 20 : 230;
  assert.equal(frameIsConfidentBlank(probeFromGray(mixed, 40, 40)), false);
  assert.equal(probeFromGray(new Uint8Array(8 * 8), 8, 8), null);
});
