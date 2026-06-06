"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Camera, Loader2, Plus, Trash2 } from "lucide-react";
import { AppraisalPhotoThumb } from "../../components/AppraisalPhotoThumb";
import { SiteHeader } from "../../components/SiteHeader";
import { API_BASE, apiFetch, parseApiErrorMessage } from "../../lib/api";

const SALE_STORAGE_KEY = "ct_selected_sale_id";

type Sale = {
  id: string;
  name: string;
  closed_at: string | null;
};

type AppraisalResult = {
  summary: string;
  recommended_starting_price: number;
  price_low: number;
  price_high: number;
  high_value: boolean;
  high_value_rationale: string;
  listing_channel: string;
  listing_channel_rationale: string;
  recommended_ct_category: string;
  confidence: string;
  rationale: string;
  search_summary: string;
  items: Array<{
    line_item_id: number;
    title: string;
    brand?: string;
    estimated_low?: number;
    estimated_high?: number;
    notes?: string;
  }>;
};

type AppraisalCheck = {
  id: string;
  sale_id: string;
  status: string;
  scene_note: string | null;
  photo_count: number;
  result: AppraisalResult | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

type Health = {
  enable_appraiser?: boolean;
  appraiser_max_photos?: number;
};

function channelLabel(channel: string): string {
  switch (channel) {
    case "ct_bids":
      return "CT Bids (estate auction)";
    case "ebay":
      return "eBay / broader market";
    case "either":
      return "Either channel";
    default:
      return "Needs review";
  }
}

function formatMoney(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "—";
  return `$${n.toFixed(0)}`;
}

function isAuthFailureMessage(message: string): boolean {
  const m = message.toLowerCase();
  return m.includes("invalid authentication") || m.includes("401") || m.includes("unauthorized");
}

export default function AppraiserPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [sales, setSales] = useState<Sale[]>([]);
  const [selectedSaleId, setSelectedSaleId] = useState("");
  const [checks, setChecks] = useState<AppraisalCheck[]>([]);
  const [sceneNote, setSceneNote] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [checksLoading, setChecksLoading] = useState(false);
  const [submitBusy, setSubmitBusy] = useState(false);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const maxPhotos = health?.appraiser_max_photos ?? 3;
  const appraiserEnabled = health?.enable_appraiser === true;
  const selectedSale = useMemo(
    () => sales.find((s) => s.id === selectedSaleId) ?? null,
    [sales, selectedSaleId]
  );

  const hasInProgress = checks.some((c) => c.status === "queued" || c.status === "processing");

  const loadChecks = useCallback(
    async (t: string, saleId: string) => {
      if (!saleId) {
        setChecks([]);
        return;
      }
      setChecksLoading(true);
      try {
        const rows = (await apiFetch(`/appraiser/checks?sale_id=${encodeURIComponent(saleId)}`, t)) as AppraisalCheck[];
        setChecks(rows);
      } catch (e) {
        setError(parseApiErrorMessage(e instanceof Error ? e.message : "Could not load checks"));
      } finally {
        setChecksLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void (async () => {
      try {
        const [h, saleRows] = await Promise.all([
          apiFetch("/health", t) as Promise<Health>,
          apiFetch("/sales", t) as Promise<Sale[]>
        ]);
        setHealth(h);
        setSales(saleRows);
        const stored = localStorage.getItem(SALE_STORAGE_KEY) || "";
        const initial = stored && saleRows.some((s) => s.id === stored) ? stored : saleRows[0]?.id || "";
        setSelectedSaleId(initial);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Could not load appraiser";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          router.replace("/");
          return;
        }
        setError(parseApiErrorMessage(msg));
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

  useEffect(() => {
    if (!token || !selectedSaleId) return;
    localStorage.setItem(SALE_STORAGE_KEY, selectedSaleId);
    void loadChecks(token, selectedSaleId);
  }, [token, selectedSaleId, loadChecks]);

  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (!token || !selectedSaleId || !hasInProgress) return;
    pollRef.current = setInterval(() => {
      void loadChecks(token, selectedSaleId);
    }, 2500);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [token, selectedSaleId, hasInProgress, loadChecks]);

  useEffect(() => {
    const urls = pendingFiles.map((f) => URL.createObjectURL(f));
    setPreviewUrls(urls);
    return () => {
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [pendingFiles]);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  function onFilesPicked(list: FileList | null) {
    if (!list?.length) return;
    setPendingFiles((prev) => {
      const merged = [...prev, ...Array.from(list)].slice(0, maxPhotos);
      return merged;
    });
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function removePending(index: number) {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function submitCheck() {
    const t = token;
    if (!t || !selectedSaleId || submitBusy) return;
    if (!pendingFiles.length) {
      setBanner("Add at least one photo.");
      return;
    }
    setSubmitBusy(true);
    setBanner("");
    setError("");
    try {
      const fd = new FormData();
      fd.append("sale_id", selectedSaleId);
      fd.append("scene_note", sceneNote.trim());
      for (const f of pendingFiles) {
        fd.append("files", f);
      }
      const res = await fetch(`${API_BASE}/appraiser/checks`, {
        method: "POST",
        headers: { Authorization: `Bearer ${t}` },
        body: fd
      });
      const raw = await res.text();
      if (!res.ok) {
        throw new Error(parseApiErrorMessage(raw, "Appraisal failed"));
      }
      setPendingFiles([]);
      setSceneNote("");
      setBanner("Appraisal started — results appear below when ready.");
      await loadChecks(t, selectedSaleId);
    } catch (e) {
      setError(parseApiErrorMessage(e instanceof Error ? e.message : "Appraisal failed"));
    } finally {
      setSubmitBusy(false);
    }
  }

  if (loading) {
    return (
      <main className="ct-shell">
        <p className="ct-subtitle">Loading appraiser…</p>
      </main>
    );
  }

  if (!token) {
    router.replace("/");
    return null;
  }

  return (
    <main className="ct-shell">
      <SiteHeader
        actions={
          <>
            <Link href="/account" className="ct-btn ct-btn--ghost">
              Account
            </Link>
            <button type="button" className="ct-btn ct-btn--ghost" onClick={logout}>
              Log out
            </button>
          </>
        }
      />

      <header className="ct-page-header">
        <div>
          <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
            Appraiser
          </h1>
          <p className="ct-subtitle" style={{ margin: 0, maxWidth: "40rem" }}>
            Job-site spot checks — snap up to {maxPhotos} photos, get price range, high-value flag, and CT Bids vs eBay
            guidance. Separate from catalog lots.
          </p>
        </div>
      </header>

      {!appraiserEnabled && (
        <section className="ct-banner ct-banner--warning" style={{ marginBottom: "1rem" }}>
          Appraiser mode is not enabled on this server. Set <code>ENABLE_APPRAISER=1</code> on the backend to use it.
        </section>
      )}

      {error && (
        <section className="ct-banner ct-banner--error" style={{ marginBottom: "1rem" }}>
          {error}
        </section>
      )}
      {banner && (
        <section className="ct-banner ct-banner--success" style={{ marginBottom: "1rem" }}>
          {banner}
        </section>
      )}

      <section className="ct-card ct-card--padded" style={{ marginBottom: "1.25rem" }}>
        <label className="ct-label" htmlFor="appraiser-sale">
          Sale
        </label>
        <select
          id="appraiser-sale"
          className="ct-input"
          value={selectedSaleId}
          onChange={(e) => setSelectedSaleId(e.target.value)}
          disabled={!sales.length}
        >
          {sales.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
              {s.closed_at ? " (closed)" : ""}
            </option>
          ))}
        </select>
        {selectedSale && (
          <p className="ct-subtitle" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
            Checks for this sale only — not mixed with catalog lots.
          </p>
        )}
      </section>

      {appraiserEnabled && (
        <section className="ct-card ct-card--padded" style={{ marginBottom: "1.25rem" }}>
          <h2 className="ct-section-title">New check</h2>
          <label className="ct-label" htmlFor="appraiser-note">
            Scene note (optional)
          </label>
          <input
            id="appraiser-note"
            className="ct-input"
            placeholder='e.g. "Kitchen counter box" or "Master bedroom dresser"'
            value={sceneNote}
            onChange={(e) => setSceneNote(e.target.value)}
            disabled={submitBusy}
          />

          <div style={{ marginTop: "1rem" }}>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              multiple
              hidden
              onChange={(e) => onFilesPicked(e.target.files)}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", alignItems: "center" }}>
              {previewUrls.map((url, i) => (
                <div key={url} style={{ position: "relative" }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={url}
                    alt={`Pending ${i + 1}`}
                    style={{ width: 96, height: 96, objectFit: "cover", borderRadius: 8, border: "1px solid var(--ct-border)" }}
                  />
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Remove photo"
                    style={{ position: "absolute", top: 2, right: 2, padding: "0.15rem" }}
                    onClick={() => removePending(i)}
                    disabled={submitBusy}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              {pendingFiles.length < maxPhotos && (
                <button
                  type="button"
                  className="ct-btn ct-btn--secondary"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={submitBusy}
                >
                  <Camera size={16} style={{ marginRight: "0.35rem" }} />
                  {pendingFiles.length ? "Add photo" : "Take / add photos"}
                </button>
              )}
            </div>
            <p className="ct-subtitle" style={{ marginTop: "0.5rem" }}>
              {pendingFiles.length} / {maxPhotos} photos — multiple items in one frame is OK.
            </p>
          </div>

          <button
            type="button"
            className="ct-btn ct-btn--primary"
            style={{ marginTop: "1rem" }}
            disabled={submitBusy || !pendingFiles.length || !selectedSaleId}
            onClick={() => void submitCheck()}
          >
            {submitBusy ? (
              <>
                <Loader2 size={16} style={{ marginRight: "0.35rem", verticalAlign: "middle" }} />
                Running appraisal…
              </>
            ) : (
              <>
                <Plus size={16} style={{ marginRight: "0.35rem" }} />
                Run appraisal
              </>
            )}
          </button>
        </section>
      )}

      <section className="ct-card ct-card--padded">
        <h2 className="ct-section-title">Check history</h2>
        {checksLoading && !checks.length ? (
          <p className="ct-subtitle">Loading…</p>
        ) : !checks.length ? (
          <p className="ct-subtitle">No appraisals yet for this sale.</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "1rem" }}>
            {checks.map((check) => (
              <li key={check.id} className="ct-card ct-card--padded" style={{ background: "var(--ct-surface-muted)" }}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", justifyContent: "space-between" }}>
                  <div>
                    <strong>{new Date(check.created_at).toLocaleString()}</strong>
                    {check.scene_note && (
                      <p className="ct-subtitle" style={{ margin: "0.25rem 0 0" }}>
                        {check.scene_note}
                      </p>
                    )}
                  </div>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      fontWeight: 600,
                      padding: "0.2rem 0.55rem",
                      borderRadius: 999,
                      background: "var(--ct-surface)",
                      border: "1px solid var(--ct-border)"
                    }}
                  >
                    {check.status}
                  </span>
                </div>

                <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem", flexWrap: "wrap" }}>
                  {Array.from({ length: check.photo_count }).map((_, i) => (
                    <AppraisalPhotoThumb key={i} checkId={check.id} photoIndex={i} token={token} />
                  ))}
                </div>

                {(check.status === "queued" || check.status === "processing") && (
                  <p className="ct-subtitle" style={{ marginTop: "0.75rem", display: "flex", alignItems: "center", gap: "0.35rem" }}>
                    <Loader2 size={14} />
                    Pro + search running…
                  </p>
                )}

                {check.status === "failed" && check.error_message && (
                  <p className="ct-banner ct-banner--error" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
                    {check.error_message}
                  </p>
                )}

                {check.result && (
                  <div style={{ marginTop: "0.85rem" }}>
                    <p style={{ margin: "0 0 0.5rem" }}>{check.result.summary}</p>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginBottom: "0.5rem" }}>
                      <span
                        style={{
                          fontSize: "0.78rem",
                          fontWeight: 600,
                          padding: "0.25rem 0.6rem",
                          borderRadius: 999,
                          background: "color-mix(in srgb, var(--ct-primary) 14%, transparent)",
                          border: "1px solid var(--ct-border)"
                        }}
                      >
                        Start {formatMoney(check.result.recommended_starting_price)}
                      </span>
                      <span
                        style={{
                          fontSize: "0.78rem",
                          padding: "0.25rem 0.6rem",
                          borderRadius: 999,
                          border: "1px solid var(--ct-border)"
                        }}
                      >
                        Range {formatMoney(check.result.price_low)} – {formatMoney(check.result.price_high)}
                      </span>
                      {check.result.high_value && (
                        <span
                          style={{
                            fontSize: "0.78rem",
                            fontWeight: 600,
                            padding: "0.25rem 0.6rem",
                            borderRadius: 999,
                            background: "var(--ct-warning-bg, rgba(255, 180, 0, 0.12))",
                            border: "1px solid var(--ct-border)"
                          }}
                        >
                          High value
                        </span>
                      )}
                      <span
                        style={{
                          fontSize: "0.78rem",
                          padding: "0.25rem 0.6rem",
                          borderRadius: 999,
                          border: "1px solid var(--ct-border)"
                        }}
                      >
                        {channelLabel(check.result.listing_channel)}
                      </span>
                      {check.result.recommended_ct_category && (
                        <span
                          style={{
                            fontSize: "0.78rem",
                            padding: "0.25rem 0.6rem",
                            borderRadius: 999,
                            border: "1px solid var(--ct-border)"
                          }}
                        >
                          {check.result.recommended_ct_category}
                        </span>
                      )}
                    </div>
                    {check.result.listing_channel_rationale && (
                      <p className="ct-subtitle" style={{ margin: "0 0 0.35rem" }}>
                        Channel: {check.result.listing_channel_rationale}
                      </p>
                    )}
                    {check.result.high_value_rationale && check.result.high_value && (
                      <p className="ct-subtitle" style={{ margin: "0 0 0.35rem" }}>
                        High value: {check.result.high_value_rationale}
                      </p>
                    )}
                    {check.result.rationale && (
                      <p className="ct-subtitle" style={{ margin: 0 }}>
                        {check.result.rationale}
                      </p>
                    )}
                    {check.result.items?.length > 1 && (
                      <ul style={{ marginTop: "0.5rem", paddingLeft: "1.1rem" }}>
                        {check.result.items.map((item) => (
                          <li key={item.line_item_id} className="ct-subtitle">
                            {item.title}
                            {item.brand ? ` (${item.brand})` : ""} — {formatMoney(item.estimated_low ?? 0)}–
                            {formatMoney(item.estimated_high ?? 0)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
