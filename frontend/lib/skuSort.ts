/** Natural SKU order: letters A–Z, then numbers as numbers (DK080 before DK81 before DK295). */

function skuTokens(sku: string): Array<string | number> {
  return sku
    .trim()
    .split(/(\d+)/)
    .filter(Boolean)
    .map((tok) => (/^\d+$/.test(tok) ? Number(tok) : tok.toLowerCase()));
}

export function compareSkus(a: string | null | undefined, b: string | null | undefined): number {
  const left = (a || "").trim();
  const right = (b || "").trim();
  if (!left && !right) return 0;
  if (!left) return 1;
  if (!right) return -1;
  const at = skuTokens(left);
  const bt = skuTokens(right);
  const n = Math.max(at.length, bt.length);
  for (let i = 0; i < n; i++) {
    const x = at[i];
    const y = bt[i];
    if (x === undefined) return 1;
    if (y === undefined) return -1;
    if (typeof x === "number" && typeof y === "number") {
      if (x !== y) return x - y;
      continue;
    }
    const xs = String(x);
    const ys = String(y);
    if (xs !== ys) return xs < ys ? -1 : 1;
  }
  return 0;
}

export function sortBySku<T extends { id?: string }>(
  rows: T[],
  skuOf: (row: T) => string | null | undefined
): T[] {
  return [...rows].sort((left, right) => {
    const c = compareSkus(skuOf(left), skuOf(right));
    if (c !== 0) return c;
    return String(left.id || "").localeCompare(String(right.id || ""));
  });
}
