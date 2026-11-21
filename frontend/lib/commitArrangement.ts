import {
  arrangedRel,
  type ArrangeResult
} from "./arrangeLots";
import {
  markStagedUploadRel,
  rememberOriginalUploadRel,
  restoreOriginalUploadRel,
  stagedUploadRelOrNull
} from "./stagedUploadPath";
import { forgetUploadNames } from "./webUploadSession";

export type CommittedArrangement<T> = {
  files: T[];
  thumbs: string[];
};

/** Reorder staged photos to the arranged lots and point each file at ``arranged/lot-N``. */
export function commitArrangement<T extends File>(
  files: readonly T[],
  thumbs: readonly (string | null | undefined)[] | undefined,
  result: ArrangeResult
): CommittedArrangement<T> {
  const order = result.lots.flat();
  result.lots.forEach((photos, lotIndex) => {
    photos.forEach((fileIndex) => {
      const file = files[fileIndex];
      if (!file) return;
      rememberOriginalUploadRel(file);
      const label = stagedUploadRelOrNull(file) || file.name;
      markStagedUploadRel(file, arrangedRel(lotIndex + 1, label));
    });
  });
  forgetUploadNames(order.map((fileIndex) => files[fileIndex]).filter((file): file is T => Boolean(file)));
  return {
    files: order.map((fileIndex) => files[fileIndex]!).filter(Boolean),
    thumbs: order.map((fileIndex) => thumbs?.[fileIndex] || "")
  };
}

/** Drop arranged paths so the automatic folder and blank-sheet split shows again. */
export function resetArrangement(files: readonly File[]): void {
  for (const file of files) restoreOriginalUploadRel(file);
  forgetUploadNames(files);
}
