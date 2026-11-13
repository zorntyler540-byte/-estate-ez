import { CTBIDS_MAX_PHOTOS_PER_LOT } from "../catalogLimits";

/** Most photos one camera-roll import may pick (5 full lots of 40). */
export const IMPORT_MAX_PICKS = 200;
/** A pause this long between shots usually means the next lot. */
export const IMPORT_DEFAULT_GAP_MS = 2 * 60_000;
export const IMPORT_GAP_CHOICES_MS = [30_000, 60_000, 2 * 60_000, 5 * 60_000] as const;

export type ImportPick = {
  /** Stable per pick (asset id, else the picker uri). */
  key: string;
  uri: string;
  width?: number;
  height?: number;
  /** When the shot was taken (EXIF), or null when the picker gave no date. */
  takenAt: number | null;
  /** Camera file name when the picker has one (``IMG_3839.JPG``). */
  fileName?: string | null;
  /** 0-based position in the picker result (selection order on iOS 15+). */
  pickIndex: number;
};

export type ImportGroupMode = "one" | "gap";

export type ImportGroup = {
  picks: ImportPick[];
  /** Part of a lot that went over the per-lot photo cap. */
  capped: boolean;
  /** Why this lot starts here: only ``auto`` / ``manual`` starts can be joined back. */
  start: "first" | "auto" | "manual" | "cap";
};

/** ``2024:05:01 13:45:10`` (EXIF, local time) → epoch ms. */
export function parseExifDate(raw: unknown, offset?: unknown): number | null {
  if (typeof raw !== "string") return null;
  const m = raw.trim().match(/^(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/);
  if (!m) return null;
  const [, y, mo, d, h, mi, s, frac] = m;
  const ms = frac ? Math.round(Number(`0.${frac}`) * 1000) : 0;
  const off = typeof offset === "string" ? offset.trim().match(/^([+-])(\d{2}):?(\d{2})$/) : null;
  let t: number;
  if (off) {
    const sign = off[1] === "-" ? -1 : 1;
    const offMs = sign * (Number(off[2]) * 60 + Number(off[3])) * 60_000;
    t = Date.UTC(+y!, +mo! - 1, +d!, +h!, +mi!, +s!, ms) - offMs;
  } else {
    t = new Date(+y!, +mo! - 1, +d!, +h!, +mi!, +s!, ms).getTime();
  }
  return Number.isFinite(t) && t > 0 ? t : null;
}

/**
 * Capture time from a picker asset's ``exif``. iOS nests tags under ``{Exif}``; Android
 * returns them flat.
 */
export function exifTakenAtMs(exif: Record<string, unknown> | null | undefined): number | null {
  if (!exif || typeof exif !== "object") return null;
  const nested = (exif["{Exif}"] ?? null) as Record<string, unknown> | null;
  const tiff = (exif["{TIFF}"] ?? null) as Record<string, unknown> | null;
  const sources = [nested, exif].filter(Boolean) as Record<string, unknown>[];
  for (const src of sources) {
    const t = parseExifDate(src.DateTimeOriginal, src.OffsetTimeOriginal ?? src.OffsetTime);
    if (t) return t;
  }
  for (const src of [nested, tiff, exif].filter(Boolean) as Record<string, unknown>[]) {
    const t = parseExifDate(src.DateTimeDigitized ?? src.DateTime, src.OffsetTime);
    if (t) return t;
  }
  return null;
}

/** Trailing number in a camera name (``IMG_3839.JPG`` → 3839). */
export function cameraShotNumber(fileName: string | null | undefined): number | null {
  if (!fileName) return null;
  const match = fileName.match(/(\d+)(?=\D*$)/);
  if (!match) return null;
  const n = parseInt(match[1]!, 10);
  return Number.isFinite(n) ? n : null;
}

/**
 * Photo order inside the import. Camera file numbers win (IMG_9 before IMG_10) because the
 * Photos picker does not always return shooting order. Otherwise time-gap mode sorts by
 * capture time, and one-lot mode keeps pick order.
 */
export function orderPicks(picks: readonly ImportPick[], mode: ImportGroupMode): ImportPick[] {
  const byPick = [...picks].sort((a, b) => a.pickIndex - b.pickIndex);
  const numbered = byPick.filter((p) => cameraShotNumber(p.fileName) != null).length;
  if (numbered >= 2 && numbered * 2 >= byPick.length) {
    return byPick.sort((a, b) => {
      const na = cameraShotNumber(a.fileName);
      const nb = cameraShotNumber(b.fileName);
      if (na != null && nb != null && na !== nb) return na - nb;
      if (na != null && nb == null) return -1;
      if (na == null && nb != null) return 1;
      return a.pickIndex - b.pickIndex;
    });
  }
  if (mode !== "gap" || !byPick.every((p) => p.takenAt != null)) return byPick;
  return byPick.sort((a, b) => a.takenAt! - b.takenAt! || a.pickIndex - b.pickIndex);
}

/** Next SKUs from one starting value (``1001`` or ``DK1001``). Null when it does not end in a number. */
export function skuSequence(start: string, count: number): string[] | null {
  const match = start.trim().match(/^(.*?)(\d+)$/);
  if (!match || count < 1) return null;
  const prefix = match[1] ?? "";
  const width = match[2]!.length;
  let n = parseInt(match[2]!, 10);
  if (!Number.isFinite(n)) return null;
  const out: string[] = [];
  for (let i = 0; i < count; i++) {
    out.push(prefix + String(n).padStart(width, "0"));
    n += 1;
  }
  return out;
}

/** Keys of photos that start a new lot by themselves (before manual toggles). */
export function autoSplitKeys(ordered: readonly ImportPick[], mode: ImportGroupMode, gapMs: number): Set<string> {
  const out = new Set<string>();
  if (mode !== "gap") return out;
  for (let i = 1; i < ordered.length; i++) {
    const prev = ordered[i - 1]!.takenAt;
    const cur = ordered[i]!.takenAt;
    if (prev != null && cur != null && cur - prev >= gapMs) out.add(ordered[i]!.key);
  }
  return out;
}

/**
 * Group the picks into lots. ``toggled`` flips the boundary before a photo: it starts a new lot
 * there, or joins an automatic split back. Lots over the cap are cut into ≤cap parts.
 */
/** Put picks in an explicit key order. Keys that are missing are skipped; leftover picks follow. */
export function picksInKeyOrder(picks: readonly ImportPick[], keys: readonly string[]): ImportPick[] {
  const byKey = new Map(picks.map((p) => [p.key, p]));
  const out: ImportPick[] = [];
  const seen = new Set<string>();
  for (const key of keys) {
    const pick = byKey.get(key);
    if (!pick || seen.has(key)) continue;
    seen.add(key);
    out.push(pick);
  }
  for (const pick of picks) {
    if (!seen.has(pick.key)) out.push(pick);
  }
  return out;
}

/** Swap a photo with its neighbor. Null when it is already at that end. */
export function moveImportKey(keys: readonly string[], key: string, delta: -1 | 1): string[] | null {
  const from = keys.indexOf(key);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= keys.length) return null;
  const next = keys.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved!);
  return next;
}

