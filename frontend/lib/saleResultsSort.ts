export type SortDir = "asc" | "desc";

export type CategorySortKey =
  | "category"
  | "posted"
  | "sold"
  | "unsold"
  | "live"
  | "sell_through"
  | "hammer"
  | "avg"
  | "low"
  | "high";

export type CategorySortRow = {
  category: string;
  posted_count?: number;
  sold_count: number;
  unsold_count?: number;
  live_count?: number;
  revenue: number;
  avg_price: number;
  min_price: number;
  max_price: number;
  sell_through?: number;
};

const NUMERIC_CATEGORY_KEYS: ReadonlySet<CategorySortKey> = new Set([
  "posted",
  "sold",
  "unsold",
  "live",
  "sell_through",
  "hammer",
  "avg",
  "low",
  "high"
]);

export function defaultDirForCategoryKey(key: CategorySortKey): SortDir {
  return NUMERIC_CATEGORY_KEYS.has(key) ? "desc" : "asc";
}

export function nextCategorySort(
  currentKey: CategorySortKey,
  currentDir: SortDir,
  clicked: CategorySortKey
): { key: CategorySortKey; dir: SortDir } {
  if (clicked === currentKey) {
    return { key: currentKey, dir: currentDir === "desc" ? "asc" : "desc" };
  }
  return { key: clicked, dir: defaultDirForCategoryKey(clicked) };
}

export function categorySortValue(row: CategorySortRow, key: CategorySortKey): string | number {
  switch (key) {
    case "category":
      return (row.category || "").toLowerCase();
    case "posted":
      return row.posted_count ?? 0;
    case "sold":
      return row.sold_count ?? 0;
    case "unsold":
      return row.unsold_count ?? 0;
    case "live":
      return row.live_count ?? 0;
    case "sell_through":
      return row.sell_through ?? 0;
    case "hammer":
      return row.revenue ?? 0;
    case "avg":
      return row.avg_price ?? 0;
    case "low":
      return row.min_price ?? 0;
    case "high":
      return row.max_price ?? 0;
  }
}

export function sortCategoryRows(
  rows: CategorySortRow[],
  key: CategorySortKey,
  dir: SortDir
): CategorySortRow[] {
  const copy = rows.slice();
  copy.sort((a, b) => {
    const av = categorySortValue(a, key);
    const bv = categorySortValue(b, key);
    let cmp =
      typeof av === "string" && typeof bv === "string"
        ? av.localeCompare(bv)
        : Number(av) - Number(bv);
    if (cmp === 0) cmp = (a.category || "").localeCompare(b.category || "");
    return dir === "asc" ? cmp : -cmp;
  });
  return copy;
}

export type DocketSortKey =
  | "sku"
  | "title"
  | "category"
  | "outcome"
  | "hammer"
  | "sale"
  | "source"
  | "posted"
  | "catalogger";

export type DocketSortRow = {
  sku?: string | null;
  auction_title?: string;
  category: string;
  outcome?: string;
  sold_price: number;
  sale_name?: string;
  source?: string;
  posted_at?: string | null;
  user_email?: string;
};

const NUMERIC_DOCKET_KEYS: ReadonlySet<DocketSortKey> = new Set(["hammer"]);

export function defaultDirForDocketKey(key: DocketSortKey): SortDir {
  return NUMERIC_DOCKET_KEYS.has(key) ? "desc" : "asc";
}

export function nextDocketSort(
  currentKey: DocketSortKey,
  currentDir: SortDir,
  clicked: DocketSortKey
): { key: DocketSortKey; dir: SortDir } {
  if (clicked === currentKey) {
    return { key: currentKey, dir: currentDir === "desc" ? "asc" : "desc" };
  }
  return { key: clicked, dir: defaultDirForDocketKey(clicked) };
}

function docketSortValue(row: DocketSortRow, key: DocketSortKey): string | number {
  switch (key) {
    case "sku":
      return (row.sku || "").toLowerCase();
    case "title":
      return (row.auction_title || "").toLowerCase();
    case "category":
      return (row.category || "").toLowerCase();
    case "outcome":
      return (row.outcome || "").toLowerCase();
    case "hammer":
      return (row.outcome || "").toLowerCase() === "sold" ? row.sold_price || 0 : 0;
    case "sale":
      return (row.sale_name || "").toLowerCase();
    case "source":
      return (row.source || "").toLowerCase();
    case "posted":
      return row.posted_at || "";
    case "catalogger":
      return (row.user_email || "").toLowerCase();
  }
}

export function sortDocketRows<T extends DocketSortRow>(rows: T[], key: DocketSortKey, dir: SortDir): T[] {
  const copy = rows.slice();
  copy.sort((a, b) => {
    const av = docketSortValue(a, key);
    const bv = docketSortValue(b, key);
    let cmp =
      typeof av === "string" && typeof bv === "string"
        ? av.localeCompare(bv)
        : Number(av) - Number(bv);
    if (cmp === 0) cmp = (a.sku || "").localeCompare(b.sku || "");
    return dir === "asc" ? cmp : -cmp;
  });
  return copy;
}
