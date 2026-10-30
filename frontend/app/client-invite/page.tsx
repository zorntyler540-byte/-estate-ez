"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { apiFetch } from "../../lib/api";
import { rememberAccountRole } from "../../lib/roles";

function RedeemForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = (params.get("token") || "").trim();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function openLink() {
    if (!token) return;
    setBusy(true);
    setError("");
    try {
      const row = (await apiFetch("/auth/client-invite/redeem", undefined, {
        method: "POST",
        body: JSON.stringify({ token })
      })) as { access_token: string };
      localStorage.setItem("token", row.access_token);
      const me = (await apiFetch("/auth/me", row.access_token)) as { role?: string };
      rememberAccountRole(me.role);
      router.replace("/client");
    } catch (e) {
      setError(e instanceof Error ? e.message : "This link is not valid");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="ct-shell">
      <div className="ct-panel" style={{ maxWidth: 520 }}>
        <h1 className="ct-section-title" style={{ marginTop: 0 }}>
          Upload photos
        </h1>
        {!token ? (
          <p>This page needs the invite link from the sale owner.</p>
        ) : (
          <>
            <p className="ct-lead">
              This link signs you in to upload photos for one sale. It stops working when the owner revokes it
              or the end time is reached.
            </p>
            {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
            <button type="button" className="ct-btn ct-btn--primary" disabled={busy} onClick={() => void openLink()}>
              {busy ? "Opening…" : "Open my upload page"}
            </button>
          </>
        )}
        <p style={{ marginTop: "1rem" }}>
          <Link href="/">Estate-EZ sign in</Link>
        </p>
      </div>
    </main>
  );
}

export default function ClientInvitePage() {
  return (
    <Suspense fallback={<main className="ct-shell"><p>Loading…</p></main>}>
      <RedeemForm />
    </Suspense>
  );
}
