export function isHeicLikeFile(file: File): boolean {
  const type = (file.type || "").toLowerCase();
  if (type.includes("heic") || type.includes("heif")) return true;
  return /\.heic$|\.heif$/i.test(file.name || "");
}

/** Decode iPhone HEIC/HEIF in the browser (Safari cannot paint HEIC in ``<img>``). */
export async function convertHeicFileToJpeg(file: File, outName: string): Promise<File | null> {
  if (typeof window === "undefined") return null;
  try {
    const { default: heic2any } = await import("heic2any");
    const result = await heic2any({ blob: file, toType: "image/jpeg", quality: 0.92 });
    const blob = Array.isArray(result) ? result[0] : result;
    if (!blob) return null;
    const name = outName.toLowerCase().endsWith(".jpg") ? outName : `${outName.replace(/\.[a-z0-9]{2,6}$/i, "")}.jpg`;
    return new File([blob], name, { type: "image/jpeg", lastModified: Date.now() });
  } catch {
    return null;
  }
}
