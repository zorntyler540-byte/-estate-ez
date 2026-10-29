import {
  findUniqueSkuLot,
  isStillCatalogingMessage,
  shouldArchiveCatalogedDraft,
  shouldSkipReupload,
  uniqueSkuLotMap
} from "./archiveCataloged";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(isStillCatalogingMessage("On server — cataloging…") === true, "cataloging copy");
assert(isStillCatalogingMessage("Still cataloging on server…") === true, "still cataloging");
assert(isStillCatalogingMessage("On server — check the website") === true, "check website");
assert(isStillCatalogingMessage(null) === false, "empty is not cataloging");
assert(isStillCatalogingMessage("Photo file is missing") === false, "real error stays");

assert(
  shouldArchiveCatalogedDraft({
    status: "uploaded",
    errorMessage: null,
    confirmedOnServer: true
  }) === true,
  "confirmed uploaded archives"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "uploaded",
    errorMessage: "On server — cataloging…",
    confirmedOnServer: true
  }) === true,
  "leftover cataloging copy archives once website confirms"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "queued",
    errorMessage: null,
    confirmedOnServer: true
  }) === true,
  "queued archives when website already has the lot"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "uploading",
    errorMessage: "Sending photo 1 of 12…",
    confirmedOnServer: true
  }) === true,
  "stuck uploading archives when website already has the lot"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "failed",
    errorMessage: "Website was busy",
    confirmedOnServer: true
  }) === true,
  "failed leftover archives when website already has the lot"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "draft",
    errorMessage: null,
    confirmedOnServer: false
  }) === false,
  "draft stays when the website does not have this id"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "uploaded",
    errorMessage: null,
    confirmedOnServer: false
  }) === false,
  "do not archive without server confirm"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "uploaded",
    errorMessage: "On server — cataloging…",
    confirmedOnServer: false,
    hasServerLotId: true
  }) === true,
  "linked website lot archives even if catalog list misses it"
);
assert(
  shouldArchiveCatalogedDraft({
    alreadyArchived: true,
    status: "uploaded",
    errorMessage: null,
    confirmedOnServer: true
  }) === false,
  "already archived"
);

assert(shouldSkipReupload({ confirmedOnServer: true }) === true, "skip reupload when confirmed");
assert(shouldSkipReupload({ confirmedOnServer: false }) === false, "upload when not on website");
assert(
  shouldSkipReupload({ confirmedOnServer: false, catalogLookupFailed: true }) === true,
  "do not re-upload when we could not check the website"
);
assert(
  shouldSkipReupload({ confirmedOnServer: false, hasServerLotId: true }) === true,
  "do not re-upload a lot already linked on the website"
);
assert(
  shouldSkipReupload({ confirmedOnServer: false, matchingJobLiveOrDone: true }) === true,
  "skip reupload when a live job already has this client_draft_id"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "uploaded",
    confirmedOnServer: false,
    uniqueSkuMatch: true
  }) === true,
  "unique SKU on the website archives a sent lot"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "draft",
    confirmedOnServer: false,
    uniqueSkuMatch: true
  }) === false,
  "draft does not archive on SKU alone"
);
assert(
  shouldArchiveCatalogedDraft({
    status: "draft",
    confirmedOnServer: true
  }) === true,
  "draft archives when this phone id is already on website photos"
);

const skuMap = uniqueSkuLotMap([
  { id: "a", sku: "LV101" },
  { id: "b", sku: "lv102" },
  { id: "c", sku: "LV101" },
  { id: "d", sku: "  " }
]);
assert(skuMap.get("lv102") === "b", "unique sku maps");
assert(skuMap.has("lv101") === false, "duplicate sku does not map");

const jl = uniqueSkuLotMap([{ id: "lot-jl", sku: "JL91500" }]);
assert(findUniqueSkuLot("JL91500ADD", jl) === "lot-jl", "ADD suffix matches base SKU");
assert(findUniqueSkuLot("jl91500", jl) === "lot-jl", "exact sku matches");
assert(findUniqueSkuLot("JL90400", jl) === undefined, "other sku does not match");
assert(
  findUniqueSkuLot(
    "JL91",
    uniqueSkuLotMap([
      { id: "a", sku: "JL91500" },
      { id: "b", sku: "JL91000" }
    ])
  ) === undefined,
  "short prefix does not guess when two lots share a stem"
);

console.log("archiveCataloged selftest ok (27 cases)");
