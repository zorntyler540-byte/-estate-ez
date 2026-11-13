import { releaseNoteKey, releaseNoteToShow } from "./releaseNote";

function assert(cond: boolean, message: string): void {
  if (!cond) throw new Error(message);
}

const first = releaseNoteToShow(null, "  Photos show under the count.  ");
assert(first?.text === "Photos show under the count.", "trims the note");
assert(first?.key === releaseNoteKey("Photos show under the count."), "key matches the shown text");

assert(releaseNoteToShow(first!.key, "Photos show under the count.") === null, "same note is not shown again");
assert(releaseNoteToShow(first!.key, "Bulk upload clears when the photos are queued.") !== null, "a new note shows");
assert(releaseNoteToShow(null, "   ") === null, "blank note stays quiet");

console.log("releaseNote selftest ok");
