/**
 * Opens Chromium with a persistent profile under ./browser-profile/ so CT Seller
 * stays logged in between runs. If ../../extension/manifest.json exists, loads the
 * Estate-Ez unpacked extension so Playwright and the side panel share one profile.
 *
 * Run: npm install && npm run install:browsers && npm run open
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const userDataDir = path.join(__dirname, "..", "browser-profile");

const extDir = path.resolve(path.join(__dirname, "..", "..", "extension"));
const extManifest = path.join(extDir, "manifest.json");
const loadExtension = fs.existsSync(extManifest);
const args = ["--disable-blink-features=AutomationControlled"];
if (loadExtension) {
  const p = extDir.replace(/\\/g, "/");
  args.push(`--disable-extensions-except=${p}`, `--load-extension=${p}`);
}

const context = await chromium.launchPersistentContext(userDataDir, {
  headless: false,
  args
});

const page = context.pages()[0] ?? (await context.newPage());
await page.goto("https://seller.ctbids.com/", { waitUntil: "domcontentloaded" });

console.log("");
console.log("Chromium is open with profile:", userDataDir);
if (loadExtension) console.log("Estate-Ez extension loaded from:", extDir);
console.log("Log in to CT Seller once; later runs reuse the same cookies.");
console.log("Press Ctrl+C in this terminal to close the browser and exit.");
console.log("");

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
