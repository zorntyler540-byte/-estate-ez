import { API_BASE, apiUploadFiles, parseApiErrorMessage } from "./api";

export async function fetchLotPhotoBlob(lotId: string, photoId: string, token: string): Promise<Blob> {
  // Prefer signed R2 URL when CORS allows reading the object from the web origin.
  try {
    const mediaRes = await fetch(
      `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/media-url`,
      {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store"
      }
    );
    if (mediaRes.ok) {
      const data = (await mediaRes.json()) as { url?: string };
      if (data?.url) {
        const r2 = await fetch(data.url, { cache: "no-store" });
        if (r2.ok) return r2.blob();
      }
    }
  } catch {
    // Fall through to API proxy (works without R2 CORS).
  }

  const res = await fetch(
    `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/download`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store"
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(parseApiErrorMessage(text, `Photo download failed (${res.status})`));
  }
  return res.blob();
}

export function catalogPhotoFileName(originalName: string): string {
  const base = (originalName || "photo").trim() || "photo";
  if (base.toLowerCase().endsWith(".jpg") || base.toLowerCase().endsWith(".jpeg")) {
    return base;
  }
  const stem = base.replace(/\.[^.]+$/, "") || "photo";
  return `${stem}.jpg`;
}

export async function replaceLotPhotoBlob(
  lotId: string,
  photoId: string,
  token: string,
  blob: Blob,
  originalName: string
): Promise<void> {
  const fd = new FormData();
  fd.append("file", blob, catalogPhotoFileName(originalName));
  await apiUploadFiles(
    `/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/replace`,
    token,
    fd,
    120_000
  );
}
