/** Normalize a sale name the same way Auth persists it. */
export function normalizeSaleName(name?: string | null): string | null {
  const trimmed = name?.trim() || null;
  return trimmed;
}

/**
 * Whether ``setSelectedSale`` should write. Calling it on every /sales fetch
 * retriggers focus effects that depend on the selected sale and unmounts the
 * Sale list into a spinner — phones then cannot tap a row.
 */
export function selectedSaleNeedsSync(
  currentId: string | null,
  currentName: string | null,
  nextId: string | null,
  nextName?: string | null
): boolean {
  const name = nextId ? normalizeSaleName(nextName) : null;
  return nextId !== currentId || name !== (nextId ? currentName : null);
}
