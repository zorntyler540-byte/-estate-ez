import {
  nextCategorySort,
  sortCategoryRows,
  type CategorySortRow
} from "./saleResultsSort";

const rows: CategorySortRow[] = [
  {
    category: "Firearms",
    posted_count: 39,
    sold_count: 23,
    unsold_count: 8,
    live_count: 8,
    revenue: 4988.52,
    avg_price: 216.89,
    min_price: 25,
    max_price: 710,
    sell_through: 0.59
  },
  {
    category: "Costume Jewelry",
    posted_count: 134,
    sold_count: 56,
    unsold_count: 9,
    live_count: 69,
    revenue: 1081.7,
    avg_price: 19.32,
    min_price: 1,
    max_price: 176.01,
    sell_through: 0.42
  },
  {
    category: "Rings",
    posted_count: 3,
    sold_count: 3,
    unsold_count: 0,
    live_count: 0,
    revenue: 1219.5,
    avg_price: 406.5,
    min_price: 7.5,
    max_price: 970,
    sell_through: 1
  }
];

const byHammer = sortCategoryRows(rows, "hammer", "desc");
if (byHammer[0].category !== "Firearms") throw new Error(`hammer desc: ${byHammer[0].category}`);
const byAvg = sortCategoryRows(rows, "avg", "desc");
if (byAvg[0].category !== "Rings") throw new Error(`avg desc: ${byAvg[0].category}`);
const byName = sortCategoryRows(rows, "category", "asc");
if (byName[0].category !== "Costume Jewelry") throw new Error(`name asc: ${byName[0].category}`);
const toggle = nextCategorySort("hammer", "desc", "hammer");
if (toggle.dir !== "asc") throw new Error("toggle should flip to asc");
const switchCol = nextCategorySort("hammer", "asc", "avg");
if (switchCol.key !== "avg" || switchCol.dir !== "desc") throw new Error("avg should default desc");

console.log("saleResultsSort selftest ok");
