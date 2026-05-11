/**
 * Shrink large camera JPEG/PNG/WebP in the browser before multipart upload so jobs send fewer
 * megabytes (upload progress moves faster; server still normalizes for catalog).
 */
export async function maybeDownscaleForUpload(
  file: File,
  opts?: { maxEdge?: number; jpegQuality?: number; minBytes?: number }
): Promise<File> {
  if (typeof createImageBitmap !== "function" || typeof document === "undefined") {
    return file;
  }
  const maxEdge = opts?.maxEdge ?? 2048;
  const jpegQuality = opts?.jpegQuality ?? 0.82;
  const minBytes = opts?.minBytes ?? 450_000;
  if (file.size < minBytes) return file;

  const nameLower = file.name.toLowerCase();
  if (
    !nameLower.endsWith(".jpg") &&
    !nameLower.endsWith(".jpeg") &&
    !nameLower.endsWith(".png") &&
    !nameLower.endsWith(".webp")
  ) {
    return file;
  }

  let bmp: ImageBitmap | null = null;
  try {
    try {
      bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      bmp = await createImageBitmap(file);
    }
    const longer = Math.max(bmp.width, bmp.height);
    const scale = longer > maxEdge ? maxEdge / longer : 1;
    const w = Math.max(1, Math.round(bmp.width * scale));
    const h = Math.max(1, Math.round(bmp.height * scale));

    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      bmp.close();
      return file;
    }
    ctx.drawImage(bmp, 0, 0, w, h);
    bmp.close();
    bmp = null;

    const blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob((b) => resolve(b), "image/jpeg", jpegQuality);
    });
    if (!blob || blob.size >= file.size * 0.92) {
      return file;
    }
    const base = file.name.replace(/\.[^.\\/]+$/, "");
    return new File([blob], `${base}.jpg`, { type: "image/jpeg", lastModified: file.lastModified });
  } catch {
    if (bmp) {
      try {
        bmp.close();
      } catch {
        /* ignore */
      }
    }
    return file;
  }
}
