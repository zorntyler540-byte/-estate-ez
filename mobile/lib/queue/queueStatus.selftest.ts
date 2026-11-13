import { UPLOAD_PAUSED_MESSAGE, UploadPausedError } from "../uploadPause";
import {
  blockedClipLine,
  countWaitingLots,
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
assert(archived.label === "In Estate-EZ", "archived label");
assert(archived.hint.includes("seller app"), "archived says the seller app is a separate post");
assert(archived.showResend === false, "archived hides Resend");
assert(archived.removeLabel === "Remove", "archived Remove");
assert(archived.detail === null, "archived has one status line");
assert(queueRowTitle({ archived_at: 1, photo_count: 8, manual_sku: "LV101" }).includes("Estate-EZ"), "archived title");
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
assert(linked.label === "In Estate-EZ", "linked lot is In Estate-EZ");
assert(linked.hint.includes("seller app"), "linked lot names the seller app");
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

// Offline queueing / auto-upload copy.
{
  const q = (connection: Parameters<typeof queueRowView>[0]["connection"], extra = {}) =>
    queueRowView({ status: "queued", photo_count: 3, connection, syncing: false, ...extra });
  assert(q("offline").hint === "Will send when you're back online.", "queued offline hint");
  assert(q("waiting_stable").hint.startsWith("Waiting for a stable connection"), "queued waiting hint");
  assert(q("checking").hint.startsWith("Waiting for a stable connection"), "queued checking hint");
  assert(q("wifi_required").hint.includes("Upload on Wi‑Fi only"), "queued Wi-Fi only hint");
  assert(q("unreachable").hint.startsWith("Website is not answering"), "queued unreachable hint");
  assert(q("ready").hint.startsWith("Uploads on its own"), "queued ready hint");
  assert(q("ready", { syncing: true }).hint.startsWith("In line"), "queued while syncing");
  assert(queueRowView({ status: "queued", online: false }).hint === "Will send when you're back online.", "legacy online flag");

  const paused = q("waiting_stable", { error_message: UPLOAD_PAUSED_MESSAGE });
  assert(paused.label === "Paused", "paused label");
  assert(paused.hint.includes("not sent again"), "paused says sent photos are kept");
  assert(paused.tone === "progress", "paused is not an error");
  const wifiPaused = q("wifi_required", { error_message: new UploadPausedError("wifi_only").message });
  assert(wifiPaused.label === "Paused" && wifiPaused.hint.includes("Wi‑Fi"), "Wi-Fi only pause");

  const interrupted = queueRowView({ status: "uploading", photo_count: 3, syncing: false, connection: "offline" });
  assert(interrupted.label === "Interrupted", "killed mid-upload shows Interrupted, not Uploading…");
  assert(interrupted.hint.startsWith("Will send when you're back online."), "interrupted offline hint");
  assert(
    queueRowView({ status: "uploading", photo_count: 3, syncing: true }).label === "Uploading…",
    "live upload stays Uploading…"
  );

  assert(
    countWaitingLots([
      { status: "queued" },
      { status: "uploading" },
      { status: "failed", error_kind: "transient" },
      { status: "failed", error_kind: "permanent" },
      { status: "draft" },
      { status: "uploaded" },
      { status: "queued", archived_at: 1 }
    ]) === 3,
    "waiting = queued + interrupted + will-retry"
  );

  assert(
    formatSyncOutcome({ uploaded: 0, failed: 0, reconciled: 0, paused: true }).startsWith("Paused — the connection dropped"),
    "paused Sync copy"
  );
  assert(
    formatSyncOutcome({ uploaded: 2, failed: 0, reconciled: 0, paused: true }).startsWith("Sent 2 lots, then paused"),
    "partial then paused"
  );
  assert(
    formatSyncOutcome({ uploaded: 0, failed: 0, reconciled: 0, waitingForWifi: 2 }) ===
      "Checked the website. 2 lots waiting for Wi‑Fi (Upload on Wi‑Fi only is on).",
    "Wi-Fi only Sync copy"
  );
  assert(
    formatSyncOutcome({ uploaded: 0, failed: 0, reconciled: 0, deferred: 3 }) ===
      "3 lots waiting for the website to catalog earlier ones — they upload on their own.",
    "paced-only Sync copy"
  );
  assert(
    formatSyncOutcome({ uploaded: 2, failed: 0, reconciled: 0, deferred: 1 }).endsWith(
      " 1 more lot uploads as the website catalogs earlier ones."
    ),
    "sent + paced Sync copy"
  );
}

{
  const importing = queueRowView({ status: "importing", photo_count: 4 });
  assert(importing.label === "Importing…" && !importing.showResend && !importing.showRetry, "importing row");
  const paced = queueRowView({
    status: "queued",
    error_message: "Waiting — the website is still cataloging earlier lots. This lot uploads on its own as they finish.",
    photo_count: 4
  });
  assert(paced.label === "Waiting for website" && paced.detail?.startsWith("Waiting —"), "paced row");
}

console.log("queueStatus selftest ok (70 cases)");
