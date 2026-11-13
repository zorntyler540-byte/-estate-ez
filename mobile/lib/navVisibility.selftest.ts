import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  HIDDEN_TAB_ROUTES,
  formatTabBadge,
  isTabHidden,
  queueTabAccessibilityLabel
} from "./navVisibility";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(isTabHidden("facebook"), "Facebook tab hidden like web");
assert(!isTabHidden("queue") && !isTabHidden("capture") && !isTabHidden("lots"), "core tabs visible");

// Stay in step with the web sidebar: every hidden mobile tab is hidden on web too.
const webSource = readFileSync(join(__dirname, "../../frontend/lib/navVisibility.ts"), "utf8");
for (const route of HIDDEN_TAB_ROUTES) {
  assert(webSource.includes(`"/${route}"`), `web navVisibility also hides /${route}`);
}

assert(formatTabBadge(0) === undefined, "no badge at zero");
assert(formatTabBadge(-3) === undefined, "no badge for bad counts");
assert(formatTabBadge(Number.NaN) === undefined, "no badge for NaN");
assert(formatTabBadge(7) === "7", "plain count");
assert(formatTabBadge(99) === "99", "99 fits");
assert(formatTabBadge(140) === "99+", "caps at 99+");

assert(queueTabAccessibilityLabel(0) === "Queue", "plain label at zero");
assert(queueTabAccessibilityLabel(1).includes("1 lot not"), "singular count is spoken");
assert(queueTabAccessibilityLabel(4).includes("4 lots"), "plural count is spoken");

console.log("navVisibility selftest ok (13 cases)");
