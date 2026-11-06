import {
  donutArcs,
  hammerTimeBuckets,
  outcomeSlices,
  topAvgBars,
  topHammerBars
} from "./saleResultsCharts";

const slices = outcomeSlices([
  { outcome: "sold" },
  { outcome: "sold" },
  { outcome: "unsold" },
  { outcome: "live" }
]);
if (slices.map((s) => s.count).join(",") !== "2,1,1") throw new Error(`slices ${slices.map((s) => s.count)}`);

const bars = topHammerBars([
  { category: "Firearms", revenue: 4000, sold_count: 10, posted_count: 12 },
  { category: "Rings", revenue: 1200, sold_count: 3, posted_count: 3 },
  { category: "Z", revenue: 10, sold_count: 1, posted_count: 1 },
  { category: "Empty", revenue: 0, sold_count: 0, posted_count: 4 }
]);
if (bars.map((b) => b.category).join(",") !== "Firearms,Rings,Z") {
  throw new Error(`hammer cats ${bars.map((b) => b.category)}`);
}
if (bars[2].hammer !== 10) throw new Error("named remainder");

const avgs = topAvgBars([
  { category: "Rings", revenue: 1200, sold_count: 3, posted_count: 3, avg_price: 400 },
  { category: "Firearms", revenue: 4000, sold_count: 20, posted_count: 22, avg_price: 200 },
  { category: "Costume", revenue: 80, sold_count: 8, posted_count: 40, avg_price: 10 }
]);
if (avgs.map((b) => b.category).join(",") !== "Rings,Firearms,Costume") {
  throw new Error(`avg cats ${avgs.map((b) => b.category)}`);
}
if (Math.abs(avgs[2].avg - 10) > 0.01) throw new Error(`avg remainder ${avgs[2].avg}`);

const months = hammerTimeBuckets(
  [
    { outcome: "sold", sold_price: 20, posted_at: "2026-01-15T00:00:00Z" },
    { outcome: "sold", sold_price: 5, posted_at: "2026-01-20T00:00:00Z" },
    { outcome: "unsold", sold_price: 99, posted_at: "2026-01-20T00:00:00Z" },
    { outcome: "sold", sold_price: 8, posted_at: "2026-02-01T00:00:00Z" }
  ],
  "2026-01-01T00:00:00Z",
  "2026-03-01T00:00:00Z"
);
if (months.length !== 2) throw new Error(`months ${months.length}`);
if (Math.abs(months[0].hammer - 25) > 0.01) throw new Error(`jan hammer ${months[0].hammer}`);
if (Math.abs(months[0].avg - 12.5) > 0.01) throw new Error(`jan avg ${months[0].avg}`);

const C = 2 * Math.PI * 40;
const arcs = donutArcs(slices, C);
const arcSum = arcs.reduce((s, a) => s + a.dash, 0);
if (Math.abs(arcSum - C) > 0.01) throw new Error("donut should use full circumference");

console.log("saleResultsCharts selftest ok");
