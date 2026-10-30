import { formatSyncOutcome, canResendQueueRow, queueRowTitle, queueRowView } from "./queueStatus";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const archived = queueRowView({
  status: "uploaded",
  archived_at: 1,
  photo_count: 8,
  manual_sku: "LV101"
});
assert(archived.label === "On website", "archived label");
assert(archived.showResend === false, "archived hides Resend");
assert(archived.removeLabel === "Remove", "archived Remove");
assert(archived.detail === null, "archived has one status line");
assert(queueRowTitle({ archived_at: 1, photo_count: 8, manual_sku: "LV101" }).includes("on website"), "archived title");

const linked = queueRowView({
  status: "uploaded",
  server_lot_id: "lot-1",
  photo_count: 4,
  error_message: "On server — cataloging…"
});
assert(linked.label === "On website", "linked lot is On website");
assert(linked.showResend === false, "linked hides Resend");
assert(linked.detail === null, "linked no extra error line");

const cataloging = queueRowView({
  status: "uploaded",
  photo_count: 6,
  error_message: "Still cataloging on server…"
});
assert(cataloging.label === "Cataloging…", "cataloging label");
assert(cataloging.showResend === false, "cataloging hides Resend");
assert(cataloging.detail === null, "cataloging does not repeat copy");
assert(cataloging.tone === "progress", "cataloging tone");
assert(canResendQueueRow({ status: "uploaded", photo_count: 6, error_message: "On server — cataloging…" }) === false, "canResend cataloging");

const sentClean = queueRowView({ status: "uploaded", photo_count: 3 });
assert(sentClean.label === "Sent", "unconfirmed sent label");
assert(sentClean.showResend === true, "unconfirmed sent can Resend");
assert(canResendQueueRow({ status: "uploaded", photo_count: 3 }) === true, "canResend unconfirmed");

const busy = queueRowView({
  status: "failed",
  photo_count: 2,
  error_message: "Website was busy — tap Sync to confirm. Do not Resend if the lot is already online."
});
assert(busy.label === "Needs a check", "transient failed");
assert(busy.showResend === false, "transient hides Resend");
assert(busy.detail === null, "transient no duplicate detail");

const gemini = queueRowView({
  status: "failed",
  photo_count: 2,
  error_message: "Gemini 500: overloaded"
});
assert(gemini.label === "Failed", "real fail label");
assert(gemini.detail === "Gemini 500: overloaded", "real fail shows server error once");
assert(gemini.showResend === false, "failed uses Sync not Resend");

assert(canResendQueueRow({ status: "failed", photo_count: 2, error_message: "Gemini 500" }) === false, "no resend on failed");
assert(canResendQueueRow({ status: "uploaded", photo_count: 0 }) === false, "no photos no resend");

assert(
  formatSyncOutcome({ uploaded: 0, failed: 0, reconciled: 0, archived: 2 }).includes("Phone archive"),
  "archive outcome"
);
assert(
  formatSyncOutcome({ uploaded: 0, failed: 0, reconciled: 0, healthSummary: "2 still cataloging" }).includes(
    "Checked leftover lots"
  ),
  "leftover check outcome"
);
assert(
  formatSyncOutcome({ uploaded: 0, failed: 0, reconciled: 0 }, true).includes("Phone archive"),
  "empty queue mentions archive"
);

console.log("queueStatus selftest ok (18 cases)");
