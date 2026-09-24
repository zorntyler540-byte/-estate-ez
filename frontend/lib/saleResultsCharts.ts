export type OutcomeSlice = {
  key: "sold" | "unsold" | "live";
  label: string;
  count: number;
  color: string;
};

export type HammerBar = {
  category: string;
  hammer: number;
  avg: number;
  sold: number;
  posted: number;
};

export type TimeBucket = {
  key: string;
  label: string;
  hammer: number;
  avg: number;
  sold: number;
};

const OUTCOME_COLORS = {
  sold: "#4ade80",
  unsold: "#a1a1aa",
  live: "#60a5fa"
} as const;

export function outcomeSlices(items: Array<{ outcome?: string }>): OutcomeSlice[] {
  const sold = items.filter((i) => (i.outcome || "").toLowerCase() === "sold").length;
  const unsold = items.filter((i) => (i.outcome || "").toLowerCase() === "unsold").length;
  const live = items.filter((i) => (i.outcome || "").toLowerCase() === "live").length;
  return [
    { key: "sold", label: "Sold", count: sold, color: OUTCOME_COLORS.sold },
    { key: "unsold", label: "Unsold", count: unsold, color: OUTCOME_COLORS.unsold },
    { key: "live", label: "Live", count: live, color: OUTCOME_COLORS.live }
  ];
}

export function topHammerBars(
  categories: Array<{
    category: string;
    revenue: number;
    sold_count: number;
    posted_count?: number;
    avg_price?: number;
  }>,
  limit = 10
): HammerBar[] {
  const ranked = categories
    .map((c) => ({
      category: c.category,
      hammer: c.revenue || 0,
      avg: c.avg_price ?? (c.sold_count ? (c.revenue || 0) / c.sold_count : 0),
      sold: c.sold_count || 0,
      posted: c.posted_count ?? 0
    }))
    .sort((a, b) => b.hammer - a.hammer || b.sold - a.sold);
  if (ranked.length <= limit) return ranked;
  const head = ranked.slice(0, limit);
  const rest = ranked.slice(limit);
  const sold = rest.reduce((s, r) => s + r.sold, 0);
  const hammer = rest.reduce((s, r) => s + r.hammer, 0);
  const other: HammerBar = {
    category: "Other",
    hammer,
    avg: sold ? hammer / sold : 0,
    sold,
    posted: rest.reduce((s, r) => s + r.posted, 0)
  };
  return other.hammer > 0 || other.sold > 0 ? [...head, other] : head;
}

export function topAvgBars(
  categories: Array<{
    category: string;
    revenue: number;
    sold_count: number;
    posted_count?: number;
    avg_price?: number;
  }>,
  limit = 10
): HammerBar[] {
  const ranked = categories
    .map((c) => ({
      category: c.category,
      hammer: c.revenue || 0,
      avg: c.avg_price ?? (c.sold_count ? (c.revenue || 0) / c.sold_count : 0),
      sold: c.sold_count || 0,
      posted: c.posted_count ?? 0
    }))
    .filter((c) => c.sold > 0 && c.avg > 0)
    .sort((a, b) => b.avg - a.avg || b.hammer - a.hammer);
  if (ranked.length <= limit) return ranked;
  const head = ranked.slice(0, limit);
  const rest = ranked.slice(limit);
  const sold = rest.reduce((s, r) => s + r.sold, 0);
  const hammer = rest.reduce((s, r) => s + r.hammer, 0);
  const other: HammerBar = {
    category: "Other",
    hammer,
    avg: sold ? hammer / sold : 0,
    sold,
    posted: rest.reduce((s, r) => s + r.posted, 0)
  };
  return other.avg > 0 ? [...head, other] : head;
}

export function hammerTimeBuckets(
  items: Array<{ outcome?: string; sold_price?: number; posted_at?: string | null }>,
  sinceIso: string,
  untilIso: string
): TimeBucket[] {
  const sold = items.filter((i) => (i.outcome || "").toLowerCase() === "sold");
  const since = new Date(sinceIso).getTime();
  const until = new Date(untilIso).getTime();
  const spanDays =
    Number.isFinite(since) && Number.isFinite(until) && until > since ? (until - since) / 86400000 : 90;
  const byYear = spanDays > 400;

  const map = new Map<string, TimeBucket>();
  for (const item of sold) {
    const raw = item.posted_at;
    if (!raw) continue;
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) continue;
    const key = byYear
      ? String(d.getUTCFullYear())
      : `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    const label = byYear
      ? key
      : d.toLocaleDateString(undefined, { month: "short", year: "2-digit", timeZone: "UTC" });
    const row = map.get(key) || { key, label, hammer: 0, avg: 0, sold: 0 };
    row.hammer += Number(item.sold_price) || 0;
    row.sold += 1;
    row.avg = row.sold ? row.hammer / row.sold : 0;
    map.set(key, row);
  }
  return Array.from(map.values()).sort((a, b) => a.key.localeCompare(b.key));
}

export type DonutArc = {
  key: string;
  color: string;
  dash: number;
  offset: number;
};

export function donutArcs(slices: OutcomeSlice[], circumference: number): DonutArc[] {
  const total = slices.reduce((s, x) => s + x.count, 0);
  if (total < 1) return [];
  let cursor = 0;
  return slices
    .filter((s) => s.count > 0)
    .map((s) => {
      const dash = (s.count / total) * circumference;
      const arc: DonutArc = { key: s.key, color: s.color, dash, offset: -cursor };
      cursor += dash;
      return arc;
    });
}
