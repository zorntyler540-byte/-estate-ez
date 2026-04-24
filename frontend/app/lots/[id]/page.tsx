"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { LotPhotoThumb } from "../../../components/LotPhotoThumb";
import { LotThumbnail } from "../../../components/LotThumbnail";
import { API_BASE, apiFetch } from "../../../lib/api";

type LotPhoto = {
  id: string;
  lot_id: string;
  original_name: string;
  created_at: string;
};

type Lot = {
  id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  professional_description: string;
  specification_lines: string[];
  brands: string[];
  labels_and_tags: string[];
  starting_price: number | null;
  condition_text: string;
  is_completed: boolean;
  photo_count: number;
  has_thumbnail: boolean;
  sku_needs_review: boolean;
  many_photos_review: boolean;
};

export default function LotDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const [token, setToken] = useState("");
  const [lot, setLot] = useState<Lot | null>(null);
  const [catalogPhotos, setCatalogPhotos] = useState<LotPhoto[]>([]);
  const [photosError, setPhotosError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [viewerUrl, setViewerUrl] = useState<string>("");
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerError, setViewerError] = useState("");

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t || !params.id) return;
    let cancelled = false;
    setPhotosError("");
    void (async () => {
      try {
        const d = await apiFetch(`/lots/${params.id}`, t);
        if (cancelled) return;
        setLot(d);
        try {
          const photos = (await apiFetch(`/lots/${params.id}/photos`, t)) as LotPhoto[];
          if (cancelled) return;
          setCatalogPhotos(Array.isArray(photos) ? photos : []);
        } catch {
          if (!cancelled) {
            setCatalogPhotos([]);
            setPhotosError("Could not load the photo list for this lot.");
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Load failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params.id]);

  useEffect(() => {
    return () => {
      if (viewerUrl) URL.revokeObjectURL(viewerUrl);
    };
  }, [viewerUrl]);

  useEffect(() => {
    if (typeof window === "undefined" || !lot) return;
    if (window.location.hash !== "#listing-details") return;
    requestAnimationFrame(() => {
      document.getElementById("listing-details")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [lot?.id]);

  async function loadViewerPhoto(index: number) {
    if (!lot || !token) return;
    const photo = catalogPhotos[index];
    if (!photo) return;
    setViewerIndex(index);
    setViewerLoading(true);
    setViewerError("");
    try {
      const res = await fetch(
        `${API_BASE}/lots/${encodeURIComponent(lot.id)}/photos/${encodeURIComponent(photo.id)}/download`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store"
        }
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Photo download failed (${res.status})`);
      }
      const blob = await res.blob();
      if (viewerUrl) URL.revokeObjectURL(viewerUrl);
      setViewerUrl(URL.createObjectURL(blob));
    } catch (e) {
      setViewerError(e instanceof Error ? e.message : "Could not load full-size photo.");
      setViewerUrl("");
    } finally {
      setViewerLoading(false);
    }
  }

  function closeViewer() {
    setViewerIndex(null);
    setViewerLoading(false);
    setViewerError("");
    if (viewerUrl) URL.revokeObjectURL(viewerUrl);
    setViewerUrl("");
  }

  function showPrevPhoto() {
    if (viewerIndex === null || viewerIndex <= 0) return;
    void loadViewerPhoto(viewerIndex - 1);
  }

  function showNextPhoto() {
    if (viewerIndex === null || viewerIndex >= catalogPhotos.length - 1) return;
    void loadViewerPhoto(viewerIndex + 1);
  }

  async function save() {
    if (!lot) return;
    setSaving(true);
    setError("");
    try {
      const updated = await apiFetch(`/lots/${lot.id}`, token, {
        method: "PATCH",
        body: JSON.stringify({
          sku: lot.sku,
          auction_title: lot.auction_title,
          professional_description: lot.professional_description,
          specification_lines: lot.specification_lines,
          brands: lot.brands,
          labels_and_tags: lot.labels_and_tags,
          starting_price: lot.starting_price,
          condition_text: lot.condition_text,
          is_completed: lot.is_completed
        })
      });
      setLot(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function removeLot() {
    if (!lot) return;
    if (!window.confirm("Delete this lot permanently?")) return;
    setError("");
    try {
      await apiFetch(`/lots/${lot.id}`, token, { method: "DELETE" });
      router.replace("/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  if (!token) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Please sign in first.</p>
        </div>
      </main>
    );
  }
  if (!lot) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Loading lot…</p>
        </div>
      </main>
    );
  }

  return (
    <main className="ct-shell" style={{ maxWidth: 960 }}>
      <p style={{ margin: "0 0 0.75rem" }}>
        <Link href="/dashboard">← Back to dashboard</Link>
      </p>
      <header className="ct-page-header" style={{ borderBottom: "none", paddingBottom: 0, marginBottom: "0.5rem" }}>
        <h1 className="ct-page-title">Lot {lot.sku || "(no SKU)"}</h1>
      </header>
      {lot.sku_needs_review ? (
        <div className="ct-banner ct-banner--warning" style={{ marginBottom: "0.85rem" }}>
          <strong>SKU review.</strong> The model was unsure or did not read purple-tape digits clearly. Confirm the
          handwritten lot number below matches your photos before posting with the extension.
        </div>
      ) : null}
      {lot.many_photos_review ? (
        <div className="ct-banner ct-banner--warning" style={{ marginBottom: "0.85rem" }}>
          <strong>Photo count looks unusual</strong> for this sale — this listing has many more images than your other
          lots. Check that photos from a different lot were not merged here (wrong tape SKU early in the job can cause
          that). Re-upload as separate jobs if needed.
        </div>
      ) : null}
      <section className="ct-card ct-card--padded" style={{ marginBottom: "1rem" }}>
        <h2 className="ct-section-title" style={{ margin: "0 0 0.5rem", fontSize: "1.05rem" }}>
          Catalog photos
          {catalogPhotos.length ? (
            <span className="ct-muted" style={{ fontWeight: 500, marginLeft: "0.35rem" }}>
              ({catalogPhotos.length})
            </span>
          ) : null}
        </h2>
        <p className="ct-muted" style={{ margin: "0 0 1rem", fontSize: "0.88rem", maxWidth: "40rem" }}>
          Same order as{" "}
          <strong style={{ color: "var(--ct-text-muted)" }}>Upload lot photos</strong> in the browser extension —
          first row left‑to‑right, then the next row.
        </p>
        {photosError ? (
          <div className="ct-banner ct-banner--error" style={{ marginBottom: "0.75rem" }}>
            {photosError}
          </div>
        ) : null}
        {catalogPhotos.length === 0 && !photosError ? (
          <div className="ct-lot-hero" style={{ marginBottom: 0 }}>
            <LotThumbnail lotId={lot.id} token={token} hasThumbnail={lot.has_thumbnail} size={140} maxPx={400} />
            <p className="ct-lot-hero-note" style={{ margin: 0 }}>
              No linked photos yet. Thumbnail shows the first image only when the API reports one; run a catalog job
              to attach frames to this lot.
            </p>
          </div>
        ) : (
          <div
            className="ct-lot-photo-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
              gap: "0.75rem"
            }}
          >
            {catalogPhotos.map((p, index) => (
              <figure key={p.id} style={{ margin: 0, display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                <div style={{ position: "relative" }}>
                  <span
                    className="ct-muted"
                    style={{
                      position: "absolute",
                      top: 6,
                      left: 6,
                      zIndex: 1,
                      fontSize: "0.72rem",
                      fontWeight: 700,
                      background: "rgba(12, 15, 18, 0.82)",
                      color: "var(--ct-primary)",
                      padding: "0.12rem 0.4rem",
                      borderRadius: 6,
                      border: "1px solid var(--ct-border)"
                    }}
                  >
                    {index + 1}
                  </span>
                  <button
                    type="button"
                    onClick={() => void loadViewerPhoto(index)}
                    title={`Open ${p.original_name}`}
                    style={{
                      all: "unset",
                      cursor: "zoom-in",
                      display: "block",
                      width: "100%"
                    }}
                  >
                    <LotPhotoThumb
                      lotId={lot.id}
                      photoId={p.id}
                      token={token}
                      label={p.original_name}
                      maxPx={360}
                      minHeight={120}
                    />
                  </button>
                </div>
                <figcaption
                  className="ct-muted"
                  style={{
                    fontSize: "0.72rem",
                    lineHeight: 1.35,
                    wordBreak: "break-word",
                    margin: 0
                  }}
                  title={p.original_name}
                >
                  {p.original_name}
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </section>
      <div id="listing-details" className="ct-card ct-card--padded ct-stack" style={{ scrollMarginTop: "0.75rem" }}>
        <h2 className="ct-section-title" style={{ margin: "0 0 0.65rem", fontSize: "1.05rem" }}>
          Listing details
        </h2>
        <div>
          <label className="ct-label" htmlFor="lot-sku">
            Estate lot SKU (tape digits)
          </label>
          <input
            id="lot-sku"
            className="ct-input"
            inputMode="numeric"
            autoComplete="off"
            placeholder="e.g. 034"
            value={lot.sku ?? ""}
            onChange={(e) => setLot({ ...lot, sku: e.target.value.trim() || null })}
          />
          <p className="ct-muted" style={{ margin: "0.35rem 0 0", fontSize: "0.82rem" }}>
            Save after correcting. This clears the “review SKU” highlight on the dashboard.
          </p>
        </div>
        <div>
          <label className="ct-label" htmlFor="auction-title">
            Auction title
          </label>
          <input
            id="auction-title"
            className="ct-input"
            value={lot.auction_title}
            onChange={(e) => setLot({ ...lot, auction_title: e.target.value })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="pro-desc">
            Professional description
          </label>
          <textarea
            id="pro-desc"
            className="ct-textarea"
            style={{ minHeight: 160 }}
            value={lot.professional_description}
            onChange={(e) => setLot({ ...lot, professional_description: e.target.value })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="spec-lines">
            Specification lines (one per line)
          </label>
          <textarea
            id="spec-lines"
            className="ct-textarea"
            value={lot.specification_lines.join("\n")}
            onChange={(e) => setLot({ ...lot, specification_lines: e.target.value.split("\n").filter(Boolean) })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="brands">
            Brands (comma separated)
          </label>
          <input
            id="brands"
            className="ct-input"
            value={lot.brands.join(", ")}
            onChange={(e) => setLot({ ...lot, brands: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="tags">
            Sizes / tags (comma separated)
          </label>
          <input
            id="tags"
            className="ct-input"
            value={lot.labels_and_tags.join(", ")}
            onChange={(e) =>
              setLot({ ...lot, labels_and_tags: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })
            }
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="price">
            Starting price
          </label>
          <input
            id="price"
            className="ct-input"
            type="number"
            value={lot.starting_price ?? ""}
            onChange={(e) => setLot({ ...lot, starting_price: e.target.value ? Number(e.target.value) : null })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="condition">
            Condition
          </label>
          <input
            id="condition"
            className="ct-input"
            value={lot.condition_text}
            onChange={(e) => setLot({ ...lot, condition_text: e.target.value })}
          />
        </div>
        <div className="ct-form-actions">
          <label className="ct-row" style={{ gap: "0.45rem" }}>
            <input
              type="checkbox"
              checked={lot.is_completed}
              onChange={(e) => setLot({ ...lot, is_completed: e.target.checked })}
            />
            <span className="ct-muted" style={{ fontSize: "0.9rem" }}>
              Mark this lot as completed
            </span>
          </label>
        </div>
        <div className="ct-form-actions">
          <button type="button" className="ct-btn ct-btn--primary" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="ct-btn ct-btn--danger" onClick={() => void removeLot()}>
            Delete lot
          </button>
        </div>
        {error ? <div className="ct-banner ct-banner--error" style={{ marginBottom: 0 }}>{error}</div> : null}
      </div>
      {viewerIndex !== null ? (
        <div
          onClick={closeViewer}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.85)",
            zIndex: 9999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "1rem"
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "min(96vw, 1200px)",
              maxHeight: "92vh",
              display: "flex",
              flexDirection: "column",
              gap: "0.75rem"
            }}
          >
            <div className="ct-row ct-row--gap-lg" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <div className="ct-muted" style={{ fontSize: "0.9rem" }}>
                Photo {viewerIndex + 1}/{catalogPhotos.length}
                {catalogPhotos[viewerIndex] ? ` · ${catalogPhotos[viewerIndex]!.original_name}` : ""}
              </div>
              <button type="button" className="ct-btn ct-btn--ghost" onClick={closeViewer}>
                Close
              </button>
            </div>
            <div
              style={{
                minHeight: "50vh",
                border: "1px solid var(--ct-border)",
                borderRadius: "var(--ct-radius)",
                background: "#0c0f12",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                overflow: "hidden"
              }}
            >
              {viewerLoading ? (
                <div className="ct-muted">Loading full-size photo…</div>
              ) : viewerError ? (
                <div className="ct-banner ct-banner--error" style={{ margin: "1rem" }}>
                  {viewerError}
                </div>
              ) : viewerUrl ? (
                <img
                  src={viewerUrl}
                  alt={catalogPhotos[viewerIndex]?.original_name || "Lot photo"}
                  style={{
                    maxWidth: "100%",
                    maxHeight: "78vh",
                    objectFit: "contain",
                    display: "block"
                  }}
                />
              ) : null}
            </div>
            <div className="ct-row ct-row--gap-lg" style={{ justifyContent: "space-between" }}>
              <button
                type="button"
                className="ct-btn"
                onClick={showPrevPhoto}
                disabled={viewerLoading || viewerIndex <= 0}
              >
                Previous
              </button>
              <button
                type="button"
                className="ct-btn"
                onClick={showNextPhoto}
                disabled={viewerLoading || viewerIndex >= catalogPhotos.length - 1}
              >
                Next
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

