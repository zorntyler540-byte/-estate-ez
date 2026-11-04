import {
  blockedClipLine,
  formatSyncOutcome,
  canResendQueueRow,
  queueRowTitle,
  queueRowView,
  removeNeedsSecondConfirm
} from "./queueStatus";

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
assert(
  queueRowTitle({ photo_count: 4, video_count: 1 }) === "4 photos · 1 video",
  "queue card does not count a clip as a photo"
);

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
assert(
  busy.detail === "Website was busy — tap Sync to confirm. Do not Resend if the lot is already online.",
  "transient failure still shows its own error on the row"
);

const silentFail = queueRowView({ status: "failed", photo_count: 2 });
assert(silentFail.label === "Needs a check", "failed without error");
assert(Boolean(silentFail.detail), "failed row never has an empty error line");

const sentBusy = queueRowView({
  status: "uploaded",
  photo_count: 2,
  error_message: "Network request failed"
});
assert(sentBusy.detail === "Network request failed", "sent + transient shows the error");

assert(blockedClipLine([]) === null, "no blocked clips, no line");
assert(blockedClipLine([null, "  "]) === null, "blank reasons ignored");
const oneBlocked = blockedClipLine(["Video is too large (max 80 MB)"]);
assert(oneBlocked?.startsWith("1 video was not accepted"), "single blocked clip");
assert(oneBlocked?.includes("too large"), "blocked reason is shown");
const twoBlocked = blockedClipLine(["Lot was deleted", "Lot was deleted"]);
assert(twoBlocked?.startsWith("2 videos"), "counts every blocked clip");
assert(twoBlocked?.split("Lot was deleted").length === 2, "repeated reason shown once");

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

// Permanent failures stop auto-retry and offer Retry.
const refused = queueRowView({
  status: "failed",
  photo_count: 41,
  error_kind: "permanent",
  error_message: "This lot has 41 photos; CT Bids allows at most 40 per listing."
});
assert(refused.label === "Needs attention", "permanent label");
assert(refused.showRetry === true, "permanent shows Retry");
assert(refused.tone === "error", "permanent tone");
assert(refused.detail?.includes("41 photos"), "permanent shows server reason");

const backoff = queueRowView({
  status: "failed",
  photo_count: 3,
  error_kind: "transient",
  error_message: "Network request failed",
  next_attempt_at: 1_000_000 + 90_000,
  now: 1_000_000
});
assert(backoff.label === "Will retry", "transient label");
assert(backoff.showRetry === false, "transient retries itself");
assert(backoff.hint.startsWith("Retrying in 2 min"), "transient shows wait");
assert(backoff.detail === "Network request failed", "transient shows error");

// Remove on a sent-but-unconfirmed lot asks twice.
const unconfirmed = queueRowView({ status: "uploaded", photo_count: 3 });
assert(unconfirmed.removeConfirm !== null, "unconfirmed asks twice");
assert(removeNeedsSecondConfirm({ status: "uploading" }) === true, "uploading asks twice");
assert(queueRowView({ status: "uploaded", photo_count: 3, server_lot_id: "lot" }).removeConfirm === null, "linked once");
assert(queueRowView({ status: "uploaded", archived_at: 1 }).removeConfirm === null, "archived once");
assert(queueRowView({ status: "draft", photo_count: 3 }).removeConfirm === null, "local draft once");
assert(queueRowView({ status: "failed", photo_count: 3 }).removeConfirm === null, "failed once");

// Add-photos sessions.
assert(
  queueRowTitle({ photo_count: 2, append_lot_id: "lot-1", append_label: "LV101" }) === "Add 2 photos to LV101",
  "append title"
);
assert(
  queueRowTitle({ photo_count: 2, archived_at: 1, append_lot_id: "lot-1", append_label: "LV101" }) ===
    "2 photos added to LV101",
  "append archived title"
);
assert(
  canResendQueueRow({ status: "uploaded", photo_count: 2, append_lot_id: "lot-1" }) === false,
  "append never Resends"
);
assert(
  queueRowView({ status: "draft", photo_count: 2, append_lot_id: "lot-1" }).removeTitle === "Delete these new shots?",
  "append delete copy"
);

console.log("queueStatus selftest ok (45 cases)");
