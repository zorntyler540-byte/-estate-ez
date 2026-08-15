import { decideSyncRecovery } from "./syncRecovery";

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

console.log(`syncRecovery selftest ok (${cases.length} cases)`);
