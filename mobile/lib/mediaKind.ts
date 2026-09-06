/** Keep in sync with ``backend/app/catalog_limits.py``. */

export const MAX_VIDEOS_PER_LOT = 8;
export const MAX_VIDEO_DURATION_SEC = 45;

const VIDEO_SUFFIXES = [".mp4", ".mov", ".m4v", ".webm"];

export type MediaKind = "image" | "video";

export function mediaKindFromName(name: string | null | undefined): MediaKind {
  const raw = String(name || "").trim().toLowerCase().replace(/\\/g, "/");
  const base = raw.split("/").pop() || raw;
  const q = base.split("?")[0] || base;
  const dot = q.lastIndexOf(".");
  const suffix = dot >= 0 ? q.slice(dot) : "";
  return VIDEO_SUFFIXES.includes(suffix) ? "video" : "image";
}

export function isVideoUri(uri: string | null | undefined): boolean {
  return mediaKindFromName(uri) === "video";
}

export function videoMimeType(name: string | null | undefined): string {
  const raw = String(name || "").trim().toLowerCase();
  if (raw.endsWith(".mov")) return "video/quicktime";
  if (raw.endsWith(".webm")) return "video/webm";
  if (raw.endsWith(".m4v")) return "video/x-m4v";
  return "video/mp4";
}

export function splitMediaUris(uris: string[]): { images: string[]; videos: string[] } {
  const images: string[] = [];
  const videos: string[] = [];
  for (const uri of uris) {
    if (isVideoUri(uri)) videos.push(uri);
    else images.push(uri);
  }
  return { images, videos };
}
