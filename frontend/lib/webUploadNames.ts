/**
 * Stable multipart names for website / client-portal photo uploads:
 * ``[subfolder/]web-<batch>-NNNN[-stem].ext``.
 *
 * Camera names (``IMG_1234.JPG``, iOS ``image.jpg``) repeat across phones and picker sessions,
 * so the server never treats them as a photo's identity. ``<batch>`` is random per staged batch
 * and ``NNNN`` is the pick order, so a name is unique across the sale, a retried chunk is
 * recognized as the same photo, and the server's name sort keeps pick order.
 * Must match ``backend/app/services/upload_names.py``.
 */

const BATCH_ID_HEX = 12;
const STEM_MAX = 40;

export function newWebUploadBatchId(): string {
  const bytes = new Uint8Array(BATCH_ID_HEX / 2);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function safeExtension(leaf: string): string {
  const dot = leaf.lastIndexOf(".");
  const ext = dot > 0 ? leaf.slice(dot).toLowerCase() : "";
  return /^\.[a-z0-9]{2,5}$/.test(ext) ? ext : ".jpg";
}

function safeStem(leaf: string): string {
  const dot = leaf.lastIndexOf(".");
  const stem = dot > 0 ? leaf.slice(0, dot) : leaf;
  return stem
    .replace(/[^A-Za-z0-9_-]+/g, "_")
    .slice(0, STEM_MAX)
    .replace(/^[_-]+|[_-]+$/g, "");
}

/** ``seq`` is 1-based pick order. ``relPath`` may include subfolders (kept for lot boundaries). */
export function webUploadName(batchId: string, seq: number, relPath: string): string {
  const parts = relPath.replace(/\\/g, "/").replace(/^\/+/, "").split("/").filter(Boolean);
  const leaf = parts.pop() || "photo.jpg";
  const stem = safeStem(leaf);
  const name = `web-${batchId}-${String(seq).padStart(4, "0")}${stem ? `-${stem}` : ""}${safeExtension(leaf)}`;
  return [...parts, name].join("/");
}

const WEB_NAME = /^web-[0-9a-f]{12,32}-\d{4,5}(?:-[a-z0-9_-]{1,60})?\.[a-z0-9]{2,5}$/i;

export function isWebUploadName(name: string): boolean {
  const leaf = name.replace(/\\/g, "/").split("/").pop() || "";
  return WEB_NAME.test(leaf);
}
