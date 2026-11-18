import {
  formatReleaseNoteDate,
  parseReleaseNoteLatest,
  releaseNoteToShow,
  type ReleaseNote
} from "./releaseNote";

function assert(cond: boolean, message: string): void {
  if (!cond) throw new Error(message);
}

const note: ReleaseNote = {
  id: "2026-11-18-uploads-photos-listings",
  date: "2026-11-18",
  title: "Updates for your next sale",
  items: ["Big uploads now split lots correctly at white divider sheets."]
};

const first = releaseNoteToShow(null, note);
assert(first?.id === note.id, "unseen note shows");
assert(releaseNoteToShow(note.id, note) === null, "same id stays hidden");
assert(releaseNoteToShow(`  ${note.id}  `, note) === null, "stored id is trimmed");
assert(releaseNoteToShow("note-1a2b", note)?.id === note.id, "old hashed key does not hide a server id");
assert(releaseNoteToShow(note.id, { ...note, id: "next-note" })?.id === "next-note", "a new id shows");
assert(releaseNoteToShow(null, null) === null, "missing note stays quiet");
assert(releaseNoteToShow(null, { ...note, id: "  " }) === null, "blank id stays quiet");
assert(releaseNoteToShow(null, { ...note, items: [] }) === null, "empty bullets stay quiet");

const parsed = parseReleaseNoteLatest({
  note: {
    id: " 2026-11-18-uploads-photos-listings ",
    date: "2026-11-18",
    title: " Updates ",
    items: ["  Set reserve works again. ", ""]
  }
});
assert(parsed?.id === "2026-11-18-uploads-photos-listings", "parses the note id");
assert(parsed?.title === "Updates", "trims the title");
assert(parsed?.items.length === 1 && parsed.items[0] === "Set reserve works again.", "trims bullets");
assert(parseReleaseNoteLatest({ note: null }) === null, "null note stays quiet");
assert(parseReleaseNoteLatest(null) === null, "bad payload stays quiet");
assert(formatReleaseNoteDate("2026-11-18") === "November 18, 2026", "formats the calendar day");
assert(formatReleaseNoteDate("nope") === "nope", "leaves an odd date alone");

console.log("releaseNote selftest ok");
