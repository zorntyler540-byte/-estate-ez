import assert from "node:assert/strict";
import test from "node:test";
import {
  assignUploadNames,
  decideResume,
  inheritUploadName,
  missingUploadIndexes,
  newWebUploadSession,
  setUploadName,
  uploadNameOf,
  webClientDraftId,
  webUploadBatchOf
} from "./webUploadSession.ts";

const file = (name: string) => new File([new Uint8Array([1, 2, 3])], name, { type: "image/jpeg" });
const rel = (f: File) => f.name;

test("names follow pick order and stay put when files are added or removed", () => {
  const session = newWebUploadSession();
  const a = file("image.jpg");
  const b = file("image.jpg");
  const first = assignUploadNames(session, [a, b], rel);
  assert.match(first[0]!, new RegExp(`^web-${session.batchId}-0001-image\\.jpg$`));
  assert.match(first[1]!, new RegExp(`^web-${session.batchId}-0002-image\\.jpg$`));

  const c = file("IMG_9.JPG");
  const again = assignUploadNames(session, [b, c], rel);
  assert.equal(again[0], first[1]);
  assert.match(again[1]!, /-0003-IMG_9\.jpg$/);
});

test("a converted or edited copy keeps its slot; other batches' names are replaced", () => {
  const session = newWebUploadSession();
  const raw = file("IMG_1.HEIC");
  const [name] = assignUploadNames(session, [raw], rel);
  const jpeg = file("IMG_1.jpg");
  inheritUploadName(raw, jpeg);
  assert.equal(assignUploadNames(session, [jpeg], rel)[0], name);

  const stale = file("x.jpg");
  setUploadName(stale, "web-aaaaaaaaaaaa-0001-x.jpg");
  const [fresh] = assignUploadNames(session, [stale], rel);
  assert.equal(webUploadBatchOf(fresh!), session.batchId);
  assert.equal(uploadNameOf(stale), fresh);
});

test("restored names push nextSeq past them", () => {
  const session = { batchId: "0123456789ab", nextSeq: 0, jobId: null };
  const restored = file("a.jpg");
  setUploadName(restored, "web-0123456789ab-0042-a.jpg");
  const added = file("b.jpg");
  const names = assignUploadNames(session, [restored, added], rel);
  assert.equal(names[1], "web-0123456789ab-0043-b.jpg");
});

test("client draft id fits the server's 36-char key", () => {
  const session = newWebUploadSession();
  const id = webClientDraftId(session);
  assert.ok(id.length <= 36);
  assert.equal(id, `web-${session.batchId}`);
});

test("missing indexes compare without extension and accept leaf or folder names", () => {
  const names = [
    "Estate/A/web-0123456789ab-0001-x.png",
    "web-0123456789ab-0002-y.jpg",
    "web-0123456789ab-0003-z.jpg"
  ];
  assert.deepEqual(
    missingUploadIndexes(names, ["Estate/A/web-0123456789ab-0001-x.jpg", "web-0123456789ab-0003-z.jpg"]),
    [1]
  );
});

test("resume rules: receiving resumes, submitted is never re-sent, orphans start fresh", () => {
  const names = ["web-0123456789ab-0001-a.jpg", "web-0123456789ab-0002-b.jpg"];
  const session = { batchId: "0123456789ab", nextSeq: 2, jobId: "job-1" };

  assert.deepEqual(decideResume({ ...session, jobId: null }, names, null), { kind: "fresh", discardJobId: null });
  assert.deepEqual(decideResume(session, names, null), { kind: "fresh", discardJobId: null });
  assert.deepEqual(decideResume(session, names, { status: "receiving", names: [names[0]!] }), {
    kind: "resume",
    jobId: "job-1",
    missing: [1]
  });
  assert.deepEqual(
    decideResume(session, names, { status: "receiving", names: [names[0]!, "web-0123456789ab-0003-gone.jpg"] }),
    { kind: "fresh", discardJobId: "job-1" }
  );
  assert.deepEqual(decideResume(session, names, { status: "receiving", names: [], folder_missing: true }), {
    kind: "fresh",
    discardJobId: "job-1"
  });
  for (const status of ["queued", "processing", "completed"]) {
    assert.deepEqual(decideResume(session, names, { status, names: [] }), {
      kind: "already_submitted",
      jobId: "job-1",
      status
    });
  }
  assert.equal(
    decideResume(session, names, {
      status: "failed",
      names: [],
      error_message: "Upload never finished (website upload stopped mid-batch)."
    }).kind,
    "fresh"
  );
  assert.equal(
    decideResume(session, names, { status: "failed", names: [], error_message: "Gemini 500" }).kind,
    "already_submitted"
  );
});
