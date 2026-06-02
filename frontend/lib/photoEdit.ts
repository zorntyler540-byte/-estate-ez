/** Client-side rotate/crop for catalog photos (canvas; no extra dependencies). */

export type CropRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

function canvasToJpegBlob(canvas: HTMLCanvasElement, quality = 0.92): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("Could not encode image"))),
      "image/jpeg",
      quality
    );
  });
}

export async function rotateImageBlob(blob: Blob, degrees: 90 | -90 | 180): Promise<Blob> {
  const bmp = await createImageBitmap(blob);
  const swap = degrees === 90 || degrees === -90;
  const canvas = document.createElement("canvas");
  canvas.width = swap ? bmp.height : bmp.width;
  canvas.height = swap ? bmp.width : bmp.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bmp.close();
    throw new Error("Canvas not available");
  }
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((degrees * Math.PI) / 180);
  ctx.drawImage(bmp, -bmp.width / 2, -bmp.height / 2);
  bmp.close();
  return canvasToJpegBlob(canvas);
}

export async function cropImageBlob(blob: Blob, rect: CropRect): Promise<Blob> {
  const bmp = await createImageBitmap(blob);
  const w = Math.max(1, Math.round(rect.width));
  const h = Math.max(1, Math.round(rect.height));
  const x = Math.max(0, Math.min(Math.round(rect.x), bmp.width - 1));
  const y = Math.max(0, Math.min(Math.round(rect.y), bmp.height - 1));
  const cw = Math.min(w, bmp.width - x);
  const ch = Math.min(h, bmp.height - y);
  const canvas = document.createElement("canvas");
  canvas.width = cw;
  canvas.height = ch;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bmp.close();
    throw new Error("Canvas not available");
  }
  ctx.drawImage(bmp, x, y, cw, ch, 0, 0, cw, ch);
  bmp.close();
  return canvasToJpegBlob(canvas);
}

export type DisplayPoint = { x: number; y: number };

/** Map a click on the displayed image (object-fit: contain) to natural pixel coordinates. */
export function displayPointToNatural(
  clientX: number,
  clientY: number,
  img: HTMLImageElement
): DisplayPoint | null {
  const rect = img.getBoundingClientRect();
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  if (!nw || !nh || rect.width <= 0 || rect.height <= 0) return null;

  const scale = Math.min(rect.width / nw, rect.height / nh);
  const dw = nw * scale;
  const dh = nh * scale;
  const ox = rect.left + (rect.width - dw) / 2;
  const oy = rect.top + (rect.height - dh) / 2;

  const lx = clientX - ox;
  const ly = clientY - oy;
  if (lx < 0 || ly < 0 || lx > dw || ly > dh) return null;

  return {
    x: (lx / dw) * nw,
    y: (ly / dh) * nh
  };
}

export function normalizeCropRect(a: DisplayPoint, b: DisplayPoint, nw: number, nh: number): CropRect {
  const x1 = Math.max(0, Math.min(a.x, b.x, nw));
  const y1 = Math.max(0, Math.min(a.y, b.y, nh));
  const x2 = Math.max(0, Math.min(Math.max(a.x, b.x), nw));
  const y2 = Math.max(0, Math.min(Math.max(a.y, b.y), nh));
  const width = Math.max(8, x2 - x1);
  const height = Math.max(8, y2 - y1);
  return {
    x: x1,
    y: y1,
    width: Math.min(width, nw - x1),
    height: Math.min(height, nh - y1)
  };
}
