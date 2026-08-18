/** Default path when the zip is served from this Next.js app (`public/downloads`). */
export const DEFAULT_SALE_SUITE_ZIP = "/downloads/CTBids-Sale-Suite.zip";

/** Direct HTTPS URL or same-origin path for CTBids Sale Suite (Windows). */
export function saleSuiteDownloadUrl(): string {
  const fromEnv = (process.env.NEXT_PUBLIC_SALE_SUITE_DOWNLOAD_URL || "").trim();
  return fromEnv || DEFAULT_SALE_SUITE_ZIP;
}
