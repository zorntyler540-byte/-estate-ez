"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { apiFetch } from "../../lib/api";
import { accountRoleLabel, isClientRole } from "../../lib/roles";

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
  assigned_sale_id?: string | null;
  assigned_sale_name?: string | null;
};

type SaleOption = {
  id: string;
  name: string;
  closed_at?: string | null;
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
  const [accountSales, setAccountSales] = useState<SaleOption[]>([]);
  const [clientEmail, setClientEmail] = useState("");
  const [clientPassword, setClientPassword] = useState("");
  const [clientSaleId, setClientSaleId] = useState("");
  const [clientBusy, setClientBusy] = useState(false);
  const [clientSaleChoice, setClientSaleChoice] = useState<Record<string, string>>({});
  const [clientSaleBusyId, setClientSaleBusyId] = useState("");
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
  const [facebookConfigured, setFacebookConfigured] = useState(false);
  const [facebookConnected, setFacebookConnected] = useState(false);
  const [facebookNeedsReconnect, setFacebookNeedsReconnect] = useState(false);
  const [facebookCalendarAuto, setFacebookCalendarAuto] = useState(false);
  const [facebookPageName, setFacebookPageName] = useState("");
  const [facebookPages, setFacebookPages] = useState<Array<{ id: string; name: string; can_post: boolean; selected: boolean }>>(
    []
  );
  const [facebookBusy, setFacebookBusy] = useState(false);
  const [facebookMsg, setFacebookMsg] = useState("");
  const [facebookErr, setFacebookErr] = useState("");
  const [facebookPosts, setFacebookPosts] = useState<
    Array<{
      id: string;
      lot_id?: string | null;
      status: string;
      facebook_post_id?: string | null;
      caption: string;
      created_at: string;
      error_message?: string | null;
    }>
  >([]);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void apiFetch("/auth/me", t)
      .then((data) => {
        const row = data as Me;
        if (isClientRole(row.role)) {
          router.replace("/client");
          return;
        }
        setMe(row);
      })
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
    const params = new URLSearchParams(window.location.search);
    const fbFlag = params.get("facebook") || "";
    const fbError = params.get("facebook_error") || "";
    if (fbError) setFacebookErr(fbError);
    else if (fbFlag === "connected") setFacebookMsg("Facebook Page connected.");
    void apiFetch("/integrations/facebook/status", t)
      .then((data) => {
        const row = data as {
          configured?: boolean;
          connected?: boolean;
          needs_reconnect?: boolean;
          page_name?: string | null;
          calendar_auto_approve?: boolean;
        };
        setFacebookConfigured(Boolean(row.configured));
        setFacebookConnected(Boolean(row.connected));
        setFacebookNeedsReconnect(Boolean(row.needs_reconnect));
        setFacebookPageName((row.page_name || "").trim());
        setFacebookCalendarAuto(Boolean(row.calendar_auto_approve));
        if (row.configured && (fbFlag === "pick" || (row.connected && !row.page_name))) {
          void apiFetch("/integrations/facebook/pages", t)
            .then((pages) => setFacebookPages(pages as typeof facebookPages))
            .catch(() => undefined);
        }
        if (row.connected) {
          void apiFetch("/facebook/posts?limit=12", t)
            .then((rows) => setFacebookPosts(rows as typeof facebookPosts))
            .catch(() => undefined);
        }
      })
      .catch(() => {
        /* feature hidden until API with META_APP_ID is deployed */
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

  async function startFacebookConnect() {
    if (!token) return;
    setFacebookBusy(true);
    setFacebookErr("");
    setFacebookMsg("");
    try {
      const res = (await apiFetch("/integrations/facebook/oauth/start", token, {
        credentials: "include"
      })) as { url: string };
      if (res.url) window.location.href = res.url;
    } catch (e) {
      setFacebookErr(e instanceof Error ? e.message : "Could not start Facebook sign-in");
      setFacebookBusy(false);
    }
  }

  async function selectFacebookPage(pageId: string) {
    if (!token) return;
    setFacebookBusy(true);
    setFacebookErr("");
    try {
      const res = (await apiFetch("/integrations/facebook/pages/select", token, {
        method: "POST",
        body: JSON.stringify({ page_id: pageId })
      })) as { connected?: boolean; page_name?: string | null; needs_reconnect?: boolean };
      setFacebookConnected(Boolean(res.connected));
      setFacebookNeedsReconnect(Boolean(res.needs_reconnect));
      setFacebookPageName((res.page_name || "").trim());
      setFacebookMsg(`Posting as ${res.page_name || "that Page"}.`);
    } catch (e) {
      setFacebookErr(e instanceof Error ? e.message : "Could not select that Page");
    } finally {
      setFacebookBusy(false);
    }
  }

  async function toggleFacebookCalendarAuto(next: boolean) {
    if (!token) return;
    setFacebookBusy(true);
    setFacebookErr("");
    try {
      const res = (await apiFetch("/integrations/facebook/settings", token, {
        method: "PATCH",
        body: JSON.stringify({ calendar_auto_approve: next })
      })) as { calendar_auto_approve?: boolean };
      setFacebookCalendarAuto(Boolean(res.calendar_auto_approve));
      setFacebookMsg(
        next
          ? "New Push-calendar slots will be scheduled automatically as Facebook Page posts."
          : "Push-calendar slots need an approve click before they go to the Page."
      );
    } catch (e) {
      setFacebookErr(e instanceof Error ? e.message : "Could not save Facebook calendar setting");
    } finally {
      setFacebookBusy(false);
    }
  }

  async function disconnectFacebook() {
    if (!token) return;
    if (!window.confirm("Disconnect this Facebook Page from Estate-EZ? Stored tokens are deleted.")) return;
    setFacebookBusy(true);
    setFacebookErr("");
    setFacebookMsg("");
    try {
      await apiFetch("/integrations/facebook/connection", token, { method: "DELETE" });
      setFacebookConnected(false);
      setFacebookNeedsReconnect(false);
      setFacebookPageName("");
      setFacebookPages([]);
      setFacebookPosts([]);
      setFacebookMsg("Facebook Page disconnected.");
    } catch (e) {
      setFacebookErr(e instanceof Error ? e.message : "Could not disconnect");
    } finally {
      setFacebookBusy(false);
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
      const sales = (await apiFetch("/sales", token)) as SaleOption[];
      setAccountSales(sales);
      setClientSaleId((prev) => prev || sales.find((s) => !s.closed_at)?.id || sales[0]?.id || "");
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

  async function addClientLogin() {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    if (!clientEmail.trim() || clientPassword.trim().length < 8 || !clientSaleId) return;
    setClientBusy(true);
    setTeamError("");
    try {
      await apiFetch("/team/members", token, {
        method: "POST",
        body: JSON.stringify({
          email: clientEmail.trim(),
          password: clientPassword,
          role: "client",
          sale_id: clientSaleId
        })
      });
      setClientEmail("");
      setClientPassword("");
      await refreshTeamMembers();
    } catch (e) {
      setTeamError(e instanceof Error ? e.message : "Could not add client login");
    } finally {
      setClientBusy(false);
    }
  }

  async function saveClientSale(memberId: string) {
    if (!token || !me || me.role.toLowerCase() !== "owner") return;
    const saleId = (clientSaleChoice[memberId] || "").trim();
    if (!saleId) return;
    setClientSaleBusyId(memberId);
    setTeamError("");
    try {
      await apiFetch(`/team/members/${memberId}/sale`, token, {
        method: "PATCH",
        body: JSON.stringify({ sale_id: saleId })
      });
      await refreshTeamMembers();
    } catch (e) {
      setTeamError(e instanceof Error ? e.message : "Could not change the assigned sale");
    } finally {
      setClientSaleBusyId("");
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
      <div className="ct-dashboard" style={{ maxWidth: 1100 }}>
      <header className="ct-page-header">
        <div>
          <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
            Account
          </h1>
          <p className="ct-subtitle" style={{ margin: 0 }}>
            {me?.role?.toLowerCase() === "owner"
              ? "Who you are in the app, password, feedback, and teammates. Catalog counts are on the Team tab."
              : "Who you are in the app, password, and feedback."}
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
              <p style={{ margin: "0.25rem 0 0" }}>{accountRoleLabel(me.role)}</p>
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

      {facebookConfigured ? (
        <section className="ct-card ct-card--padded" style={{ marginTop: "1.25rem" }}>
          <h2 className="ct-section-title" style={{ marginTop: 0 }}>
            Facebook Page
          </h2>
          <p className="ct-lead" style={{ marginTop: 0, marginBottom: "0.55rem", fontSize: "0.95rem" }}>
            Connect the franchise Facebook Page to post catalog lots (multi-photo, editable caption).
            Push-calendar Instagram, Stories, and TikTok slots currently publish as Page feed posts.
            Native Instagram, Groups, and Marketplace are not available here. Owners only to connect.
          </p>
          {facebookMsg ? <div className="ct-banner ct-banner--success">{facebookMsg}</div> : null}
          {facebookErr ? <div className="ct-banner ct-banner--error">{facebookErr}</div> : null}
          {facebookNeedsReconnect ? (
            <div className="ct-banner ct-banner--warning">Facebook access expired. Reconnect the Page.</div>
          ) : null}
          {facebookConnected ? (
            <p style={{ marginTop: 0 }}>
              Connected to <strong>{facebookPageName || "a Facebook Page"}</strong>
            </p>
          ) : (
            <p className="ct-muted" style={{ marginTop: 0 }}>
              Not connected.
            </p>
          )}
          {me?.role?.toLowerCase() === "owner" ? (
            <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap" }}>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                disabled={facebookBusy}
                onClick={() => void startFacebookConnect()}
              >
                {facebookBusy
                  ? "Working…"
                  : facebookConnected || facebookNeedsReconnect
                    ? "Reconnect Facebook"
                    : "Connect Facebook Page"}
              </button>
              {facebookConnected ? (
                <button type="button" className="ct-btn" disabled={facebookBusy} onClick={() => void disconnectFacebook()}>
                  Disconnect
                </button>
              ) : null}
            </div>
          ) : (
            <p className="ct-muted">Ask an account owner to connect the Facebook Page.</p>
          )}
          {me?.role?.toLowerCase() === "owner" && facebookConnected ? (
            <label className="ct-row" style={{ gap: "0.45rem", marginTop: "0.85rem" }}>
              <input
                type="checkbox"
                checked={facebookCalendarAuto}
                disabled={facebookBusy}
                onChange={(e) => void toggleFacebookCalendarAuto(e.target.checked)}
              />
              <span>
                Auto-approve new Push-calendar slots as Facebook Page posts (off by default). Every
                spreadsheet slot (9:00 Facebook, 12:15 Instagram, 16:30 Stories, 18:00 TikTok, closing
                17:40) is scheduled on the Page until a native publisher exists.
              </span>
            </label>
          ) : null}
          {facebookPages.length > 1 || (facebookPages.length === 1 && !facebookConnected) ? (
            <div style={{ marginTop: "0.85rem" }}>
              <p className="ct-muted" style={{ marginTop: 0 }}>
                Choose which Page Estate-EZ should post to.
              </p>
              {facebookPages.map((page) => (
                <label key={page.id} className="ct-row" style={{ gap: "0.45rem", marginBottom: "0.4rem" }}>
                  <input
                    type="radio"
                    name="fb-page"
                    checked={page.selected}
                    disabled={facebookBusy || !page.can_post}
                    onChange={() => void selectFacebookPage(page.id)}
                  />
                  <span>
                    {page.name}
                    {page.can_post ? "" : " (no posting permission)"}
                  </span>
                </label>
              ))}
            </div>
          ) : null}
          {facebookPosts.length > 0 ? (
            <div style={{ marginTop: "1rem" }}>
              <h3 className="ct-section-title" style={{ fontSize: "1rem" }}>
                Recent Page posts
              </h3>
              <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                {facebookPosts.map((row) => (
                  <li key={row.id}>
                    {row.status}
                    {row.facebook_post_id ? ` · ${row.facebook_post_id}` : ""}
                    {row.error_message ? ` · ${row.error_message}` : ""}
                    <div className="ct-muted">{row.caption.slice(0, 140)}</div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}

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
            Photos, posted/sold, and minutes per lot are on the <Link href="/activity">Team</Link> tab.
          </p>
          <details className="ct-help-details" style={{ marginBottom: "0.75rem" }}>
            <summary>Roles</summary>
            <div className="ct-help-details__body">
              <strong>Catalogger</strong> — catalog lots, edit listings, add photos, and post to CT Seller.
              They cannot delete lots, clear a sale, close a sale, run audience hunts, manage teammates,
              or connect CT Seller. <strong>Owner</strong> — full access. Choose a temporary password; they
              can change it after signing in.
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
          {teamMembers.filter((m) => !isClientRole(m.role)).length === 0 ? (
            <p className="ct-muted" style={{ marginBottom: 0 }}>
              No team members yet.
            </p>
          ) : (
            <div className="ct-stack">
              {teamMembers.filter((m) => !isClientRole(m.role)).map((m) => (
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
          <h2 className="ct-section-title" style={{ marginTop: 0 }}>
            Client logins
          </h2>
          <p className="ct-lead" style={{ marginTop: "0.35rem", marginBottom: "0.5rem", fontSize: "0.95rem" }}>
            A client can sign in on this website or the phone app and upload photos into one sale you choose.
            They cannot open other sales, edit listings, or post to CT Seller.
          </p>
          {accountSales.length === 0 ? (
            <p className="ct-muted">Create a sale on the dashboard before adding a client login.</p>
          ) : (
            <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", marginBottom: "0.75rem" }}>
              <input
                className="ct-input"
                style={{ maxWidth: 300, width: "100%" }}
                placeholder="client@email.com"
                value={clientEmail}
                onChange={(e) => setClientEmail(e.target.value)}
                autoComplete="off"
              />
              <input
                className="ct-input"
                style={{ maxWidth: 260, width: "100%" }}
                type="password"
                placeholder="Temporary password (min 8)"
                value={clientPassword}
                onChange={(e) => setClientPassword(e.target.value)}
                autoComplete="new-password"
              />
              <select
                className="ct-input"
                style={{ maxWidth: 280 }}
                value={clientSaleId}
                onChange={(e) => setClientSaleId(e.target.value)}
                aria-label="Sale this client uploads into"
              >
                {accountSales.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.closed_at ? " (closed)" : ""}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                disabled={
                  clientBusy || !clientEmail.trim() || clientPassword.trim().length < 8 || !clientSaleId
                }
                onClick={() => void addClientLogin()}
              >
                {clientBusy ? "Adding…" : "Add client login"}
              </button>
            </div>
          )}
          {teamMembers.filter((m) => isClientRole(m.role)).length === 0 ? (
            <p className="ct-muted" style={{ marginBottom: 0 }}>
              No client logins yet.
            </p>
          ) : (
            <div className="ct-stack">
              {teamMembers.filter((m) => isClientRole(m.role)).map((m) => {
                const selectedSale = clientSaleChoice[m.id] || m.assigned_sale_id || "";
                return (
                  <div key={m.id} className="ct-card ct-card--padded">
                    <p style={{ margin: 0, fontWeight: 600 }}>{m.email}</p>
                    <p className="ct-muted" style={{ margin: "0.3rem 0 0", fontSize: "0.86rem" }}>
                      Uploads to {m.assigned_sale_name || "no sale"} · Added {new Date(m.created_at).toLocaleString()}
                    </p>
                    <div className="ct-row ct-row--gap-lg" style={{ marginTop: "0.55rem", flexWrap: "wrap" }}>
                      <select
                        className="ct-input"
                        style={{ maxWidth: 280 }}
                        value={selectedSale}
                        onChange={(e) => setClientSaleChoice((prev) => ({ ...prev, [m.id]: e.target.value }))}
                        aria-label={`Sale for ${m.email}`}
                      >
                        {accountSales.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                            {s.closed_at ? " (closed)" : ""}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        disabled={
                          clientSaleBusyId === m.id || !selectedSale || selectedSale === (m.assigned_sale_id || "")
                        }
                        onClick={() => void saveClientSale(m.id)}
                      >
                        {clientSaleBusyId === m.id ? "Saving…" : "Change sale"}
                      </button>
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
                );
              })}
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
