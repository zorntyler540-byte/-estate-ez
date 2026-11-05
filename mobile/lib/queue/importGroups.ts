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

/**
 * Photo order inside the import. Time-gap grouping needs shooting order, so it sorts by
 * capture time when every pick has one; otherwise (and for "one lot") pick order is kept.
 */
export function orderPicks(picks: readonly ImportPick[], mode: ImportGroupMode): ImportPick[] {
  const byPick = [...picks].sort((a, b) => a.pickIndex - b.pickIndex);
  if (mode !== "gap" || !byPick.every((p) => p.takenAt != null)) return byPick;
  return byPick.sort((a, b) => a.takenAt! - b.takenAt! || a.pickIndex - b.pickIndex);
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
export function planImportGroups(
  picks: readonly ImportPick[],
  opts: { mode: ImportGroupMode; gapMs?: number; toggled?: ReadonlySet<string>; maxPerLot?: number }
): ImportGroup[] {
  const ordered = orderPicks(picks, opts.mode);
  if (!ordered.length) return [];
  const cap = Math.max(1, opts.maxPerLot ?? CTBIDS_MAX_PHOTOS_PER_LOT);
  const auto = autoSplitKeys(ordered, opts.mode, opts.gapMs ?? IMPORT_DEFAULT_GAP_MS);
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
