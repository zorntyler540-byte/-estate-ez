"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";

type InviteRow = {
  id: string;
  email: string;
  user_id: string;
  access_expires_at?: string | null;
  expires_at?: string | null;
  accepted_at?: string | null;
  revoked_at?: string | null;
  created_at: string;
};

type UploadRow = {
  job_id: string;
  email: string;
  status: string;
  sku?: string | null;
  client_draft_id?: string | null;
  created_at: string;
  error_message?: string | null;
};

export function ClientSaleAccess({
  token,
  saleId,
  saleName
}: {
  token: string;
  saleId: string;
  saleName: string;
}) {
  const [email, setEmail] = useState("");
  const [expiresLocal, setExpiresLocal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [appLogin, setAppLogin] = useState<{ email: string; password: string } | null>(null);
  const [invites, setInvites] = useState<InviteRow[]>([]);
  const [uploads, setUploads] = useState<UploadRow[]>([]);

  const refresh = useCallback(async () => {
    const [inviteRows, uploadRows] = await Promise.all([
      apiFetch(`/sales/${encodeURIComponent(saleId)}/client-invites`, token) as Promise<InviteRow[]>,
      apiFetch(`/sales/${encodeURIComponent(saleId)}/client-uploads`, token) as Promise<UploadRow[]>
    ]);
    setInvites(inviteRows);
    setUploads(uploadRows);
  }, [saleId, token]);

  useEffect(() => {
    setAppLogin(null);
    setError("");
    void refresh().catch((e) => setError(e instanceof Error ? e.message : "Could not load client access"));
  }, [refresh]);

  async function invite() {
    if (!email.trim()) return;
    setBusy(true);
    setError("");
    setAppLogin(null);
    try {
      const body: { email: string; access_expires_at?: string } = { email: email.trim() };
      if (expiresLocal) {
        const when = new Date(expiresLocal);
        if (Number.isNaN(when.getTime())) throw new Error("Expiry is not a valid date");
        body.access_expires_at = when.toISOString();
      }
      const created = (await apiFetch(`/sales/${encodeURIComponent(saleId)}/client-invites`, token, {
        method: "POST",
        body: JSON.stringify(body)
      })) as { email: string; app_password: string };
      setAppLogin({ email: created.email, password: created.app_password });
      setEmail("");
      setExpiresLocal("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the invite");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(userId: string) {
    setError("");
    try {
      await apiFetch(`/sales/${encodeURIComponent(saleId)}/client-invites/${encodeURIComponent(userId)}`, token, {
        method: "DELETE"
      });
      setAppLogin(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not revoke access");
    }
  }

  return (
    <div className="ct-sale-watch" style={{ marginTop: "0.75rem" }}>
      <p className="ct-muted" style={{ margin: "0 0 0.35rem" }}>
        Give a client the Estate-EZ phone app. They sign in with the email and password shown here, then
        take pictures. Those photos upload into <strong>{saleName}</strong> only. Access stops at the end
        time, or as soon as you revoke it.
      </p>
      <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "center" }}>
        <input
          className="ct-input"
          style={{ maxWidth: 260 }}
          type="email"
          placeholder="client@email.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="off"
        />
        <input
          className="ct-input"
          style={{ maxWidth: 220 }}
          type="datetime-local"
          aria-label="Access ends"
          value={expiresLocal}
          onChange={(e) => setExpiresLocal(e.target.value)}
        />
        <button type="button" className="ct-btn" disabled={busy || !email.trim()} onClick={() => void invite()}>
          {busy ? "Creating…" : "Create app login"}
        </button>
      </div>
      {appLogin ? (
        <div className="ct-banner ct-banner--success" style={{ marginTop: "0.45rem" }}>
          <p style={{ margin: "0 0 0.35rem" }}>
            Shown once. Have them open the <strong>Estate-EZ</strong> app and sign in. Pictures they take go
            into this sale.
          </p>
          <p style={{ margin: 0 }}>
            Email: <strong>{appLogin.email}</strong>
            <br />
            Password: <strong>{appLogin.password}</strong>
          </p>
        </div>
      ) : null}
      {error ? (
        <p className="ct-banner ct-banner--error" style={{ marginTop: "0.45rem" }}>
          {error}
        </p>
      ) : null}
      {invites.length > 0 ? (
        <ul className="ct-muted" style={{ margin: "0.55rem 0 0", paddingLeft: "1.1rem", fontSize: "0.86rem" }}>
          {invites.map((row) => (
            <li key={row.id}>
              {row.email}
              {row.revoked_at ? " · revoked" : row.accepted_at ? " · signed in" : " · not signed in yet"}
              {row.access_expires_at ? ` · ends ${new Date(row.access_expires_at).toLocaleString()}` : ""}
              {!row.revoked_at ? (
                <>
                  {" "}
                  <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void revoke(row.user_id)}>
                    Revoke
                  </button>
                </>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {uploads.length > 0 ? (
        <ul className="ct-muted" style={{ margin: "0.45rem 0 0", paddingLeft: "1.1rem", fontSize: "0.86rem" }}>
          {uploads.slice(0, 8).map((row) => (
            <li key={row.job_id}>
              {row.email} uploaded {row.sku ? `SKU ${row.sku}` : "a lot"} · {row.status}
              {row.client_draft_id ? ` · draft ${row.client_draft_id.slice(0, 8)}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
