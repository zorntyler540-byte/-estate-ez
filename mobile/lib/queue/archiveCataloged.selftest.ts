import { isStillCatalogingMessage, shouldArchiveCatalogedDraft } from "./archiveCataloged";

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
  }) === false,
  "do not archive while cataloging copy remains"
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
    status: "queued",
    errorMessage: null,
    confirmedOnServer: true
  }) === false,
  "queued never archives"
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
assert(
  shouldArchiveCatalogedDraft({
    status: "failed",
    errorMessage: null,
    confirmedOnServer: true
  }) === false,
  "failed stays in Queue until Sync"
);

console.log("archiveCataloged selftest ok (11 cases)");
