/**
 * What's new notes come from GET /release-notes/latest. The browser remembers
 * the note id in localStorage and shows that id once.
 */

export type ReleaseNote = {
  id: string;
  date: string;
  title: string;
  items: string[];
};

export const RELEASE_NOTE_SEEN_KEY = "release_note_seen";

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December"
];

type KeyValueStore = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

function browserStore(): KeyValueStore | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage;
}

export function formatReleaseNoteDate(date: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date.trim());
  if (!match) return date;
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return date;
  return `${month} ${Number(match[3])}, ${match[1]}`;
}

export function parseReleaseNote(value: unknown): ReleaseNote | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const id = typeof row.id === "string" ? row.id.trim() : "";
  const title = typeof row.title === "string" ? row.title.trim() : "";
  const date = typeof row.date === "string" ? row.date.trim() : "";
  const items = Array.isArray(row.items)
    ? row.items.map((item) => (typeof item === "string" ? item.trim() : "")).filter(Boolean)
    : [];
  if (!id || !title || items.length === 0) return null;
  return { id, date, title, items };
}

export function parseReleaseNoteLatest(data: unknown): ReleaseNote | null {
  if (!data || typeof data !== "object") return null;
  return parseReleaseNote((data as { note?: unknown }).note);
}

/** Null when there is no note, or this browser already dismissed this id. */
export function releaseNoteToShow(seenId: string | null | undefined, note: ReleaseNote | null): ReleaseNote | null {
  if (!note) return null;
  const id = note.id.trim();
  if (!id || note.items.length === 0) return null;
  if ((seenId ?? "").trim() === id) return null;
  return note;
}

export function readSeenReleaseNoteId(store?: KeyValueStore | null): string | null {
  const target = store === undefined ? browserStore() : store;
  if (!target) return null;
  try {
    return target.getItem(RELEASE_NOTE_SEEN_KEY);
  } catch {
    return null;
  }
}

export function markReleaseNoteSeen(id: string, store?: KeyValueStore | null): void {
  const target = store === undefined ? browserStore() : store;
  if (!target) return;
  const value = id.trim();
  if (!value) return;
  try {
    target.setItem(RELEASE_NOTE_SEEN_KEY, value);
  } catch {
    /* private mode or blocked storage */
  }
}
