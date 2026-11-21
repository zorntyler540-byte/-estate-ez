import assert from "node:assert/strict";
import test from "node:test";
import { assignUploadNames, newWebUploadSession, uploadNameOf } from "./webUploadSession.ts";
import {
  buildUploadOrderPreview,
  cameraStampRegressionNote,
  cameraStampRegressions,
  cameraStampValue,
  sortFilesBeforeUploadNames,
  sortUploadFiles,
  uploadSortLabel,
  type UploadSortable
} from "./sortUploadFiles.ts";

const shot = (name: string, lastModified: number, webkitRelativePath = ""): UploadSortable => ({
  name,
  lastModified,
  webkitRelativePath
});

test("camera timestamps and IMG numbers sort numerically, lastModified breaks ties", () => {
  const ordered = sortUploadFiles([
    shot("20261120_120200.jpg", 3),
    shot("20261120_120010.jpg", 1),
    shot("IMG_10.jpg", 5),
    shot("IMG_9.jpg", 4),
    shot("image.jpg", 20),
    shot("image.jpg", 10)
  ]);
  assert.deepEqual(
    ordered.map((file) => `${file.name}@${file.lastModified}`),
    [
      "20261120_120010.jpg@1",
      "20261120_120200.jpg@3",
      "image.jpg@10",
      "image.jpg@20",
      "IMG_9.jpg@4",
      "IMG_10.jpg@5"
    ]
  );
});

test("folder picks sort by relative path so subfolders stay together", () => {
  const ordered = sortUploadFiles([
    shot("IMG_1.jpg", 1, "Estate/036/IMG_1.jpg"),
    shot("IMG_10.jpg", 2, "Estate/035/IMG_10.jpg"),
    shot("IMG_2.jpg", 3, "Estate/035/IMG_2.jpg"),
    shot("a.jpg", 4, "Estate/100/a.jpg")
  ]);
  assert.deepEqual(ordered.map(uploadSortLabel), [
    "Estate/035/IMG_2.jpg",
    "Estate/035/IMG_10.jpg",
    "Estate/036/IMG_1.jpg",
    "Estate/100/a.jpg"
  ]);
});

test("equal name and time keep the original order", () => {
  const files = [shot("image.jpg", 1), shot("image.jpg", 1)];
  const ordered = sortUploadFiles(files);
  assert.equal(ordered[0], files[0]);
  assert.equal(ordered[1], files[1]);
});

test("assignUploadNames follows natural filename order; named files are not reshuffled", () => {
  const session = newWebUploadSession();
  const late = new File([new Uint8Array([1])], "20261120_153000.jpg", {
    type: "image/jpeg",
    lastModified: 30
  });
  const early = new File([new Uint8Array([1])], "20261120_091500.jpg", {
    type: "image/jpeg",
    lastModified: 10
  });
  const mid = new File([new Uint8Array([1])], "20261120_120000.jpg", {
    type: "image/jpeg",
    lastModified: 20
  });
  const ordered = sortFilesBeforeUploadNames([late, early, mid]);
  const names = assignUploadNames(session, ordered, (file) => file.name);
  assert.match(names[0]!, /-0001-20261120_091500\.jpg$/);
  assert.match(names[1]!, /-0002-20261120_120000\.jpg$/);
  assert.match(names[2]!, /-0003-20261120_153000\.jpg$/);

  const again = sortFilesBeforeUploadNames([late, early, mid], (file) => Boolean(uploadNameOf(file)));
  assert.deepEqual(
    again.map((file) => file.name),
    ["20261120_153000.jpg", "20261120_091500.jpg", "20261120_120000.jpg"]
  );
  const kept = assignUploadNames(session, again, (file) => file.name);
  assert.equal(kept[0], names[2]);
  assert.equal(kept[1], names[0]);
  assert.equal(kept[2], names[1]);
});

test("camera stamps ignore divider names and flag a time that moves backward", () => {
  assert.equal(cameraStampValue("Estate/035/20261120_091500.jpg"), 20261120091500);
  assert.equal(cameraStampValue("IMG_20261120_235959.JPG"), 20261120235959);
  assert.equal(cameraStampValue("20261120_256000.jpg"), null);
  assert.equal(cameraStampValue("blank.jpg"), null);

  const files = [
    shot("20261120_091500.jpg", 1),
    shot("blank.jpg", 2),
    shot("20261120_120000.jpg", 3),
    shot("20261120_080000.jpg", 4, "Estate/036/20261120_080000.jpg")
  ];
  const regressions = cameraStampRegressions(files);
  assert.deepEqual(
    regressions.map((row) => row.index),
    [3]
  );
  assert.equal(regressions[0]?.earlierIndex, 2);
  assert.match(cameraStampRegressionNote(regressions), /1 photo is timestamped earlier.*row 4/);
  assert.equal(cameraStampRegressionNote([]), "");
});

test("catalog order preview keeps filename order, folder breaks, and a backward stamp", () => {
  const preview = buildUploadOrderPreview([
    shot("20261120_120000.jpg", 1, "Sale/035/20261120_120000.jpg"),
    shot("blank.jpg", 2, "Sale/035/blank.jpg"),
    shot("20261120_080000.jpg", 3, "Sale/036/20261120_080000.jpg")
  ]);
  assert.deepEqual(
    preview.rows.map((row) => row.leaf),
    ["20261120_120000.jpg", "blank.jpg", "20261120_080000.jpg"]
  );
  assert.deepEqual(
    preview.rows.map((row) => row.showDir),
    [true, false, true]
  );
  assert.equal(preview.folderCount, 2);
  assert.equal(preview.rows[2]?.backwards, true);
  assert.equal(preview.rows[2]?.earlierIndex, 0);
  assert.equal(preview.rows[2]?.earlierLeaf, "20261120_120000.jpg");
  assert.match(preview.note, /row 3/);
  assert.equal(preview.first, "20261120_120000.jpg");
  assert.equal(preview.last, "20261120_080000.jpg");
});
