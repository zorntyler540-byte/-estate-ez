import { compareSkus, sortBySku } from "./skuSort";

const mixed = [
  { id: "c", sku: "DK295SK" },
  { id: "a", sku: "DK080AD" },
  { id: "b", sku: "DK081AD" },
  { id: "d", sku: "DK082AD" },
  { id: "e", sku: "DK296SKCONT" },
  { id: "f", sku: "DK296SK" },
  { id: "g", sku: "K830J" },
  { id: "h", sku: "DK83" },
  { id: "i", sku: "DK083AD" },
  { id: "j", sku: null }
];

const ordered = sortBySku(mixed, (r) => r.sku).map((r) => r.sku);
const want = [
  "DK080AD",
  "DK081AD",
  "DK082AD",
  "DK083AD",
  "DK83",
  "DK295SK",
  "DK296SK",
  "DK296SKCONT",
  "K830J",
  null
];

if (JSON.stringify(ordered) !== JSON.stringify(want)) {
  throw new Error(`sku sort ${JSON.stringify(ordered)} !== ${JSON.stringify(want)}`);
}

if (compareSkus("DK086AD", "DK086AD") !== 0) {
  throw new Error("equal SKUs should tie");
}

console.log("skuSort selftest ok");
