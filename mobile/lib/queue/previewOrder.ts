/** Step through photos in capture order. Out-of-range deltas leave the index unchanged. */
export function stepCaptureIndex(index: number, total: number, delta: number): number {
  if (total <= 0) return 0;
  const next = index + delta;
  if (next < 0 || next >= total) return index;
  return next;
}
