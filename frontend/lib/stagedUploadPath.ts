/** Browser ``File`` after HEIC→JPEG loses ``webkitRelativePath`` — keep the server upload path here. */
const stagedUploadRel = new WeakMap<File, string>();

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
  const sourceRel = readSourceUploadRel(source) || stagedUploadRel.get(source) || null;
  if (sourceRel && sourceRel.includes("/")) {
    const parts = sourceRel.replace(/\\/g, "/").split("/");
    parts[parts.length - 1] = uniqueLeaf;
    const uploadRel = parts.join("/");
    return { fileName: uniqueLeaf, uploadRel };
  }
  return { fileName: uniqueLeaf, uploadRel: uniqueLeaf };
}
