"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { TeamActivitySection } from "../../components/TeamActivitySection";
import { apiFetch } from "../../lib/api";
import { isOwnerRole } from "../../lib/roles";

type Me = {
  id: string;
  account_id: string;
  email: string;
  role: string;
};

function isAuthFailureMessage(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("invalid authentication") ||
    m.includes("401") ||
    m.includes("unauthorized")
  );
}

export default function TeamActivityPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void apiFetch("/auth/me", t)
      .then((data) => setMe(data as Me))
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load account";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
      })
      .finally(() => setLoading(false));
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

  const owner = isOwnerRole(me?.role);

  return (
    <AppShell
      actions={
        <>
          <Link href="/account" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
            Account
          </Link>
          <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
            Log out
          </button>
        </>
      }
    >
      <div className="ct-dashboard" style={{ maxWidth: 1400 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Team
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Catalog pace, CT Bids posted/sold, and minutes per lot. Password and invites stay in
              Settings.
            </p>
          </div>
        </header>

        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}

        {loading ? (
          <p className="ct-muted">Loading…</p>
        ) : !owner ? (
          <section className="ct-card ct-card--padded">
            <p style={{ margin: 0 }}>
              Team activity is for account owners. Ask an owner if you need these numbers.
            </p>
          </section>
        ) : (
          <TeamActivitySection token={token} viewerUserId={me?.id} />
        )}
      </div>
    </AppShell>
  );
}
