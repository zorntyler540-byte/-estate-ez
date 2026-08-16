import { decideSyncRecovery, isTransientSyncError } from "./syncRecovery";

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

console.log(`syncRecovery selftest ok (${cases.length + transientCases.length} cases)`);