export function planImportGroups(
  picks: readonly ImportPick[],
  opts: {
    mode: ImportGroupMode;
    gapMs?: number;
    toggled?: ReadonlySet<string>;
    /** Suggested splits (blank frame or tape roll). A toggle undoes one. */
    suggested?: ReadonlySet<string>;
    /** When set, this order replaces camera-file and capture-time sorting. */
    manualOrder?: readonly string[] | null;
    maxPerLot?: number;
  }
): ImportGroup[] {
  const ordered = opts.manualOrder?.length
    ? picksInKeyOrder(picks, opts.manualOrder)
    : orderPicks(picks, opts.mode);
  if (!ordered.length) return [];
  const cap = Math.max(1, opts.maxPerLot ?? CTBIDS_MAX_PHOTOS_PER_LOT);
  const auto = autoSplitKeys(ordered, opts.mode, opts.gapMs ?? IMPORT_DEFAULT_GAP_MS);
  for (const key of opts.suggested ?? []) {
    if (ordered[0]?.key !== key) auto.add(key);
  }
  const toggled = opts.toggled ?? new Set<string>();
  const raw: { picks: ImportPick[]; start: ImportGroup["start"] }[] = [];
  for (let i = 0; i < ordered.length; i++) {
    const p = ordered[i]!;
    const split = i > 0 && auto.has(p.key) !== toggled.has(p.key);
    if (i === 0) raw.push({ picks: [], start: "first" });
    else if (split) raw.push({ picks: [], start: auto.has(p.key) ? "auto" : "manual" });
    raw[raw.length - 1]!.picks.push(p);
  }
  const out: ImportGroup[] = [];
  for (const g of raw) {
    if (g.picks.length <= cap) {
      out.push({ picks: g.picks, capped: false, start: g.start });
      continue;
    }
    for (let i = 0; i < g.picks.length; i += cap) {
      out.push({ picks: g.picks.slice(i, i + cap), capped: true, start: i === 0 ? g.start : "cap" });
    }
  }
  return out;
}

/** Whether a photo starts a lot in this plan (drives the "New lot here" toggle label). */
export function groupStartKeys(groups: readonly ImportGroup[]): Set<string> {
  return new Set(groups.map((g) => g.picks[0]?.key).filter(Boolean) as string[]);
}

/**
 * ``captured_at`` for each imported photo: its EXIF time, or the import time plus its position
 * so order stays stable when the picker gave no date.
 */
export function importCapturedAt(pick: ImportPick, position: number, importStartedAt: number): number {
  return pick.takenAt ?? importStartedAt + position;
}
