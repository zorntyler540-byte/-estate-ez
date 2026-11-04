/** Apple HIG / Material minimum touch target. */
export const MIN_TAP_PX = 44;

/** Even ``hitSlop`` that grows a control of ``size`` pt to at least 44 pt. */
export function hitSlopFor(size: number): { top: number; bottom: number; left: number; right: number } {
  const pad = Math.max(0, Math.ceil((MIN_TAP_PX - Math.max(0, size)) / 2));
  return { top: pad, bottom: pad, left: pad, right: pad };
}

/** Speak a status change once (skip blanks and repeats of the same text). */
export function nextAnnouncement(previous: string | null, next: string | null | undefined): string | null {
  const text = (next || "").trim();
  if (!text || text === (previous || "").trim()) return null;
  return text;
}
