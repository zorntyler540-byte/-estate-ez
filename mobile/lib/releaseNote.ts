/**
 * One plain-language sentence for the next app open. Change this text when a fix ships.
 * The phone shows it once in a "What's new" popup. The same sentence belongs in `eas update --message`.
 */
export const RELEASE_NOTE =
  "Bulk upload clears as soon as the photos are in the Queue, so you can start another batch. On the camera, the shots you just took show under the photo count.";

const SEEN_PREFIX = "note-";

export function releaseNoteKey(note: string): string {
  let hash = 0;
  for (let i = 0; i < note.length; i++) hash = (hash * 31 + note.charCodeAt(i)) >>> 0;
  return `${SEEN_PREFIX}${hash.toString(16)}`;
}

/** Null when the note is blank or this phone already dismissed this exact text. */
export function releaseNoteToShow(seenKey: string | null, note: string): { key: string; text: string } | null {
  const text = note.trim();
  if (!text) return null;
  const key = releaseNoteKey(text);
  if (seenKey === key) return null;
  return { key, text };
}
