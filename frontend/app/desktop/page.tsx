import type { Metadata } from "next";
import Link from "next/link";
import { Download } from "lucide-react";
import { saleSuiteDownloadAnchorProps, saleSuiteDownloadUrl } from "../../lib/saleSuiteDownload";

export const metadata: Metadata = {
  title: "Download CTBids Sale Suite",
  description:
    "Download CTBids Sale Suite for Windows — live sale watch and end-of-sale reports. No Python install."
};

const FEATURES = [
  "Watch live bids, usernames, and hammer totals",
  "See Suspended, Unpaid, and Paid during a sale",
  "Build end-of-sale reports from a CTBids CSV",
  "Create PDFs to email to clients"
];

export default function DesktopDownloadPage() {
  const downloadUrl = saleSuiteDownloadUrl();
  const downloadProps = saleSuiteDownloadAnchorProps(downloadUrl);

  return (
    <main className="ct-download-screen">
      <p className="ct-hero-brand" style={{ marginBottom: "1.25rem" }}>
        Desktop tools
      </p>

      <section className="ct-download-card ct-card ct-card--padded">
        <div className="ct-auth-brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            className="ct-download-logo"
            src="/zortron-logo.jpg"
            alt="Zortron Tech Solutions"
            width={88}
            height={88}
          />
          <h1 className="ct-page-title" style={{ marginBottom: "0.45rem" }}>
            CTBids Sale Suite
          </h1>
          <p className="ct-subtitle" style={{ marginBottom: 0 }}>
            Live Watch plus end-of-sale reports for Windows. Extract the zip and run — no Python
            install.
          </p>
        </div>

        <ul className="ct-download-features">
          {FEATURES.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>

        <a className="ct-btn ct-btn--primary ct-download-btn" {...downloadProps}>
          <Download size={18} aria-hidden />
          Download for Windows (64-bit)
        </a>
        <p className="ct-muted ct-download-note">
          About 700 MB · Google Drive will ask you to click Download
        </p>

        <ol className="ct-download-steps">
          <li>Download the zip, then right-click → Extract All…</li>
          <li>
            Open the folder and double-click <strong>CTBids Sale Suite.exe</strong>
          </li>
          <li>
            If Windows says it protected your PC: <strong>More info → Run anyway</strong>
          </li>
        </ol>
      </section>

      <p className="ct-download-foot">
        Already have an Estate-EZ account? <Link href="/">Sign in</Link>
      </p>
    </main>
  );
}
