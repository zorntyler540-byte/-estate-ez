"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Download, Monitor } from "lucide-react";
import { AppShell } from "../../components/AppShell";
import { ShellActions } from "../../components/ShellActions";
import {
  macWatcherDownloadUrl,
  saleSuiteDownloadAnchorProps,
  saleSuiteDownloadUrl
} from "../../lib/saleSuiteDownload";

export default function ToolsPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const downloadUrl = useMemo(() => saleSuiteDownloadUrl(), []);
  const downloadProps = saleSuiteDownloadAnchorProps(downloadUrl);
  const macUrl = useMemo(() => macWatcherDownloadUrl(), []);
  const macProps = saleSuiteDownloadAnchorProps(macUrl);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) router.replace("/");
  }, [router]);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  if (!token) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Please sign in first.</p>
          <p style={{ margin: "1rem 0 0" }}>
            <Link href="/">Go to sign in</Link>
          </p>
        </div>
      </main>
    );
  }

  return (
    <AppShell
      actions={<ShellActions onLogout={logout} />}
    >
      <div className="ct-dashboard" style={{ maxWidth: 720 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Desktop tools
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Windows and Mac apps included with your franchise account — download, extract, and run.
            </p>
          </div>
        </header>

        <section className="ct-card ct-card--padded ct-card--interactive">
          <div style={{ display: "flex", gap: "0.85rem", alignItems: "flex-start" }}>
            <span
              aria-hidden
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 44,
                height: 44,
                borderRadius: 12,
                background: "var(--ct-accent-soft, #e6f6f5)",
                color: "var(--ct-accent, #2a9d9c)",
                flexShrink: 0
              }}
            >
              <Monitor size={22} strokeWidth={1.75} />
            </span>
            <div className="ct-stack" style={{ flex: 1 }}>
              <div>
                <h2 className="ct-section-title" style={{ margin: 0 }}>
                  CTBids Sale Suite
                </h2>
                <p className="ct-muted" style={{ margin: "0.35rem 0 0" }}>
                  Live Watch during a sale (bids, Suspended / Unpaid / Paid) plus end-of-sale
                  reports from your CTBids CSV. Windows only. No Python install.
                </p>
              </div>

              <ol className="ct-muted" style={{ margin: 0, paddingLeft: "1.2rem", lineHeight: 1.55 }}>
                <li>Download the zip</li>
                <li>Right-click → Extract All…</li>
                <li>Open the folder → double-click <strong>CTBids Sale Suite.exe</strong></li>
                <li>
                  If Windows says “protected your PC”: <strong>More info → Run anyway</strong>
                </li>
              </ol>

              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.65rem", alignItems: "center" }}>
                <a className="ct-btn ct-btn--primary" {...downloadProps}>
                  <Download size={16} aria-hidden style={{ marginRight: 6 }} />
                  Download for Windows
                </a>
                <span className="ct-muted" style={{ fontSize: "0.85rem" }}>
                  Large file (includes Chromium for Sign in)
                </span>
              </div>

              <p className="ct-muted" style={{ margin: 0, fontSize: "0.85rem" }}>
                Tip: edit <code>config.json</code> next to the .exe to set your franchise business
                name. Use your own CTBids Buyer + Seller logins inside Live Watch.
              </p>
            </div>
          </div>
        </section>

        <section className="ct-card ct-card--padded ct-card--interactive" style={{ marginTop: "1rem" }}>
          <div style={{ display: "flex", gap: "0.85rem", alignItems: "flex-start" }}>
            <span
              aria-hidden
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 44,
                height: 44,
                borderRadius: 12,
                background: "var(--ct-accent-soft, #e6f6f5)",
                color: "var(--ct-accent, #2a9d9c)",
                flexShrink: 0
              }}
            >
              <Monitor size={22} strokeWidth={1.75} />
            </span>
            <div className="ct-stack" style={{ flex: 1 }}>
              <div>
                <h2 className="ct-section-title" style={{ margin: 0 }}>
                  Estate-Ez Auction Watcher
                </h2>
                <p className="ct-muted" style={{ margin: "0.35rem 0 0" }}>
                  The same Live Watch and end-of-sale reports, for Apple silicon Macs (M1 or newer).
                  No Python install.
                </p>
              </div>

              <ol className="ct-muted" style={{ margin: 0, paddingLeft: "1.2rem", lineHeight: 1.55 }}>
                <li>Download the zip and double-click it</li>
                <li>
                  Keep the folder together → double-click <strong>Open Auction Watcher.command</strong>
                </li>
                <li>
                  If macOS blocks it: right-click <strong>Estate-Ez Auction Watcher.app</strong> → Open
                  → Open
                </li>
              </ol>

              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.65rem", alignItems: "center" }}>
                <a className="ct-btn ct-btn--primary" {...macProps}>
                  <Download size={16} aria-hidden style={{ marginRight: 6 }} />
                  Download for Mac
                </a>
                <span className="ct-muted" style={{ fontSize: "0.85rem" }}>
                  About 360 MB · Apple silicon only
                </span>
              </div>

              <p className="ct-muted" style={{ margin: 0, fontSize: "0.85rem" }}>
                Tip: edit <code>config.json</code> in that folder to set your franchise business name.
                Sign in with your Estate-Ez account.
              </p>
            </div>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
