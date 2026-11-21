/**
 * Order a new website pick before ``assignUploadNames`` numbers ``web-<batch>-NNNN``.
 *
 * Browser ``FileList`` order is not filename order once a folder is large (Chrome stops
 * sorting past about 100 files). The server then trusts that ``NNNN``. Sort by the
 * relative path when the folder picker set one, otherwise the leaf name, with
 * ``localeCompare`` numeric mode (``IMG_9`` before ``IMG_10``, ``20261120_091500``
 * before ``20261120_153000``). ``lastModified`` breaks ties. Equal name and time keep
 * the original order.
 *
 * Files that already carry a ``web-`` name (resume / retry) must not be reordered —
 * pass ``alreadyNamed`` and this returns them in the order given.
 */

export type UploadSortable = {
  name: string;
  lastModified?: number;
  webkitRelativePath?: string;
};

export function uploadSortLabel(file: UploadSortable): string {
  const rel = (file.webkitRelativePath || "").replace(/\\/g, "/").replace(/^\/+/, "").trim();
  return rel || file.name || "";
}

export function compareUploadFiles(a: UploadSortable, b: UploadSortable): number {
  const byName = uploadSortLabel(a).localeCompare(uploadSortLabel(b), undefined, {
    numeric: true,
    sensitivity: "base"
  });
  if (byName !== 0) return byName;
  return (a.lastModified ?? 0) - (b.lastModified ?? 0);
}

export function sortUploadFiles<T extends UploadSortable>(files: readonly T[]): T[] {
  return files
    .map((file, index) => ({ file, index }))
    .sort((a, b) => {
      const cmp = compareUploadFiles(a.file, b.file);
      return cmp !== 0 ? cmp : a.index - b.index;
    })
    .map((row) => row.file);
}

/** Sort a fresh pick. Leave a batch that already has upload names in its current order. */
export function sortFilesBeforeUploadNames<T extends UploadSortable>(
  files: readonly T[],
  alreadyNamed?: (file: T) => boolean
): T[] {
  if (alreadyNamed && files.some((file) => alreadyNamed(file))) return [...files];
  return sortUploadFiles(files);
}
