/** Thumb loader steps for the lot editor. Signed original can be a clip named ``.jpg``. */

export type ThumbStep = "idle" | "signed" | "thumbnail" | "failed";
export type ThumbEvent =
  | "signed-ok"
  | "signed-empty"
  | "signed-fail"
  | "thumb-ok"
  | "thumb-fail"
  | "img-error";

export function nextThumbStep(current: ThumbStep, event: ThumbEvent): ThumbStep {
  if (event === "signed-ok") return "signed";
  if (event === "signed-empty" || event === "signed-fail") return "thumbnail";
  if (event === "thumb-ok") return "thumbnail";
  if (event === "thumb-fail") return "failed";
  if (event === "img-error") {
    return current === "signed" ? "thumbnail" : "failed";
  }
  return current;
}

export function thumbFailedMessage(fileOnDisk: boolean): string {
  return fileOnDisk ? "Could not load" : "Missing on server";
}
