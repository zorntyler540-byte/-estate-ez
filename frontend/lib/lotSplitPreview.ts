/**
 * Lot groups a website batch will start with, before cataloging reads estate tape.
 *
 * A new folder starts a lot. The folder the user picked is the first path segment
 * (``Sale/035/a.jpg``); the lot folder is the next one (``035``), matching
 * ``_subfolder_batch_key``. A blank sheet is a divider and is left out of every lot:
 * a near-uniform sheet, or ordinary paper that is still low-detail. Tape numbers
 * are not known until cataloging, so they are not a split here.
 */

export type LotSplit =
  | { kind: "lot"; lotNumber: number; sku: string | null; indexes: number[] }
  | { kind: "divider"; index: number };

/** Same thresholds as ``frame_is_confident_blank`` in ``lot_dividers.py``. */
const PROBE_EDGE = 160;
const EDGE_LEVEL = 40;
const CENTER_MARGIN = 0.2;
const WHITE_LUMA = 220;
const DARK_LUMA = 28;
const CONFIDENT_WHITE_MEAN = 226;
const CONFIDENT_WHITE_STDEV = 7.5;
const CONFIDENT_WHITE_FRACTION = 0.985;
const CONFIDENT_WHITE_CENTER_EDGE = 0.035;
const CONFIDENT_WHITE_FRAME_EDGE = 0.05;
const CONFIDENT_DARK_MEAN = 16;
const CONFIDENT_DARK_STDEV = 6;
const CONFIDENT_DARK_FRACTION = 0.98;
const CONFIDENT_DARK_CENTER_EDGE = 0.03;
const CONFIDENT_DARK_FRAME_EDGE = 0.045;
/** Same cut as ``frame_looks_blank``: a lit sheet of paper, not a busy item. */
const LOOKS_BLANK_CENTER_EDGE = 0.04;
const LOOKS_BLANK_FRAME_EDGE = 0.06;

export type BlankProbe = {
  centerEdges: number;
  frameEdges: number;
  meanLuma: number;
  stdevLuma: number;
  whiteFraction: number;
  darkFraction: number;
};

/** Lot folder for one upload path. ``Sale/035/a.jpg`` and ``035/a.jpg`` are both ``035``. */
export function lotFolderKey(label: string): string {
  const parts = label.replace(/\\/g, "/").split("/").filter(Boolean);
  parts.pop();
  if (parts.length >= 2) return parts[1] || "";
  return parts[0] || "";
}

/** 2–6 digit folder names are the lot number when that option is on. */
export function inheritedFolderSku(key: string): string | null {
  return /^\d{2,6}$/.test(key) ? key : null;
}

export function previewLotSplits(
  labels: readonly string[],
  options?: {
    dividerIndexes?: ReadonlySet<number>;
    singleLot?: boolean;
    inheritSku?: boolean;
  }
): LotSplit[] {
  if (!labels.length) return [];
  if (options?.singleLot) {
    return [{ kind: "lot", lotNumber: 1, sku: null, indexes: labels.map((_, index) => index) }];
  }
  const dividers = options?.dividerIndexes ?? new Set<number>();
  const out: LotSplit[] = [];
  let current: number[] = [];
  let lotNumber = 0;
  let previousKey: string | null = null;
  let currentSku: string | null = null;

  const close = () => {
    if (!current.length) return;
    lotNumber += 1;
    out.push({ kind: "lot", lotNumber, sku: currentSku, indexes: current });
    current = [];
    currentSku = null;
  };

  labels.forEach((label, index) => {
    const key = lotFolderKey(label);
    if (previousKey !== null && key !== previousKey && current.length) close();
    if (dividers.has(index)) {
      close();
      out.push({ kind: "divider", index });
      previousKey = key;
      return;
    }
    if (!current.length) currentSku = options?.inheritSku ? inheritedFolderSku(key) : null;
    current.push(index);
    previousKey = key;
  });
  close();
  return out;
}

/** One line above the grouped list. */
export function lotSplitSummary(options: {
  lotCount: number;
  dividerCount: number;
  likelyCount?: number;
  singleLot?: boolean;
  checkingBlank?: boolean;
}): string {
  if (options.singleLot) return "These photos stay one lot, in this order.";
  const lots = `${options.lotCount} lot${options.lotCount === 1 ? "" : "s"}`;
  const likely = options.likelyCount ?? 0;
  const sure = Math.max(0, options.dividerCount - likely);
  let sheets: string;
  if (options.dividerCount === 0) {
    sheets = options.checkingBlank ? "Checking blank sheets." : "No blank sheet found.";
  } else if (likely > 0 && sure === 0) {
    sheets = `${likely} likely blank sheet${likely === 1 ? "" : "s"} left out.`;
  } else if (likely > 0) {
    sheets = `${options.dividerCount} blank sheets left out, ${likely} still to confirm.`;
  } else {
    sheets = `${options.dividerCount} blank sheet${options.dividerCount === 1 ? "" : "s"} left out.`;
  }
  return `${lots} from folders and blank sheets. ${sheets} A new tape number can still start another lot while cataloging.`;
}

function edgeFraction(edges: Uint8Array, width: number, height: number, margin: number): number {
  const x0 = Math.floor(width * margin);
  const y0 = Math.floor(height * margin);
  const x1 = width - x0;
  const y1 = height - y0;
  let strong = 0;
  let total = 0;
  for (let y = y0; y < y1; y += 1) {
    const row = y * width;
    for (let x = x0; x < x1; x += 1) {
      total += 1;
      if (edges[row + x]! >= EDGE_LEVEL) strong += 1;
    }
  }
  return total ? strong / total : 0;
}

