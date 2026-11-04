/**
 * Resume a half-sent lot instead of starting over. The server lists the names already in the
 * lot's ``receiving`` batch. When every one of them is still on the phone, only the missing
 * photos are sent. A received name the phone no longer has (photo deleted after a partial
 * upload) means the batch is wrong, so it is cleared and the lot is sent again from scratch.
 */

export type ResumePlan =
  | { action: "resume"; missing: string[]; received: string[] }
  | { action: "restart"; reason: "unknown" | "folder_missing" | "extra_files" };

export function planResume(opts: {
  expected: readonly string[];
  received: readonly string[] | null | undefined;
  folderMissing?: boolean;
}): ResumePlan {
  if (opts.folderMissing) return { action: "restart", reason: "folder_missing" };
  if (!opts.received) return { action: "restart", reason: "unknown" };
  const expected = new Set(opts.expected);
  const received = [...new Set(opts.received)];
  if (received.some((name) => !expected.has(name))) {
    return { action: "restart", reason: "extra_files" };
  }
  const have = new Set(received);
  return {
    action: "resume",
    missing: opts.expected.filter((name) => !have.has(name)),
    received
  };
}

/** Expected names the server still lacks, or null when it did not report (older API). */
export function namesStillMissing(
  expected: readonly string[],
  received: readonly string[] | null | undefined
): string[] | null {
  if (!received) return null;
  const have = new Set(received);
  return expected.filter((name) => !have.has(name));
}
