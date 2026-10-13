"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, apiUploadFiles } from "../../lib/api";
import { isClientRole, rememberAccountRole } from "../../lib/roles";

const MAX_PHOTOS = 40;

type Me = {
  email?: string;
  role?: string;
  assigned_sale_id?: string | null;
  assigned_sale_name?: string | null;
};

type JobRow = {
  id: string;
  status: string;
  error_message?: string | null;
  single_lot_manual_sku?: string | null;
  created_at?: string;
  progress_phase?: string | null;
  progress_done?: number;
  progress_total?: number;
};

export default function ClientUploadPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [sku, setSku] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [jobs, setJobs] = useState<JobRow[]>([]);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setReady(true);
      return;
    }
    void apiFetch("/auth/me", t)
      .then((data) => {
        const row = data as Me;
        rememberAccountRole(row.role);
        if (!isClientRole(row.role)) {
          router.replace("/dashboard");
          return;
        }
        setMe(row);
      })
      .catch(() => {
        localStorage.removeItem("token");
        setToken("");
        router.replace("/");
      })
      .finally(() => setReady(true));
  }, [router]);

  useEffect(() => {
    if (!token || !me?.assigned_sale_id) return;
    let cancelled = false;
    const load = async () => {
      try {
        const rows = (await apiFetch(
          `/jobs?sale_id=${encodeURIComponent(me.assigned_sale_id || "")}`,
          token
        )) as JobRow[];
        if (!cancelled) setJobs(rows);
      } catch {
        /* the upload form still works if the job list is briefly unavailable */
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token, me?.assigned_sale_id]);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function upload() {
    if (!token || !me?.assigned_sale_id || files.length === 0) return;
    if (files.length > MAX_PHOTOS) {
      setError(`CTBids allows at most ${MAX_PHOTOS} photos per listing.`);
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const form = new FormData();
      form.append("sale_id", me.assigned_sale_id);
      form.append("inherit_sku_from_subfolder", "false");
      form.append("single_lot_mode", "true");
      if (sku.trim()) form.append("single_lot_manual_sku", sku.trim());
      for (const file of files) form.append("files", file, file.name);
      const created = (await apiUploadFiles("/jobs/upload", token, form)) as JobRow;
      await apiFetch(`/jobs/${encodeURIComponent(created.id)}/run`, token, { method: "POST" });
      setMessage(
        files.length === 1
          ? "1 photo is uploading into this sale."
          : `${files.length} photos are uploading into this sale.`
      );
      setFiles([]);
      setSku("");
      const input = document.getElementById("client-photos") as HTMLInputElement | null;
      if (input) input.value = "";
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p className="ct-muted" style={{ margin: 0 }}>
            Loading…
          </p>
        </div>
      </main>
    );
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

  const saleName = me?.assigned_sale_name || "your sale";

  return (
    <main className="ct-shell">
      <div className="ct-panel" style={{ maxWidth: 720 }}>
        <div className="ct-row ct-row--gap-lg" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <p className="ct-muted" style={{ margin: 0, fontSize: "0.8rem" }}>
              Estate-EZ
            </p>
            <h1 className="ct-section-title" style={{ margin: "0.2rem 0 0" }}>
              Upload photos
            </h1>
          </div>
          <button type="button" className="ct-btn ct-btn--ghost" onClick={logout}>
            Log out
          </button>
        </div>
        {me ? (
          <>
            <p className="ct-lead" style={{ marginTop: "0.85rem" }}>
              Signed in as <strong>{me.email}</strong>. Photos go into <strong>{saleName}</strong>.
            </p>
            {!me.assigned_sale_id ? (
              <div className="ct-banner ct-banner--error">
                This login does not have a sale yet. Ask the account owner to assign one.
              </div>
            ) : (
              <>
                {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
                {message ? <div className="ct-banner ct-banner--success">{message}</div> : null}
                <label className="ct-label" htmlFor="client-sku">
                  Lot number (optional)
                </label>
                <input
                  id="client-sku"
                  className="ct-input"
                  value={sku}
                  onChange={(e) => setSku(e.target.value)}
                  placeholder="Tape number, if you have one"
                  disabled={busy}
                  style={{ maxWidth: 280, marginBottom: "0.85rem" }}
                />
                <label className="ct-label" htmlFor="client-photos">
                  Photos
                </label>
                <input
                  id="client-photos"
                  className="ct-input"
                  type="file"
                  accept="image/*"
                  multiple
                  disabled={busy}
                  onChange={(e) => setFiles(Array.from(e.target.files || []))}
                />
                <p className="ct-muted" style={{ marginTop: "0.45rem" }}>
                  {files.length === 0
                    ? `Choose up to ${MAX_PHOTOS} photos for one lot.`
                    : `${files.length} photo${files.length === 1 ? "" : "s"} selected.`}
                </p>
                <button
                  type="button"
                  className="ct-btn ct-btn--primary"
                  disabled={busy || files.length === 0}
                  onClick={() => void upload()}
                >
                  {busy ? "Uploading…" : "Upload photos"}
                </button>
              </>
            )}
            {jobs.length > 0 ? (
              <div style={{ marginTop: "1.25rem" }}>
                <h2 className="ct-section-title" style={{ fontSize: "1.05rem" }}>
                  Recent uploads
                </h2>
                <div className="ct-stack">
                  {jobs.slice(0, 8).map((job) => (
                    <div key={job.id} className="ct-card ct-card--padded">
                      <p style={{ margin: 0, fontWeight: 600 }}>
                        {job.single_lot_manual_sku || "Lot"} · {jobLabel(job.status)}
                      </p>
                      {job.progress_phase ? (
                        <p className="ct-muted" style={{ margin: "0.3rem 0 0", fontSize: "0.86rem" }}>
                          {job.progress_phase}
                          {job.progress_total ? ` (${job.progress_done || 0}/${job.progress_total})` : ""}
                        </p>
                      ) : null}
                      {job.error_message ? (
                        <p className="ct-muted" style={{ margin: "0.3rem 0 0", fontSize: "0.86rem" }}>
                          {job.error_message}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </main>
  );
}

function jobLabel(status: string): string {
  const value = (status || "").toLowerCase();
  if (value === "receiving") return "Uploading";
  if (value === "queued") return "Waiting to catalog";
  if (value === "processing") return "Cataloging";
  if (value === "completed") return "Cataloged";
  if (value === "failed") return "Needs another try";
  return status || "Uploaded";
}