/**
 * Confident blank from a grayscale thumbnail. Uniform white paper and a covered
 * lens pass. A creased sheet or an item does not — cataloging still checks those.
 */
export function probeFromGray(gray: Uint8Array | Uint8ClampedArray, width: number, height: number): BlankProbe | null {
  if (width < 16 || height < 16) return null;
  const edges = new Uint8Array(width * height);
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const i = y * width + x;
      const value =
        gray[i]! * 8 -
        gray[i - width - 1]! -
        gray[i - width]! -
        gray[i - width + 1]! -
        gray[i - 1]! -
        gray[i + 1]! -
        gray[i + width - 1]! -
        gray[i + width]! -
        gray[i + width + 1]!;
      edges[i] = value <= 0 ? 0 : value >= 255 ? 255 : value;
    }
  }
  const crop = 2;
  const cw = width - crop * 2;
  const ch = height - crop * 2;
  if (cw < 8 || ch < 8) return null;
  const cropped = new Uint8Array(cw * ch);
  for (let y = 0; y < ch; y += 1) {
    cropped.set(edges.subarray((y + crop) * width + crop, (y + crop) * width + crop + cw), y * cw);
  }
  let sum = 0;
  let white = 0;
  let dark = 0;
  for (let i = 0; i < gray.length; i += 1) {
    const value = gray[i]!;
    sum += value;
    if (value >= WHITE_LUMA) white += 1;
    else if (value < DARK_LUMA) dark += 1;
  }
  const total = gray.length || 1;
  const mean = sum / total;
  let square = 0;
  for (let i = 0; i < gray.length; i += 1) {
    const delta = gray[i]! - mean;
    square += delta * delta;
  }
  return {
    centerEdges: edgeFraction(cropped, cw, ch, CENTER_MARGIN),
    frameEdges: edgeFraction(cropped, cw, ch, 0),
    meanLuma: mean,
    stdevLuma: Math.sqrt(square / total),
    whiteFraction: white / total,
    darkFraction: dark / total
  };
}

export function frameIsConfidentBlank(probe: BlankProbe | null): boolean {
  if (!probe) return false;
  const white =
    probe.meanLuma >= CONFIDENT_WHITE_MEAN &&
    probe.stdevLuma <= CONFIDENT_WHITE_STDEV &&
    probe.whiteFraction >= CONFIDENT_WHITE_FRACTION &&
    probe.centerEdges <= CONFIDENT_WHITE_CENTER_EDGE &&
    probe.frameEdges <= CONFIDENT_WHITE_FRAME_EDGE;
  const dark =
    probe.meanLuma <= CONFIDENT_DARK_MEAN &&
    probe.stdevLuma <= CONFIDENT_DARK_STDEV &&
    probe.darkFraction >= CONFIDENT_DARK_FRACTION &&
    probe.centerEdges <= CONFIDENT_DARK_CENTER_EDGE &&
    probe.frameEdges <= CONFIDENT_DARK_FRAME_EDGE;
  return white || dark;
}

/** Low-detail frame. A phone photo of paper passes; a table of objects does not. */
export function frameLooksBlank(probe: BlankProbe | null): boolean {
  if (!probe) return false;
  return probe.centerEdges <= LOOKS_BLANK_CENTER_EDGE && probe.frameEdges <= LOOKS_BLANK_FRAME_EDGE;
}

/**
 * A small item on a seamless light backdrop is also low-detail. Cataloging asks
 * the model before dropping it. The preview keeps it in the lot.
 */
function seamlessBackdropItem(probe: BlankProbe): boolean {
  return probe.whiteFraction >= 0.9 && probe.stdevLuma >= 18 && probe.centerEdges >= 0.015;
}

export type DividerKind = "blank" | "likely";

/**
 * ``blank`` is a near-uniform sheet. ``likely`` is paper with ordinary lighting
 * (the catalog still confirms it). A busy photo, a white plate, and an item on
 * a plain backdrop are neither.
 */
export function previewDividerKind(probe: BlankProbe | null): DividerKind | null {
  if (!probe) return null;
  if (frameIsConfidentBlank(probe)) return "blank";
  if (frameLooksBlank(probe) && !seamlessBackdropItem(probe)) return "likely";
  return null;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("preview image failed"));
    image.src = url;
  });
}

/** Blank-sheet kind for one file. HEIC is left for cataloging. */
export async function previewDividerFile(file: File): Promise<DividerKind | null> {
  if (typeof document === "undefined") return null;
  const name = file.name || "";
  const type = (file.type || "").toLowerCase();
  if (type.includes("heic") || type.includes("heif") || /\.heic$|\.heif$/i.test(name)) return null;
  const url = URL.createObjectURL(file);
  try {
    const image = await loadImage(url);
    const longest = Math.max(image.naturalWidth, image.naturalHeight);
    if (!longest) return null;
    const scale = Math.min(1, PROBE_EDGE / longest);
    const width = Math.max(1, Math.round(image.naturalWidth * scale));
    const height = Math.max(1, Math.round(image.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    context.drawImage(image, 0, 0, width, height);
    const pixels = context.getImageData(0, 0, width, height).data;
    const gray = new Uint8ClampedArray(width * height);
    for (let i = 0, pixel = 0; i < pixels.length; i += 4, pixel += 1) {
      gray[pixel] = Math.round(pixels[i]! * 0.299 + pixels[i + 1]! * 0.587 + pixels[i + 2]! * 0.114);
    }
    return previewDividerKind(probeFromGray(gray, width, height));
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}
