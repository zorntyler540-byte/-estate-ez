/**
 * Opens Chromium with persistent profile + unpacked Estate-Ez extension, navigates to
 * CT Seller (URL from CT_TARGET_URL), fetches a lot from your Estate API, then runs the
 * same autofill as the extension side panel via the page bridge in content.js.
 *
 * Prerequisites: be on CT Seller Step 1 (Detail) for the item form, logged in (profile
 * under ./browser-profile/).
 *
 * Env:
 *   ESTATE_API_BASE  — e.g. http://127.0.0.1:8000
 *   ESTATE_JWT       — Bearer token (same as extension / frontend)
 *   LOT_ID           — lot UUID
 *   CT_TARGET_URL    — optional; default https://seller.ctbids.com/ (deep link to sale/item if needed)
 *
 * Run: npm run autofill
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function extLaunchArgs() {
  const extPath = path.resolve(path.join(__dirname, "..", "..", "extension"));
  const manifest = path.join(extPath, "manifest.json");
  if (!fs.existsSync(manifest)) {
    console.error("Missing extension at:", extPath);
    process.exit(1);
  }
  const p = extPath.replace(/\\/g, "/");
  return [`--disable-extensions-except=${p}`, `--load-extension=${p}`];
}

const apiBase = (process.env.ESTATE_API_BASE || "").replace(/\/$/, "");
const token = process.env.ESTATE_JWT || process.env.ESTATE_TOKEN || "";
const lotId = process.env.LOT_ID || "";
const ctUrl = process.env.CT_TARGET_URL || "https://seller.ctbids.com/";

if (!apiBase || !token || !lotId) {
  console.error("Set ESTATE_API_BASE, ESTATE_JWT (or ESTATE_TOKEN), and LOT_ID.");
  process.exit(1);
}

const userDataDir = path.join(__dirname, "..", "browser-profile");
const context = await chromium.launchPersistentContext(userDataDir, {
  headless: false,
  args: ["--disable-blink-features=AutomationControlled", ...extLaunchArgs()]
});

const page = context.pages()[0] ?? (await context.newPage());
await page.goto(ctUrl, { waitUntil: "domcontentloaded" });

const res = await fetch(`${apiBase}/lots/${encodeURIComponent(lotId)}`, {
  headers: { Authorization: `Bearer ${token}`, Accept: "application/json" }
});
if (!res.ok) {
  const text = await res.text().catch(() => "");
  console.error("Lot fetch failed:", res.status, text.slice(0, 500));
  await context.close();
  process.exit(1);
}
const lot = await res.json();

const bridgeResult = await page.evaluate(async (lotPayload) => {
  if (typeof window.__estateEzPlaywrightBridge !== "function") {
    return {
      ok: false,
      error:
        "Bridge missing — open seller.ctbids.com with the extension, Detail step, and reload once if needed."
    };
  }
  const done = new Promise((resolve) => {
    document.addEventListener("__estateEzAutofillDone", (e) => resolve(e.detail), { once: true });
  });
  window.__estateEzPlaywrightBridge(lotPayload);
  return await done;
}, lot);

console.log("");
console.log("Autofill result:", JSON.stringify(bridgeResult, null, 2));
console.log("");
console.log("Browser left open. Press Ctrl+C to close.");

process.stdin.resume();
const shutdown = async () => {
  try {
    await context.close();
  } catch {
    /* ignore */
  }
  process.exit(0);
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
