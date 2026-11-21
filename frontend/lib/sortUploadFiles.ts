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

const CAMERA_STAMP = /(20\d{6})[_-]?(\d{6})/;

/** ``YYYYMMDDHHMMSS`` from a camera-style filename, or null when the stamp is missing or impossible. */
export function cameraStampValue(label: string): number | null {
  const leaf = label.replace(/\\/g, "/").split("/").pop() || label;
  const match = CAMERA_STAMP.exec(leaf);
  if (!match) return null;
  const date = match[1]!;
  const time = match[2]!;
  const month = Number(date.slice(4, 6));
  const day = Number(date.slice(6, 8));
  const hour = Number(time.slice(0, 2));
  const minute = Number(time.slice(2, 4));
  const second = Number(time.slice(4, 6));
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  if (hour > 23 || minute > 59 || second > 59) return null;
  const stamp = Number(date + time);
  return Number.isFinite(stamp) ? stamp : null;
}

export type StampRegression = {
  /** Index of the photo whose camera time moves backward. */
  index: number;
  label: string;
  /** Index of the previous photo that had a camera time. */
  earlierIndex: number;
  earlierLabel: string;
};

/**
 * Photos whose embedded camera time is earlier than the previous stamped photo.
 * Unstamped names (divider sheets, ``IMG_``) stay in place and do not reset the check.
 */
export function cameraStampRegressions(
  files: readonly UploadSortable[],
  labelOf?: (file: UploadSortable) => string
): StampRegression[] {
  const out: StampRegression[] = [];
  let previous: { index: number; label: string; stamp: number } | null = null;
  files.forEach((file, index) => {
    const label = labelOf ? labelOf(file) : uploadSortLabel(file);
    const stamp = cameraStampValue(label);
    if (stamp == null) return;
    if (previous && stamp < previous.stamp) {
      out.push({
        index,
        label,
        earlierIndex: previous.index,
        earlierLabel: previous.label
      });
    }
    previous = { index, label, stamp };
  });
  return out;
}

/** Short warning for the catalog-order preview. Empty when every stamped photo moves forward. */
export function cameraStampRegressionNote(regressions: readonly StampRegression[]): string {
  if (!regressions.length) return "";
  const rows = regressions.map((row) => row.index + 1);
  const shown = rows.slice(0, 6);
  const more = rows.length - shown.length;
  const list = shown.join(", ") + (more > 0 ? `, +${more} more` : "");
  const noun = regressions.length === 1 ? "photo is" : "photos are";
  const rowWord = rows.length === 1 ? "row" : "rows";
  const before = regressions.length === 1 ? "it" : "them";
  return `${regressions.length} ${noun} timestamped earlier than the stamped photo before ${before} (${rowWord} ${list}). Cataloging still uses this order.`;
}

export type UploadOrderRow = {
  index: number;
  label: string;
  leaf: string;
  dir: string;
  /** First photo in a folder, so the preview can print the folder once. */
  showDir: boolean;
  backwards: boolean;
  earlierIndex: number | null;
  earlierLeaf: string;
};

export type UploadOrderPreview = {
  rows: UploadOrderRow[];
  note: string;
  folderCount: number;
  first: string;
  last: string;
};

function leafOf(label: string): string {
  const slash = label.lastIndexOf("/");
  return slash >= 0 ? label.slice(slash + 1) : label;
}

function dirOf(label: string): string {
  const slash = label.lastIndexOf("/");
  return slash >= 0 ? label.slice(0, slash) : "";
}

/** Rows in the order cataloging will see, including folder breaks and backward camera times. */
export function buildUploadOrderPreview(
  files: readonly UploadSortable[],
  labelOf?: (file: UploadSortable) => string
): UploadOrderPreview {
  const labels = files.map((file, index) => {
    const raw = (labelOf ? labelOf(file) : uploadSortLabel(file)) || file.name || `Photo ${index + 1}`;
    return raw.replace(/\\/g, "/");
  });
  const regressions = cameraStampRegressions(
    labels.map((label) => ({ name: label })),
    (file) => file.name
  );
  const byIndex = new Map(regressions.map((row) => [row.index, row]));
  let previousDir: string | null = null;
  const rows: UploadOrderRow[] = labels.map((label, index) => {
    const dir = dirOf(label);
    const showDir = dir !== previousDir && dir !== "";
    previousDir = dir;
    const problem = byIndex.get(index);
    return {
      index,
      label,
      leaf: leafOf(label) || label,
      dir,
      showDir,
      backwards: Boolean(problem),
      earlierIndex: problem ? problem.earlierIndex : null,
      earlierLeaf: problem ? leafOf(problem.earlierLabel) : ""
    };
  });
  const dirs = new Set(rows.map((row) => row.dir).filter(Boolean));
  return {
    rows,
    note: cameraStampRegressionNote(regressions),
    folderCount: dirs.size,
    first: rows[0]?.leaf ?? "",
    last: rows[rows.length - 1]?.leaf ?? ""
  };
}
