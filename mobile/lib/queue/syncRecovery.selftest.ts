import { decideSyncRecovery, findJobForDraft, isTransientSyncError } from "./syncRecovery";

const cases: Array<{
  status: "uploading" | "failed" | "queued" | "uploaded";
  job: { status: string } | null;
  want: "adopt" | "retry";
}> = [
  { status: "uploading", job: { status: "processing" }, want: "adopt" },
  { status: "uploading", job: { status: "queued" }, want: "adopt" },
  { status: "uploading", job: { status: "completed" }, want: "adopt" },
  { status: "uploading", job: { status: "receiving" }, want: "retry" },
  { status: "uploading", job: { status: "failed" }, want: "retry" },
  { status: "uploading", job: null, want: "retry" },
  { status: "failed", job: { status: "failed" }, want: "retry" },
  { status: "failed", job: { status: "processing" }, want: "adopt" },
  { status: "queued", job: { status: "queued" }, want: "adopt" },
  { status: "uploaded", job: { status: "processing" }, want: "adopt" }
];

for (const c of cases) {
  const got = decideSyncRecovery(c.status, c.job);
  if (got !== c.want) {
    throw new Error(`${c.status} + ${c.job?.status ?? "null"} => ${got}, expected ${c.want}`);
  }
}

const transientCases: Array<{ msg: string; want: boolean }> = [
  { msg: "Sync didn't finish. Use Resend if the lot is missing.", want: true },
  { msg: "The website was busy. Open Queue and tap Sync again.", want: true },
  { msg: "Website was busy — tap Sync to confirm. Do not Resend if the lot is already online.", want: true },
  { msg: "Request timed out — check Wi‑Fi and try Sync again.", want: true },
  { msg: "QueuePool overflow", want: true },
  { msg: "upstream error", want: true },
  { msg: "On server — check the website", want: true },
  { msg: "Photo file is missing on this phone.", want: false },
  { msg: "No photos in lot", want: false }
];

for (const c of transientCases) {
  const got = isTransientSyncError(c.msg);
  if (got !== c.want) {
    throw new Error(`isTransientSyncError(${JSON.stringify(c.msg)}) => ${got}, expected ${c.want}`);
  }
}

const jobs = [
  { id: "job-old", client_draft_id: null as string | null, status: "receiving" },
  { id: "job-match", client_draft_id: "1C86C230-A1C1-4433-B64E-5C2957E79027", status: "processing" }
];
const byDraft = findJobForDraft(jobs, "1c86c230-a1c1-4433-b64e-5c2957e79027", "job-old");
if (byDraft?.id !== "job-match") {
  throw new Error("findJobForDraft should prefer client_draft_id over server_job_id");
}
const byId = findJobForDraft(
  [{ id: "job-old", client_draft_id: null, status: "queued" }],
  "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "job-old"
);
if (byId?.id !== "job-old") {
  throw new Error("findJobForDraft should fall back to server_job_id");
}
const missing = findJobForDraft(jobs, "no-such-draft", "gone");
if (missing !== null) {
  throw new Error("findJobForDraft should return null when nothing matches");
}

console.log(`syncRecovery selftest ok (${cases.length + transientCases.length + 3} cases)`);
