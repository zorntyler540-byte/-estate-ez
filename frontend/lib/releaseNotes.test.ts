import assert from "node:assert/strict";
import test from "node:test";
import {
  formatReleaseNoteDate,
  markReleaseNoteSeen,
  parseReleaseNoteLatest,
  readSeenReleaseNoteId,
  releaseNoteToShow,
  type ReleaseNote
} from "./releaseNotes.ts";

const note: ReleaseNote = {
  id: "2026-11-18-uploads-photos-listings",
  date: "2026-11-18",
  title: "Updates for your next sale",
  items: ["Big uploads now split lots correctly at white divider sheets."]
};

function memoryStore() {
  const mem = new Map<string, string>();
  return {
    getItem: (key: string) => mem.get(key) ?? null,
    setItem: (key: string, value: string) => {
      mem.set(key, value);
    }
  };
}

test("a note shows once per id and a new id shows again", () => {
  const store = memoryStore();
  assert.equal(readSeenReleaseNoteId(store), null);
  assert.equal(releaseNoteToShow(readSeenReleaseNoteId(store), note)?.id, note.id);
  markReleaseNoteSeen(note.id, store);
  assert.equal(releaseNoteToShow(readSeenReleaseNoteId(store), note), null);
  assert.ok(releaseNoteToShow(readSeenReleaseNoteId(store), { ...note, id: "next-note" }));
});

test("an older hashed phone key does not hide a server note id", () => {
  assert.ok(releaseNoteToShow("note-1a2b", note));
  assert.equal(releaseNoteToShow(`  ${note.id}  `, note), null);
});

test("blank notes stay quiet", () => {
  assert.equal(releaseNoteToShow(null, null), null);
  assert.equal(releaseNoteToShow(null, { ...note, id: "  ", items: ["x"] }), null);
  assert.equal(releaseNoteToShow(null, { ...note, items: [] }), null);
});

test("latest payload parses and trims bullets", () => {
  const parsed = parseReleaseNoteLatest({
    note: {
      id: " 2026-11-18-uploads-photos-listings ",
      date: "2026-11-18",
      title: " Updates ",
      platform: "all",
      items: ["  Set reserve works again. ", "", 3]
    }
  });
  assert.deepEqual(parsed, {
    id: "2026-11-18-uploads-photos-listings",
    date: "2026-11-18",
    title: "Updates",
    items: ["Set reserve works again."]
  });
  assert.equal(parseReleaseNoteLatest({ note: null }), null);
  assert.equal(parseReleaseNoteLatest(null), null);
  assert.equal(parseReleaseNoteLatest({ note: { id: "x", title: "T", items: [] } }), null);
});

test("dates format without shifting the calendar day", () => {
  assert.equal(formatReleaseNoteDate("2026-11-18"), "November 18, 2026");
  assert.equal(formatReleaseNoteDate("nope"), "nope");
  assert.equal(formatReleaseNoteDate("2026-13-01"), "2026-13-01");
});
