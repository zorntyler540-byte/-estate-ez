/**
 * Full internal listing run: Detail autofill + Settings/Media tab clicks + photo upload
 * (same APIs and content-script logic as the extension) + Save click + PATCH completed.
 *
 * Env:
 *   ESTATE_API_BASE, ESTATE_JWT (or ESTATE_TOKEN), LOT_ID
 *   CT_TARGET_URL — optional; start on Step 1 Detail for a new item (default seller home)
 *   ESTATE_UPLOAD_MAX_PX — optional, default 1600 (matches extension thumbnails)
 *   ESTATE_SAVE_WAIT_MS — optional, default 120000 (poll save-gate after Save click)
 *   ESTATE_SKIP_PHOTOS=1 — skip photo upload (detail + tabs + save only)
 *   ESTATE_SKIP_MARK_COMPLETED=1 — do not PATCH is_completed after save
 *
 * Run: npm run listing:full
 *
 * Fragile parts: CT step tab labels and Save button; if a step fails, check CT DOM updates.
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
const maxPx = Math.min(2048, Math.max(64, parseInt(process.env.ESTATE_UPLOAD_MAX_PX || "1600", 10)));
const saveWaitMs = parseInt(process.env.ESTATE_SAVE_WAIT_MS || "120000", 10);
const skipPhotos = process.env.ESTATE_SKIP_PHOTOS === "1";
const skipMark = process.env.ESTATE_SKIP_MARK_COMPLETED === "1";

if (!apiBase || !token || !lotId) {
  console.error("Set ESTATE_API_BASE, ESTATE_JWT (or ESTATE_TOKEN), and LOT_ID.");
  process.exit(1);
}

async function fetchJson(url, init = {}) {
  const res = await fetch(url, {
    ...init,
    headers: { Accept: "application/json", ...init.headers }
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${url} → ${res.status} ${text.slice(0, 240)}`);
  }
  return res.json();
}

async function fetchLot() {
  return fetchJson(`${apiBase}/lots/${encodeURIComponent(lotId)}`, {
    headers: { Authorization: `Bearer ${token}` }
  });
}

function safePhotoName(originalName) {
  const stem = String(originalName || "photo")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.[^.]+$/i, "");
  const safe = (stem || "photo").replace(/[^\w.\-]+/g, "_").slice(0, 120) || "photo";
  return `${safe}.jpg`;
}

async function buildUploadFiles() {
  const list = await fetchJson(`${apiBase}/lots/${encodeURIComponent(lotId)}/photos`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!Array.isArray(list) || list.length === 0) return [];
  const files = [];
  for (const p of list) {
    const url = `${apiBase}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(p.id)}/thumbnail?max_px=${maxPx}`;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) {
      const t = await res.text().catch(() => "");
      throw new Error(`thumbnail ${p.id} → ${res.status} ${t.slice(0, 120)}`);
    }
    const buf = Buffer.from(await res.arrayBuffer());
    files.push({
      name: safePhotoName(p.original_name),
      type: "image/jpeg",
      dataBase64: buf.toString("base64")
    });
  }
  return files;
}

async function markLotCompleted() {
  const res = await fetch(`${apiBase}/lots/${encodeURIComponent(lotId)}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ is_completed: true })
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`PATCH completed → ${res.status} ${text.slice(0, 240)}`);
  }
  return res.json();
}

async function waitForPlaywrightApi(page, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await page.evaluate(() => Boolean(window.__estateEzPlaywright?.fillLot));
    if (ready) return;
    await sleep(400);
  }
  throw new Error(
    "Extension API missing — use seller.ctbids.com, ensure Estate-Ez loads (reload tab once)."
  );
}

async function tryClickSave(page) {
  try {
    const byRole = page.getByRole("button", { name: /^save$/i }).first();
    if (await byRole.isVisible({ timeout: 2500 })) {
      await byRole.click();
      return true;
    }
  } catch {
    /* continue */
  }
  try {
    await page.locator("button").filter({ hasText: /^Save$/i }).first().click({ timeout: 2500 });
    return true;
  } catch {
    return false;
  }
}

const userDataDir = path.join(__dirname, "..", "browser-profile");
const context = await chromium.launchPersistentContext(userDataDir, {
  headless: false,
  args: ["--disable-blink-features=AutomationControlled", ...extLaunchArgs()]
});

const page = context.pages()[0] ?? (await context.newPage());
await page.goto(ctUrl, { waitUntil: "domcontentloaded" });
await waitForPlaywrightApi(page);

const lot = await fetchLot();
console.log("Lot:", lot.sku || lot.id, "-", (lot.auction_title || "").slice(0, 60));

const fillResult = await page.evaluate(async (payload) => {
  return window.__estateEzPlaywright.fillLot(payload);
}, lot);
console.log("Detail autofill:", JSON.stringify(fillResult, null, 2));
if (!fillResult?.ok) {
  console.error("Autofill failed.");
  await context.close();
  process.exit(1);
}

const st = await page.evaluate(() => window.__estateEzPlaywright.clickStep("settings"));
console.log("→ Settings tab:", JSON.stringify(st));
await sleep(1400);

const mt = await page.evaluate(() => window.__estateEzPlaywright.clickStep("media"));
console.log("→ Media tab:", JSON.stringify(mt));
await sleep(2200);

if (!skipPhotos) {
  const files = await buildUploadFiles();
  if (!files.length) {
    console.log("No photos on lot — skipping upload.");
  } else {
    console.log(`Uploading ${files.length} image(s)…`);
    const up = await page.evaluate(
      async ({ id, list }) => window.__estateEzPlaywright.uploadPhotos(id, list),
      { id: lot.id, list: files }
    );
    console.log("Upload:", JSON.stringify(up, null, 2));
    if (!up?.ok) {
      console.error("Photo upload failed.");
      await context.close();
      process.exit(1);
    }
  }
} else {
  console.log("ESTATE_SKIP_PHOTOS=1 — skipped uploads.");
}

const clicked = await tryClickSave(page);
console.log("Save click attempted:", clicked);

const saved = await page.evaluate(
  async ({ id, timeoutMs: t }) => {
    const P = window.__estateEzPlaywright;
    const start = Date.now();
    while (Date.now() - start < t) {
      const s = await P.getSaveGate(id);
      if (s?.saved) return { ok: true, gate: s };
      await new Promise((r) => setTimeout(r, 900));
    }
    const last = await P.getSaveGate(id);
    return { ok: false, gate: last };
  },
  { id: lot.id, timeoutMs: saveWaitMs }
);
console.log("Save gate:", JSON.stringify(saved, null, 2));

if (saved?.ok && !skipMark) {
  try {
    const patched = await markLotCompleted();
    console.log("Marked completed:", patched?.sku || patched?.id || "ok");
  } catch (e) {
    console.error("PATCH completed failed:", String(e));
  }
} else if (skipMark) {
  console.log("ESTATE_SKIP_MARK_COMPLETED=1 — skipped PATCH.");
} else {
  console.error("Save not detected in time — click Save on CT manually; you can re-run with PATCH only later.");
}

console.log("");
console.log("Done. Browser left open. Ctrl+C to close.");
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
