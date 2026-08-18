/** Hosted zip (Google Drive). Env override wins when set at Next.js build time. */
export const DEFAULT_SALE_SUITE_ZIP =
  "https://drive.google.com/file/d/1bDhxQqcDpDCKNbCxD5y7p-ZjmSGI11iS/view?usp=sharing";

/** Direct HTTPS URL or same-origin path for CTBids Sale Suite (Windows). */
export function saleSuiteDownloadUrl(): string {
  const fromEnv = (process.env.NEXT_PUBLIC_SALE_SUITE_DOWNLOAD_URL || "").trim();
  return fromEnv || DEFAULT_SALE_SUITE_ZIP;
}

export function saleSuiteDownloadIsExternal(url: string = saleSuiteDownloadUrl()): boolean {
  return /^https?:\/\//i.test(url);
}

/** Props for the Download button (Drive links must open in a tab, not use the download attribute). */
export function saleSuiteDownloadAnchorProps(url: string = saleSuiteDownloadUrl()) {
  if (saleSuiteDownloadIsExternal(url)) {
    return { href: url, target: "_blank", rel: "noopener noreferrer" } as const;
  }
  return { href: url, download: true } as const;
}
