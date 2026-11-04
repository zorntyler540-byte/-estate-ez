import { getAppValue, setAppValue } from "./queue/db";

const WIFI_ONLY_KEY = "upload_wifi_only";

let wifiOnlyCache: boolean | null = null;

/** "Upload on Wi‑Fi only" (default off). Photos and videos both wait for Wi‑Fi when on. */
export async function getUploadOnWifiOnly(): Promise<boolean> {
  if (wifiOnlyCache != null) return wifiOnlyCache;
  try {
    wifiOnlyCache = (await getAppValue(WIFI_ONLY_KEY)) === "1";
  } catch {
    wifiOnlyCache = false;
  }
  return wifiOnlyCache;
}

export async function setUploadOnWifiOnly(on: boolean): Promise<void> {
  wifiOnlyCache = on;
  await setAppValue(WIFI_ONLY_KEY, on ? "1" : "0");
}
