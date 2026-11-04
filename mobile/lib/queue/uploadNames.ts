/**
 * Stable multipart names for queue media. Each row gets its name once and keeps it, so a
 * resumed or retried upload sends the same name and the server can skip what it already has.
 * Index-based names shifted after a delete and could make a different photo look "received".
 *
 * New-lot photos: ``mobile-<draft>-NN.jpg`` · clips: ``mobile-<draft>-vid-NN.ext``.
 * Add-photos sessions: ``append-<draft>-NN.jpg`` · clips: ``append-<draft>-vid-NN.ext``.
 */

export type UploadNamePrefix = "mobile" | "append";

export type NameableRow = {
  id: string;
  kind: "image" | "video";
  uploadName?: string | null;
  fileUri?: string | null;
};

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

export function videoExtension(fileUri: string | null | undefined): string {
  const match = String(fileUri || "")
    .toLowerCase()
    .match(/\.(mp4|mov|m4v|webm)(?:\?|$)/);
  return match?.[1] ? `.${match[1]}` : ".mp4";
}

export function photoUploadName(prefix: UploadNamePrefix, draftId: string, seq: number): string {
  return `${prefix}-${draftId}-${pad(seq)}.jpg`;
}

export function clipUploadName(
  prefix: UploadNamePrefix,
  draftId: string,
  seq: number,
  fileUri?: string | null
): string {
  return `${prefix}-${draftId}-vid-${pad(seq)}${videoExtension(fileUri)}`;
}

/** Sequence number in a name this module produced, or null. */
export function uploadNameSeq(name: string | null | undefined, kind: "image" | "video"): number | null {
  const raw = String(name || "");
  const m = kind === "video" ? raw.match(/-vid-(\d+)\.[a-z0-9]+$/i) : raw.match(/-(\d+)\.jpe?g$/i);
  if (!m || (kind === "image" && /-vid-\d+\./i.test(raw))) return null;
  const n = Number.parseInt(m[1]!, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Names for rows that do not have one yet, in the given (capture) order. New numbers start
 * above the highest existing one per kind, so a deleted photo's number is never reused.
 */
export function planUploadNames(
  prefix: UploadNamePrefix,
  draftId: string,
  rows: readonly NameableRow[]
): Map<string, string> {
  const max = { image: 0, video: 0 };
  for (const row of rows) {
    const seq = uploadNameSeq(row.uploadName, row.kind);
    if (seq != null && seq > max[row.kind]) max[row.kind] = seq;
  }
  const out = new Map<string, string>();
  for (const row of rows) {
    if (row.uploadName) continue;
    const seq = ++max[row.kind];
    out.set(
      row.id,
      row.kind === "video"
        ? clipUploadName(prefix, draftId, seq, row.fileUri)
        : photoUploadName(prefix, draftId, seq)
    );
  }
  return out;
}

/** Folder (under Documents) holding a draft's upload-ready JPEGs, one per upload name. */
export function preparedDirName(draftId: string): string {
  return `lot-prepared/${draftId}/`;
}

/** Draft id from a ``lot-prepared/<id>/`` child name, or null for anything else. */
export function draftIdFromPreparedEntry(entry: string): string | null {
  const id = String(entry || "").replace(/\/+$/, "");
  return /^[A-Za-z0-9-]+$/.test(id) ? id : null;
}
