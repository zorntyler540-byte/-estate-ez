import {
  isStillCatalogingMessage,
  shouldArchiveCatalogedDraft,
  shouldSkipReupload
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
    confirmedOnServer: true
  }) === false,
  "draft stays — still shooting"
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

console.log("archiveCataloged selftest ok (18 cases)");
