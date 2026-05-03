/**
 * Multiple lots in one browser session. CT usually only lets you open the next new item
 * after you finish the current one (Detail → Settings → Media → Save), same as with the
 * extension alone. This script only fills Step 1 from the API; you complete the listing
 * in CT, then start the next item to Detail and press Enter here.
 *
 * Env (same as autofill + one of):
 *   ESTATE_LOT_IDS     — comma-separated UUIDs
 *   ESTATE_LOT_ID_FILE — path to text file, one lot id per line (# comments ok)
 *   CT_TARGET_URL      — optional first navigation (default seller home)
 *
 * Run: npm run autofill:batch
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import readline from "readline";
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

function question(prompt) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(prompt, () => {
      rl.close();
      resolve();
    });
  });
}

function parseLotIds() {
  const filePath = process.env.ESTATE_LOT_ID_FILE || "";
  if (filePath && fs.existsSync(filePath)) {
    const raw = fs.readFileSync(filePath, "utf8");
    return raw
      .split(/\r?\n/)
      .map((line) => line.replace(/#.*$/, "").trim())
      .filter(Boolean);
  }
  const csv = process.env.ESTATE_LOT_IDS || "";
  return csv
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

const apiBase = (process.env.ESTATE_API_BASE || "").replace(/\/$/, "");
const token = process.env.ESTATE_JWT || process.env.ESTATE_TOKEN || "";
const ctUrl = process.env.CT_TARGET_URL || "https://seller.ctbids.com/";
const lotIds = parseLotIds();

if (!apiBase || !token || lotIds.length === 0) {
  console.error("Set ESTATE_API_BASE, ESTATE_JWT (or ESTATE_TOKEN), and ESTATE_LOT_IDS or ESTATE_LOT_ID_FILE.");
  process.exit(1);
}

async function fetchLot(lotId) {
  const res = await fetch(`${apiBase}/lots/${encodeURIComponent(lotId)}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" }
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`GET /lots/${lotId} → ${res.status} ${text.slice(0, 200)}`);
  }
  return res.json();
}

async function runBridge(page, lot) {
  return page.evaluate(async (lotPayload) => {
    if (typeof window.__estateEzPlaywrightBridge !== "function") {
      return {
        ok: false,
        error:
          "Bridge missing — seller.ctbids.com Step 1 Detail, extension loaded, reload tab if needed."
      };
    }
    const done = new Promise((resolve) => {
      document.addEventListener("__estateEzAutofillDone", (e) => resolve(e.detail), { once: true });
    });
    window.__estateEzPlaywrightBridge(lotPayload);
    return await done;
  }, lot);
}

const userDataDir = path.join(__dirname, "..", "browser-profile");
const context = await chromium.launchPersistentContext(userDataDir, {
  headless: false,
  args: ["--disable-blink-features=AutomationControlled", ...extLaunchArgs()]
});

const page = context.pages()[0] ?? (await context.newPage());
await page.goto(ctUrl, { waitUntil: "domcontentloaded" });

console.log("");
console.log(
  `Batch: ${lotIds.length} lot(s). After each autofill, finish the item in CT (extension helps);`
);
console.log("when the next new item is on Step 1 Detail, press Enter for the next lot.");
console.log("");

for (let i = 0; i < lotIds.length; i += 1) {
  const lotId = lotIds[i];
  const n = i + 1;
  const label = `${n}/${lotIds.length}  (${lotId})`;
  if (n === 1) {
    await question(
      `[${label}] Open Step 1 (Detail) for the first item, then press Enter to fetch + fill… `
    );
  } else {
    await question(
      `[${label}] Finish the previous item in CT if needed, open the NEXT item on Step 1 Detail, then press Enter… `
    );
  }

  let lot;
  try {
    lot = await fetchLot(lotId);
  } catch (e) {
    console.error(String(e));
    await context.close();
    process.exit(1);
  }

  const bridgeResult = await runBridge(page, lot);
  console.log(`[${label}]`, JSON.stringify(bridgeResult, null, 2));
  console.log("");
}

console.log("Batch finished. Browser left open. Press Ctrl+C to close.");
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
