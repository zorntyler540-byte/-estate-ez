import { pickCatalogLotForDraft } from "./addPhotos";
import {
  findUniqueSkuLot,
  shouldArchiveCatalogedDraft,
  shouldSkipReupload,
  uniqueSkuLotMap
} from "./archiveCataloged";
import {
  emptyWebsiteIndex,
  noteDraftConfirmed,
  resolveDraftOnServer,
  saleIndexFromResponse,
  setSaleIndex,
  type WebsiteIndex
} from "./draftConfirm";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const SALE_A = "sale-a";
const SALE_B = "sale-b";
const DRAFT_MINE = "1c86c230-a1c1-4433-b64e-5c2957e79027";
const DRAFT_OTHER_PHONE = "2d97d341-b2d2-4544-c75f-6d3a68f8a138";
const DRAFT_MOVED = "3ea8e452-c3e3-4655-d860-7e4b79a9b249";

/** Sale A: another phone's JL91500 lot. Sale B: a JL101 lot from that phone + a moved lot. */
function websiteIndex(): WebsiteIndex {
  const index = emptyWebsiteIndex();
  setSaleIndex(
    index,
    SALE_A,
    saleIndexFromResponse({
      draftIds: [DRAFT_OTHER_PHONE],
      lots: [{ draft_id: DRAFT_OTHER_PHONE, lot_id: "lot-a-jl91500" }]
    })
  );
  setSaleIndex(
    index,
    SALE_B,
    saleIndexFromResponse({
      draftIds: [],
      lots: [
        { draft_id: DRAFT_OTHER_PHONE.replace("2d97", "4f00"), lot_id: "lot-b-jl101" },
        { draft_id: DRAFT_MOVED.toUpperCase(), lot_id: "lot-b-moved" }
      ]
    })
  );
  return index;
}

/** Website SKUs (what the old code matched on). */
const saleASkus = uniqueSkuLotMap([{ id: "lot-a-jl91500", sku: "JL91500" }]);
const saleBSkus = uniqueSkuLotMap([{ id: "lot-b-jl101", sku: "JL101" }]);

function decisions(draft: { id: string; sale_id: string; status: string; server_lot_id?: string | null }) {
  const state = resolveDraftOnServer(draft, websiteIndex());
  return {
    state,
    archive: shouldArchiveCatalogedDraft({
      status: draft.status,
      errorMessage: null,
      confirmedOnServer: state.confirmed,
      hasServerLotId: Boolean(draft.server_lot_id)
    }),
    skipUpload: shouldSkipReupload({
      confirmedOnServer: state.confirmed,
      catalogLookupFailed: state.lookupFailed,
      hasServerLotId: Boolean(draft.server_lot_id)
    })
  };
}

let cases = 0;
function check(cond: unknown, msg: string) {
  assert(cond, msg);
  cases += 1;
}

// Queued / failed + exact SKU match on the website: still not this draft.
for (const status of ["queued", "failed"]) {
  check(findUniqueSkuLot("JL91500", saleASkus) === "lot-a-jl91500", "precondition: SKU would match");
  const d = decisions({ id: DRAFT_MINE, sale_id: SALE_A, status });
  check(!d.state.confirmed, `${status} + same SKU is not confirmed`);
  check(d.state.lotId === null, `${status} + same SKU gets no lot id (no clip attach)`);
  check(!d.archive, `${status} + same SKU does not archive / delete local photos`);
  check(!d.skipUpload, `${status} + same SKU still uploads`);
}

// Prefix / ADD-suffix matches (JL91500ADD ↔ JL91500, JL915 ↔ JL91500).
check(findUniqueSkuLot("JL91500ADD", saleASkus) === "lot-a-jl91500", "precondition: ADD suffix would match");
check(findUniqueSkuLot("JL915", saleASkus) === "lot-a-jl91500", "precondition: prefix would match");
for (const status of ["queued", "failed", "uploaded"]) {
  const d = decisions({ id: DRAFT_MINE, sale_id: SALE_A, status });
  check(!d.archive && !d.state.confirmed, `${status} + prefix SKU match does not archive`);
}

// Same SKU reused in another sale: the sale-B lot must never claim a sale-A draft.
check(findUniqueSkuLot("JL101", saleBSkus) === "lot-b-jl101", "precondition: sale B has JL101");
for (const status of ["queued", "failed", "uploaded"]) {
  const d = decisions({ id: DRAFT_MINE, sale_id: SALE_A, status });
  check(!d.state.confirmed && d.state.lotId === null, `${status} JL101 in another sale is not this draft`);
  check(!d.archive, `${status} JL101 in another sale does not archive`);
}

// Exact confirmations still work.
{
  const d = decisions({ id: DRAFT_OTHER_PHONE, sale_id: SALE_A, status: "queued" });
  check(d.state.confirmed && d.state.lotId === "lot-a-jl91500", "draft id in cataloged-mobile-drafts confirms");
  check(d.archive && d.skipUpload, "confirmed queued draft archives and skips upload");
}
{
  const d = decisions({ id: DRAFT_MOVED, sale_id: SALE_A, status: "failed" });
  check(d.state.confirmed && d.state.lotId === "lot-b-moved", "exact draft id in another sale (moved lot) confirms");
}
{
  const d = decisions({ id: DRAFT_MINE, sale_id: SALE_A, status: "uploaded", server_lot_id: "lot-own-job" });
  check(d.state.confirmed && d.state.lotId === "lot-own-job", "verified server_lot_id from this phone's job confirms");
  check(d.archive && d.skipUpload, "verified server_lot_id archives and skips upload");
}
{
  const index = websiteIndex();
  setSaleIndex(index, SALE_A, saleIndexFromResponse({ draftIds: [DRAFT_MINE], lots: [] }));
  const state = resolveDraftOnServer({ id: DRAFT_MINE, sale_id: SALE_A }, index);
  check(state.confirmed && state.lotId === null, "completed job id with no mapped lot still confirms");
}
{
  const index = websiteIndex();
  noteDraftConfirmed(index, DRAFT_MINE, "lot-mid-sync");
  const state = resolveDraftOnServer({ id: DRAFT_MINE, sale_id: SALE_A }, index);
  check(state.confirmed && state.lotId === "lot-mid-sync", "mid-Sync job completion patches the index");
}

// A sale whose catalog list could not be read: nothing destructive, no re-upload.
{
  const index = websiteIndex();
  setSaleIndex(index, SALE_A, { ok: false });
  const state = resolveDraftOnServer({ id: DRAFT_MINE, sale_id: SALE_A }, index);
  check(state.lookupFailed && !state.confirmed, "failed lookup is unknown, not confirmed");
  check(
    shouldSkipReupload({ confirmedOnServer: false, catalogLookupFailed: true }) &&
      !shouldArchiveCatalogedDraft({ status: "queued", confirmedOnServer: false }),
    "failed lookup neither re-uploads nor archives"
  );
}

// Add photos picker: SKU is a suggestion only.
{
  const picked = pickCatalogLotForDraft({
    draftId: DRAFT_MINE,
    manualSku: "JL101",
    mapped: [],
    lotsBySku: [{ id: "lot-b-jl101", sku: "JL101", photo_count: 2, auction_title: "Clock" } as never]
  });
  check(picked?.match === "sku", "picker marks SKU hits as a suggestion (confirm, never saved)");
}

console.log(`draftConfirm selftest ok (${cases} cases)`);
