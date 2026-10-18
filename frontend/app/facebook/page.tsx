"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { LotThumbnail } from "../../components/LotThumbnail";
import { apiFetch } from "../../lib/api";

const SALE_STORAGE_KEY = "ct_selected_sale_id";

type Sale = {
  id: string;
  name: string;
  closed_at: string | null;
};

type Lot = {
  id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  professional_description: string;
  starting_price: number | null;
  condition_text: string;
  photo_count: number;
  has_thumbnail: boolean;
  listing_channel?: string | null;
  created_at: string;
};

function readStoredSaleId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(SALE_STORAGE_KEY) || "";
}

function formatPrice(price: number | null): string {
  if (price == null || Number.isNaN(price)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(price);
}

function marketplaceText(lot: Lot): string {
  const title = lot.auction_title?.trim() || "Furniture item";
  const lines = [title];
  if (lot.starting_price != null && !Number.isNaN(lot.starting_price)) {
    lines.push(`$${lot.starting_price.toFixed(2)}`);
  }
  const desc = lot.professional_description?.trim();
  if (desc) {
    lines.push("");
    lines.push(desc);
  }
  const condition = lot.condition_text?.trim();
  if (condition) {
    lines.push("");
    lines.push(`Condition: ${condition}`);
  }
  return lines.join("\n");
}

export default function FacebookPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [sales, setSales] = useState<Sale[]>([]);
  const [selectedSaleId, setSelectedSaleId] = useState("");
  const [lots, setLots] = useState<Lot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      router.replace("/");
      return;
    }
    setSelectedSaleId(readStoredSaleId());
    void apiFetch("/sales", t)
      .then((rows) => {
        const list = (rows as Sale[]).filter((s) => !s.closed_at);
        setSales(list);
        const stored = readStoredSaleId();
        if (stored && list.some((s) => s.id === stored)) {
          setSelectedSaleId(stored);
        } else if (list[0]) {
          setSelectedSaleId(list[0].id);
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load sales"));
  }, [router]);

  const loadLots = useCallback(async () => {
    if (!token || !selectedSaleId) {
      setLots([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      localStorage.setItem(SALE_STORAGE_KEY, selectedSaleId);
      const rows = (await apiFetch(
        `/lots?sale_id=${encodeURIComponent(selectedSaleId)}&listing_channel=facebook&sort=created_desc&limit=500`,
        token
      )) as Lot[];
      setLots(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load Facebook items");
      setLots([]);
    } finally {
      setLoading(false);
    }
  }, [token, selectedSaleId]);

  useEffect(() => {
    void loadLots();
  }, [loadLots]);

  const selectedSale = useMemo(
    () => sales.find((s) => s.id === selectedSaleId) ?? null,
    [sales, selectedSaleId]
  );

  async function copyListing(lot: Lot) {
    const text = marketplaceText(lot);
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus(`Copied “${lot.auction_title?.trim() || "listing"}”`);
      window.setTimeout(() => setCopyStatus(""), 2500);
    } catch {
      setError("Could not copy to clipboard");
    }
  }

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

  const saleTopBar = (
    <label className="ct-app-topbar__sale">
      <span className="ct-app-topbar__sale-label">Sale</span>
      <select
        className="ct-input"
        value={selectedSaleId}
        onChange={(e) => setSelectedSaleId(e.target.value)}
        aria-label="Sale for Facebook items"
      >
        {!sales.length ? <option value="">No open sales</option> : null}
        {sales.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <AppShell
      topBar={saleTopBar}
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
      <div className="ct-dashboard" style={{ maxWidth: 1100 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Facebook Marketplace
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Furniture captured for Marketplace only — separate from CT Bids / Sales dashboard
              and from Facebook Page posts (lot details, dashboard selection, or the Push queue).
              {selectedSale ? ` Showing items for ${selectedSale.name}.` : ""}
            </p>
          </div>
          <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void loadLots()} disabled={loading}>
            Refresh
          </button>
        </header>

        {error ? <p className="ct-error">{error}</p> : null}
        {copyStatus ? <p className="ct-muted" style={{ marginTop: 0 }}>{copyStatus}</p> : null}

        <section className="ct-card ct-card--padded">
          {loading && !lots.length ? (
            <p className="ct-muted" style={{ margin: 0 }}>
              Loading Facebook items…
            </p>
          ) : !lots.length ? (
            <p className="ct-muted" style={{ margin: 0 }}>
              No Facebook items in this sale yet. Capture them from the mobile app’s Facebook tab.
            </p>
          ) : (
            <div className="ct-table-wrap">
              <table className="ct-table">
                <thead>
                  <tr>
                    <th>Photo</th>
                    <th>Title</th>
                    <th>Price</th>
                    <th>Condition</th>
                    <th>Photos</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {lots.map((lot) => (
                    <tr key={lot.id}>
                      <td className="ct-table__photo">
                        <LotThumbnail
                          lotId={lot.id}
                          token={token}
                          hasThumbnail={lot.has_thumbnail}
                          size={56}
                        />
                      </td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{lot.auction_title?.trim() || "Untitled"}</div>
                        {lot.professional_description?.trim() ? (
                          <div className="ct-muted" style={{ fontSize: "0.85rem", marginTop: 4 }}>
                            {lot.professional_description.trim().slice(0, 120)}
                            {lot.professional_description.trim().length > 120 ? "…" : ""}
                          </div>
                        ) : null}
                      </td>
                      <td>{formatPrice(lot.starting_price)}</td>
                      <td>{lot.condition_text?.trim() || "—"}</td>
                      <td>{lot.photo_count}</td>
                      <td>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
                          <Link href={`/lots/${lot.id}`} className="ct-btn ct-btn--ghost">
                            Details
                          </Link>
                          <button type="button" className="ct-btn ct-btn--primary" onClick={() => void copyListing(lot)}>
                            Copy listing
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </AppShell>
  );
}
