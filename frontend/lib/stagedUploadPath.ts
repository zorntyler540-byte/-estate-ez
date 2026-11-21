/** Browser ``File`` after HEIC→JPEG loses ``webkitRelativePath`` — keep the server upload path here. */
const stagedUploadRel = new WeakMap<File, string>();
/** Path from before Arrange lots, so Reset can put the automatic split back. */
const originalUploadRel = new WeakMap<File, string | null>();

export function readSourceUploadRel(file: File): string | null {
  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath?.trim();
  return rel || null;
}

export function markStagedUploadRel(file: File, relPath: string): void {
  const rel = relPath.replace(/\\/g, "/").replace(/^\/+/, "").trim();
  if (rel) stagedUploadRel.set(file, rel);
}

export function getStagedUploadRel(file: File): string {
  return stagedUploadRel.get(file) || file.name;
}

/** Server path for a staged file, including a not-yet-converted folder pick's ``webkitRelativePath``. */
export function stagedUploadRelOrNull(file: File): string | null {
  return stagedUploadRel.get(file) || readSourceUploadRel(file);
}

/** Remember the path Arrange lots will replace. A second call keeps the first path. */
export function rememberOriginalUploadRel(file: File): void {
  if (originalUploadRel.has(file)) return;
  originalUploadRel.set(file, stagedUploadRel.get(file) || readSourceUploadRel(file));
}

/** Put back the path from before Arrange lots. */
export function restoreOriginalUploadRel(file: File): void {
  if (!originalUploadRel.has(file)) return;
  const orig = originalUploadRel.get(file);
  if (orig) stagedUploadRel.set(file, orig);
  else stagedUploadRel.delete(file);
  originalUploadRel.delete(file);
}

/** Unique leaf name within one staging batch (``IMG.jpg``, ``IMG_2.jpg``, …). */
export function allocateUniqueLeafName(leaf: string, used: Set<string>): string {
  const safe = leaf.replace(/\\/g, "/").split("/").pop() || leaf || "photo.jpg";
  const dot = safe.lastIndexOf(".");
  const stem = dot > 0 ? safe.slice(0, dot) : safe;
  const ext = dot > 0 ? safe.slice(dot) : ".jpg";
  let candidate = safe;
  let n = 2;
  while (used.has(candidate.toLowerCase())) {
    candidate = `${stem}_${n}${ext}`;
    n += 1;
  }
  used.add(candidate.toLowerCase());
  return candidate;
}

/** Preserve ``folder/sub/photo.jpg`` structure when stabilizing HEIC from a folder pick. */
export function buildStagedUploadRel(
  source: File,
  leafName: string,
  usedLeaves: Set<string>
): { fileName: string; uploadRel: string } {
  const uniqueLeaf = allocateUniqueLeafName(leafName, usedLeaves);
  const sourceRel = readSourceUploadRel(source);
  if (sourceRel && sourceRel.includes("/")) {
    const parts = sourceRel.replace(/\\/g, "/").split("/");
    parts[parts.length - 1] = uniqueLeaf;
    const uploadRel = parts.join("/");
    return { fileName: uniqueLeaf, uploadRel };
  }
  return { fileName: uniqueLeaf, uploadRel: uniqueLeaf };
}
