"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { apiFetch } from "../../lib/api";

type Me = {
  id: string;
  account_id: string;
  email: string;
  role: string;
};

type SavedSuggestion = {
  saved_at: string;
  subject?: string;
  user_email?: string;
  message?: string;
};

type TeamMember = {
  id: string;
  email: string;
  role: string;
  created_at: string;
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
  const [savedSuggestions, setSavedSuggestions] = useState<SavedSuggestion[]>([]);
  const [savedLoading, setSavedLoading] = useState(false);
  const [savedError, setSavedError] = useState("");
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [teamLoading, setTeamLoading] = useState(false);
  const [teamError, setTeamError] = useState("");
  const [newMemberEmail, setNewMemberEmail] = useState("");
  const [newMemberPassword, setNewMemberPassword] = useState("");
  const [newMemberRole, setNewMemberRole] = useState<"catalogger" | "owner">("catalogger");
  const [newMemberBusy, setNewMemberBusy] = useState(false);
  const [resetPasswords, setResetPasswords] = useState<Record<string, string>>({});
  const [resetBusyId, setResetBusyId] = useState("");
  const [selfCurrentPassword, setSelfCurrentPassword] = useState("");
  const [selfNewPassword, setSelfNewPassword] = useState("");
  const [selfPasswordBusy, setSelfPasswordBusy] = useState(false);
  const [selfPasswordMsg, setSelfPasswordMsg] = useState("");
  const [selfPasswordErr, setSelfPasswordErr] = useState("");
  const [ctSellerConnected, setCtSellerConnected] = useState(false);
  const [ctSellerUsername, setCtSellerUsername] = useState("");
  const [ctSellerUserInput, setCtSellerUserInput] = useState("");
  const [ctSellerPassword, setCtSellerPassword] = useState("");
  const [ctSellerBusy, setCtSellerBusy] = useState(false);
  const [ctSellerMsg, setCtSellerMsg] = useState("");
  const [ctSellerErr, setCtSellerErr] = useState("");

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
    void apiFetch("/integrations/ctseller", t)
      .then((data) => {
        const row = data as { connected?: boolean; username?: string | null };
        setCtSellerConnected(Boolean(row.connected));
        setCtSellerUsername((row.username || "").trim());
        if (row.username) setCtSellerUserInput(String(row.username));
      })
      .catch(() => {
        /* optional until backend redeploys */
      });
  }, [router]);

  async function connectCtSeller() {
    if (!token || !ctSellerUserInput.trim() || !ctSellerPassword) return;
    setCtSellerBusy(true);
    setCtSellerErr("");
    setCtSellerMsg("");
    try {
      const res = (await apiFetch("/integrations/ctseller/credentials", token, {
        method: "PUT",
        body: JSON.stringify({
          username: ctSellerUserInput.trim(),
          password: ctSellerPassword
        })
      })) as { connected?: boolean; username?: string | null };
      setCtSellerConnected(Boolean(res.connected));
      setCtSellerUsername((res.username || ctSellerUserInput).trim());
      setCtSellerPassword("");
      setCtSellerMsg("Connected to CT Seller. You can post lots from the lot detail page.");
    } catch (e) {
      setCtSellerErr(e instanceof Error ? e.message : "Could not connect");
    } finally {
      setCtSellerBusy(false);
    }
  }

  async function disconnectCtSeller() {
    if (!token) return;
    if (!window.confirm("Remove stored CT Seller credentials from this account?")) return;
    setCtSellerBusy(true);
    setCtSellerErr("");
    setCtSellerMsg("");
    try {
      await apiFetch("/integrations/ctseller/credentials", token, { method: "DELETE" });
      setCtSellerConnected(false);
      setCtSellerUsername("");
      setCtSellerPassword("");
      setCtSellerMsg("CT Seller disconnected.");
    } catch (e) {
      setCtSellerErr(e instanceof Error ? e.message : "Could not disconnect");
    } finally {
      setCtSellerBusy(false);
    }
  }

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

  async function refreshSavedSuggestions() {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    setSavedLoading(true);
    setSavedError("");
    try {
      const rows = (await apiFetch("/feedback/suggestions/saved?limit=25", token)) as SavedSuggestion[];
      setSavedSuggestions(rows);
    } catch (e) {
      setSavedError(e instanceof Error ? e.message : "Could not load saved suggestions");
    } finally {
      setSavedLoading(false);
    }
  }

  useEffect(() => {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    void refreshSavedSuggestions();
  }, [token, me?.id]);

  async function refreshTeamMembers() {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    setTeamLoading(true);
    setTeamError("");
    try {
      const rows = (await apiFetch("/team/members", token)) as TeamMember[];
      setTeamMembers(rows);
    } catch (e) {
      setTeamError(e instanceof Error ? e.message : "Could not load team members");
    } finally {
      setTeamLoading(false);
    }
  }

  async function addTeamMember() {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    if (!newMemberEmail.trim() || !newMemberPassword.trim()) return;
    setNewMemberBusy(true);
    setTeamError("");
    try {
      await apiFetch("/team/members", token, {
        method: "POST",
        body: JSON.stringify({
          email: newMemberEmail.trim(),
          password: newMemberPassword,
          role: newMemberRole
        })
      });
      setNewMemberEmail("");
      setNewMemberPassword("");
      setNewMemberRole("catalogger");
      await refreshTeamMembers();
    } catch (e) {
      setTeamError(e instanceof Error ? e.message : "Could not add team member");
    } finally {
      setNewMemberBusy(false);
    }
  }

  async function resetMemberPassword(memberId: string) {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    const next = (resetPasswords[memberId] || "").trim();
    if (next.length < 8) return;
    setResetBusyId(memberId);
    setTeamError("");
    try {
      await apiFetch(`/team/members/${memberId}/reset-password`, token, {
        method: "POST",
        body: JSON.stringify({ new_password: next })
      });
      setResetPasswords((prev) => ({ ...prev, [memberId]: "" }));
    } catch (e) {
      setTeamError(e instanceof Error ? e.message : "Could not reset password");
    } finally {
      setResetBusyId("");
    }
  }

  async function changeOwnPassword() {
    if (!token) return;
    const current = selfCurrentPassword;
    const next = selfNewPassword.trim();
    if (!current || next.length < 8) return;
    setSelfPasswordBusy(true);
    setSelfPasswordErr("");
    setSelfPasswordMsg("");
    try {
      await apiFetch("/auth/password", token, {
        method: "PATCH",
        body: JSON.stringify({ current_password: current, new_password: next })
      });
      setSelfCurrentPassword("");
      setSelfNewPassword("");
      setSelfPasswordMsg("Password updated.");
    } catch (e) {
      setSelfPasswordErr(e instanceof Error ? e.message : "Could not update password");
    } finally {
      setSelfPasswordBusy(false);
    }
  }

  useEffect(() => {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    void refreshTeamMembers();
  }, [token, me?.id]);

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
      actions={
        <>
          <Link href="/dashboard" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
            Dashboard
          </Link>
          <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
            Log out
          </button>
        </>
      }
    >
      <div className="ct-dashboard" style={{ maxWidth: 640 }}>
      <header className="ct-page-header">
        <div>
          <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
            Account
          </h1>
          <p className="ct-subtitle" style={{ margin: 0 }}>
            Who you are in the app, password, feedback, and (for owners) teammates.
          </p>
        </div>
      </header>

      {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}

      <div className="ct-card ct-card--padded ct-card--interactive">
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
          Password
        </h2>
        {selfPasswordMsg ? <div className="ct-banner ct-banner--success">{selfPasswordMsg}</div> : null}
        {selfPasswordErr ? <div className="ct-banner ct-banner--error">{selfPasswordErr}</div> : null}
        <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap" }}>
          <input
            className="ct-input"
            style={{ maxWidth: 240, width: "100%" }}
            type="password"
            placeholder="Current password"
            value={selfCurrentPassword}
            onChange={(e) => {
              setSelfCurrentPassword(e.target.value);
              setSelfPasswordErr("");
              setSelfPasswordMsg("");
            }}
            autoComplete="current-password"
          />
          <input
            className="ct-input"
            style={{ maxWidth: 260, width: "100%" }}
            type="password"
            placeholder="New password (min 8)"
            value={selfNewPassword}
            onChange={(e) => {
              setSelfNewPassword(e.target.value);
              setSelfPasswordErr("");
              setSelfPasswordMsg("");
            }}
            autoComplete="new-password"
          />
          <button
            type="button"
            className="ct-btn ct-btn--primary"
            disabled={selfPasswordBusy || !selfCurrentPassword || selfNewPassword.trim().length < 8}
            onClick={() => void changeOwnPassword()}
          >
            {selfPasswordBusy ? "Updating…" : "Update password"}
          </button>
        </div>
      </section>

      <section className="ct-card ct-card--padded" style={{ marginTop: "1.25rem" }}>
        <h2 className="ct-section-title" style={{ marginTop: 0 }}>
          CT Seller
        </h2>
        <p className="ct-lead" style={{ marginTop: 0, marginBottom: "0.55rem", fontSize: "0.95rem" }}>
          Connect seller.ctbids.com once so the dashboard can post lots for you. We store the
          password encrypted and refresh the CT Seller session automatically — you should not need
          to reconnect unless they change your password. Owners only.
        </p>
        {ctSellerMsg ? <div className="ct-banner ct-banner--success">{ctSellerMsg}</div> : null}
        {ctSellerErr ? <div className="ct-banner ct-banner--error">{ctSellerErr}</div> : null}
        {ctSellerConnected ? (
          <p style={{ marginTop: 0 }}>
            Connected as <strong>{ctSellerUsername || "seller user"}</strong>
          </p>
        ) : (
          <p className="ct-muted" style={{ marginTop: 0 }}>
            Not connected.
          </p>
        )}
        {me?.role?.toLowerCase() === "owner" ? (
          <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
            <input
              className="ct-input"
              style={{ maxWidth: 260, width: "100%" }}
              type="text"
              placeholder="CT Seller email / username"
              value={ctSellerUserInput}
              onChange={(e) => {
                setCtSellerUserInput(e.target.value);
                setCtSellerErr("");
                setCtSellerMsg("");
              }}
              autoComplete="username"
            />
            <input
              className="ct-input"
              style={{ maxWidth: 240, width: "100%" }}
              type="password"
              placeholder={ctSellerConnected ? "Password (only if CT Seller changed it)" : "CT Seller password"}
              value={ctSellerPassword}
              onChange={(e) => {
                setCtSellerPassword(e.target.value);
                setCtSellerErr("");
                setCtSellerMsg("");
              }}
              autoComplete="current-password"
            />
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              disabled={ctSellerBusy || !ctSellerUserInput.trim() || !ctSellerPassword}
              onClick={() => void connectCtSeller()}
            >
              {ctSellerBusy ? "Connecting…" : ctSellerConnected ? "Update saved password" : "Connect"}
            </button>
            {ctSellerConnected ? (
              <button
                type="button"
                className="ct-btn"
                disabled={ctSellerBusy}
                onClick={() => void disconnectCtSeller()}
              >
                Disconnect
              </button>
            ) : null}
          </div>
        ) : (
          <p className="ct-muted" style={{ marginBottom: 0 }}>
            Ask an account owner to connect CT Seller.
          </p>
        )}
      </section>

      <section className="ct-card ct-card--padded" style={{ marginTop: "1.25rem" }}>
        <h2 className="ct-section-title" style={{ marginTop: 0 }}>
          Suggestions and improvements
        </h2>
        <p className="ct-lead" style={{ marginTop: 0, marginBottom: "0.55rem", fontSize: "0.95rem" }}>
          Send ideas or problems to the product team. Your email is attached automatically.
        </p>
        <details className="ct-help-details" style={{ marginBottom: "0.75rem" }}>
          <summary>What happens when you send</summary>
          <div className="ct-help-details__body">
            Your message may be emailed to support when delivery is configured; otherwise it can be stored on the server
            for staff to read (you will see a confirmation on screen).
          </div>
        </details>
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
          onClick={() =>
            void submitSuggestion().then(() => {
              if (me?.role?.toLowerCase() === "owner") void refreshSavedSuggestions();
            })
          }
        >
          {suggestionBusy ? "Sending…" : "Send to team"}
        </button>
      </section>
      {me?.role?.toLowerCase() === "owner" ? (
        <section className="ct-card ct-card--padded" style={{ marginTop: "1.25rem" }}>
          <h2 className="ct-section-title" style={{ marginTop: 0 }}>
            Team cataloggers
          </h2>
          <p className="ct-lead" style={{ marginTop: "0.35rem", marginBottom: "0.5rem", fontSize: "0.95rem" }}>
            Invite people who can sign in and help catalog <strong>this</strong> account&apos;s sales.
          </p>
          <details className="ct-help-details" style={{ marginBottom: "0.75rem" }}>
            <summary>Roles</summary>
            <div className="ct-help-details__body">
              <strong>Catalogger</strong> — work on lots and photos. <strong>Owner</strong> — same, plus team
              management and account-level actions. Choose a temporary password; they can change it after signing in.
            </div>
          </details>
          {teamError ? <div className="ct-banner ct-banner--error">{teamError}</div> : null}
          <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", marginBottom: "0.75rem" }}>
            <input
              className="ct-input"
              style={{ maxWidth: 300, width: "100%" }}
              placeholder="catalogger@email.com"
              value={newMemberEmail}
              onChange={(e) => setNewMemberEmail(e.target.value)}
              autoComplete="off"
            />
            <input
              className="ct-input"
              style={{ maxWidth: 260, width: "100%" }}
              type="password"
              placeholder="Temporary password (min 8)"
              value={newMemberPassword}
              onChange={(e) => setNewMemberPassword(e.target.value)}
              autoComplete="new-password"
            />
            <select
              className="ct-input"
              style={{ maxWidth: 150 }}
              value={newMemberRole}
              onChange={(e) => setNewMemberRole((e.target.value as "catalogger" | "owner") || "catalogger")}
            >
              <option value="catalogger">Catalogger (edit inventory)</option>
              <option value="owner">Owner (full access)</option>
            </select>
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              disabled={newMemberBusy || !newMemberEmail.trim() || newMemberPassword.trim().length < 8}
              onClick={() => void addTeamMember()}
            >
              {newMemberBusy ? "Adding…" : "Add team member"}
            </button>
            <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void refreshTeamMembers()} disabled={teamLoading}>
              {teamLoading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
          {teamMembers.length === 0 ? (
            <p className="ct-muted" style={{ marginBottom: 0 }}>
              No team members yet.
            </p>
          ) : (
            <div className="ct-stack">
              {teamMembers.map((m) => (
                <div key={m.id} className="ct-card ct-card--padded">
                  <p style={{ margin: 0, fontWeight: 600 }}>{m.email}</p>
                  <p className="ct-muted" style={{ margin: "0.3rem 0 0", fontSize: "0.86rem" }}>
                    Role: {m.role} · Added {new Date(m.created_at).toLocaleString()}
                  </p>
                  <div className="ct-row ct-row--gap-lg" style={{ marginTop: "0.55rem", flexWrap: "wrap" }}>
                    <input
                      className="ct-input"
                      style={{ maxWidth: 260, width: "100%" }}
                      type="password"
                      placeholder="Reset password (min 8)"
                      value={resetPasswords[m.id] || ""}
                      onChange={(e) => setResetPasswords((prev) => ({ ...prev, [m.id]: e.target.value }))}
                      autoComplete="new-password"
                    />
                    <button
                      type="button"
                      className="ct-btn ct-btn--ghost"
                      disabled={resetBusyId === m.id || (resetPasswords[m.id] || "").trim().length < 8}
                      onClick={() => void resetMemberPassword(m.id)}
                    >
                      {resetBusyId === m.id ? "Resetting…" : "Reset password"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : null}
      {me?.role?.toLowerCase() === "owner" ? (
        <section className="ct-card ct-card--padded" style={{ marginTop: "1.25rem" }}>
          <div className="ct-row ct-row--gap-lg" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h2 className="ct-section-title" style={{ margin: 0 }}>
              Recent saved suggestions
            </h2>
            <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void refreshSavedSuggestions()} disabled={savedLoading}>
              {savedLoading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
          <p className="ct-lead" style={{ marginTop: "0.45rem", marginBottom: "0.5rem", fontSize: "0.92rem" }}>
            Internal copies of feedback when email is not set up yet.
          </p>
          <details className="ct-help-details" style={{ marginBottom: "0.65rem" }}>
            <summary>Who sees this</summary>
            <div className="ct-help-details__body">
              Owners can refresh this list. Entries are stored on the server for staff review when outbound email is
              unavailable.
            </div>
          </details>
          {savedError ? <div className="ct-banner ct-banner--error">{savedError}</div> : null}
          {savedSuggestions.length === 0 ? (
            <p className="ct-muted" style={{ marginBottom: 0 }}>
              No saved suggestions yet.
            </p>
          ) : (
            <div className="ct-stack">
              {savedSuggestions.map((row, i) => (
                <div key={`${row.saved_at}-${i}`} className="ct-card ct-card--padded">
                  <p className="ct-muted" style={{ margin: 0, fontSize: "0.8rem" }}>
                    {new Date(row.saved_at).toLocaleString()} · {row.user_email || "Unknown user"}
                  </p>
                  <p style={{ margin: "0.45rem 0 0", whiteSpace: "pre-wrap" }}>{row.message || ""}</p>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : null}
      </div>
    </AppShell>
  );
}
