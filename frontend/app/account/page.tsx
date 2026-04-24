"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";

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

export default function AccountPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [suggestionText, setSuggestionText] = useState("");
  const [suggestionBusy, setSuggestionBusy] = useState(false);
  const [suggestionError, setSuggestionError] = useState("");
  const [suggestionSentState, setSuggestionSentState] = useState<"" | "sent" | "saved_local">("");

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

  async function submitSuggestion() {
    const t = token;
    const msg = suggestionText.trim();
    if (!t || !msg) return;
    setSuggestionBusy(true);
    setSuggestionError("");
    setSuggestionSentState("");
    try {
      const res = (await apiFetch("/feedback/suggestions", t, {
        method: "POST",
        body: JSON.stringify({ message: msg })
      })) as { status?: string };
      setSuggestionText("");
      setSuggestionSentState(res?.status === "saved_local" ? "saved_local" : "sent");
    } catch (e) {
      setSuggestionError(e instanceof Error ? e.message : "Could not send");
    } finally {
      setSuggestionBusy(false);
    }
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
    <main className="ct-shell" style={{ maxWidth: 640 }}>
      <p style={{ margin: "0 0 0.75rem" }}>
        <Link href="/dashboard">← Back to dashboard</Link>
      </p>
      <header className="ct-page-header">
        <div>
          <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
            Account
          </h1>
          <p className="ct-subtitle" style={{ margin: 0 }}>
            Your sign-in and workspace identifiers. Additional profile and billing fields can be added here later.
          </p>
        </div>
        <button type="button" className="ct-btn ct-btn--ghost" onClick={logout}>
          Log out
        </button>
      </header>

      {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}

      <div className="ct-card ct-card--padded">
        {loading ? (
          <p className="ct-muted" style={{ margin: 0 }}>
            Loading account…
          </p>
        ) : me ? (
          <div className="ct-stack">
            <div>
              <span className="ct-label">Email</span>
              <p style={{ margin: "0.25rem 0 0", fontWeight: 600 }}>{me.email}</p>
            </div>
            <div>
              <span className="ct-label">Role</span>
              <p style={{ margin: "0.25rem 0 0" }}>{me.role}</p>
            </div>
            <div>
              <span className="ct-label">User ID</span>
              <p style={{ margin: "0.25rem 0 0", fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
                {me.id}
              </p>
            </div>
            <div>
              <span className="ct-label">Account ID</span>
              <p style={{ margin: "0.25rem 0 0", fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
                {me.account_id}
              </p>
            </div>
          </div>
        ) : (
          <p className="ct-muted" style={{ margin: 0 }}>
            No profile data loaded.
          </p>
        )}
      </div>

      <section className="ct-card ct-card--padded" style={{ marginTop: "1.25rem" }}>
        <h2 className="ct-section-title" style={{ marginTop: 0 }}>
          Suggestions and improvements
        </h2>
        <p className="ct-muted" style={{ marginTop: 0, marginBottom: "0.75rem" }}>
          Ideas for the app, auction workflow, or anything else you would like us to consider. Your account email is
          included automatically with the message.
        </p>
        {suggestionSentState ? (
          <div className="ct-banner ct-banner--success" style={{ marginBottom: "0.75rem" }}>
            {suggestionSentState === "saved_local"
              ? "Thanks — your note was saved on this server (email is not configured yet)."
              : "Thanks — your note was sent."}
          </div>
        ) : null}
        {suggestionError ? (
          <div className="ct-banner ct-banner--error" style={{ marginBottom: "0.75rem", whiteSpace: "pre-wrap" }}>
            {suggestionError}
          </div>
        ) : null}
        <label className="ct-label" htmlFor="suggestion-msg">
          Your message
        </label>
        <textarea
          id="suggestion-msg"
          className="ct-textarea"
          style={{ minHeight: 140, marginBottom: "0.75rem" }}
          value={suggestionText}
          onChange={(e) => {
            setSuggestionText(e.target.value);
            setSuggestionSentState("");
          }}
          placeholder="Describe what you would like to see improved or added…"
          disabled={suggestionBusy}
        />
        <button
          type="button"
          className="ct-btn ct-btn--primary"
          disabled={suggestionBusy || !suggestionText.trim()}
          onClick={() => void submitSuggestion()}
        >
          {suggestionBusy ? "Sending…" : "Send to team"}
        </button>
      </section>
    </main>
  );
}
