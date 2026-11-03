/** While a lot is still cataloging (or clips are waiting for a lot id), check again soon.
 * Auto-sync's 45s cooldown is what left Queue on "Cataloging…" after the website finished. */
export const CATALOG_FOLLOWUP_MS = 8_000;

export function shouldFollowUpCatalogCheck(opts: {
  leftoverCatalogingNotice?: boolean;
  hasPendingLocalVideos?: boolean;
}): boolean {
  return Boolean(opts.leftoverCatalogingNotice || opts.hasPendingLocalVideos);
}
