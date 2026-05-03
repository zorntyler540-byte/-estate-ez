/**
 * Opens Chromium with a persistent profile under ./browser-profile/ so CT Seller
 * stays logged in between runs. Personal automation starter — extend with clicks/fills.
 *
 * Run: npm install && npm run install:browsers && npm run open
 */
import { chromium } from "playwright";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const userDataDir = path.join(__dirname, "..", "browser-profile");

const context = await chromium.launchPersistentContext(userDataDir, {
  headless: false,
  args: ["--disable-blink-features=AutomationControlled"]
});

const page = context.pages()[0] ?? (await context.newPage());
await page.goto("https://www.ctbids.com/", { waitUntil: "domcontentloaded" });

console.log("");
console.log("Chromium is open with profile:", userDataDir);
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
