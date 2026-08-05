"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { apiFetch, parseApiErrorMessage } from "../../lib/api";

const SALE_STORAGE_KEY = "ct_selected_sale_id";

type Sale = {
  id: string;
  name: string;
  closed_at: string | null;
  ctseller_sale_id?: number | null;
};

type InvoiceRow = {
  id: number;
  invoice_number: string | null;
  sale_title: string | null;
  sale_id: number | null;
  location_title: string | null;
  invoice_amount: number | null;
  invoice_status: string | null;
  type: string | null;
  buyer_id: number | null;
  created_date: string | null;
};

type InvoiceDetail = {
  id: number;
  invoice_number: string | null;
  invoice_amount: number | null;
  pending_amount: number | null;
  invoice_status: string | null;
  type: string | null;
  sale_id: number | null;
  buyer_id: number | null;
  created_date: string | null;
  receipt_method: string | null;
  wants_shipping: boolean | null;
  shipping_vendor: string | null;
  shipment_status: string | null;
  sale_uses_shipping_saint: boolean | null;
  master_invoice_id: number | null;
  child_invoice_count: number;
  pending_fulfillment_count: number;
  shipping_saint_manage_url: string;
};

function formatMoney(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

function readStoredSaleId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(SALE_STORAGE_KEY) || "";
}

export default function ShippingPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [sales, setSales] = useState<Sale[]>([]);
  const [selectedSaleId, setSelectedSaleId] = useState("");
  const [invoices, setInvoices] = useState<InvoiceRow[]>([]);
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState<boolean | null>(null);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      router.replace("/");
      return;
    }
    setSelectedSaleId(readStoredSaleId());
    void Promise.all([
      apiFetch("/sales", t) as Promise<Sale[]>,
      apiFetch("/integrations/ctseller", t) as Promise<{ connected: boolean }>
    ])
      .then(([saleRows, ct]) => {
        const list = (saleRows || []).filter((s) => !s.closed_at);
        setSales(list);
        setConnected(Boolean(ct?.connected));
        const stored = readStoredSaleId();
        if (stored && list.some((s) => s.id === stored)) setSelectedSaleId(stored);
        else if (list[0]) setSelectedSaleId(list[0].id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load account data"));
  }, [router]);

  const selectedSale = useMemo(
    () => sales.find((s) => s.id === selectedSaleId) ?? null,
    [sales, selectedSaleId]
  );

  const loadInvoices = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError("");
    setDetail(null);
    try {
      if (selectedSaleId) localStorage.setItem(SALE_STORAGE_KEY, selectedSaleId);
      const ctSaleId = selectedSale?.ctseller_sale_id;
      const q =
        ctSaleId != null && Number(ctSaleId) > 0
          ? `?sale_id=${encodeURIComponent(String(ctSaleId))}&limit=100`
          : "?limit=100";
      const rows = (await apiFetch(`/integrations/ctseller/invoices${q}`, token)) as InvoiceRow[];
      setInvoices(rows);
    } catch (e) {
      setInvoices([]);
      setError(e instanceof Error ? e.message : "Could not load CT invoices");
    } finally {
      setLoading(false);
    }
  }, [token, selectedSaleId, selectedSale?.ctseller_sale_id]);

  async function openDetail(id: number) {
    if (!token) return;
    setDetailBusy(true);
    setError("");
    try {
      const row = (await apiFetch(
        `/integrations/ctseller/invoices/${encodeURIComponent(String(id))}`,
        token
      )) as InvoiceDetail;
      setDetail(row);
    } catch (e) {
      setDetail(null);
      setError(
        e instanceof Error
          ? parseApiErrorMessage(e.message, e.message)
          : "Could not load invoice detail"
      );
    } finally {
      setDetailBusy(false);
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
        aria-label="Sale for shipping triage"
      >
        {!sales.length ? <option value="">No open sales</option> : null}
        {sales.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
            {s.ctseller_sale_id ? "" : " (no CT map)"}
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
              Shipping
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Post-sale triage: pull CT Seller invoices after buyers pay the lot invoice, then hand off
              packing/shipping billing (often Shipping Saint).
            </p>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              onClick={() => void loadInvoices()}
              disabled={loading || connected === false}
            >
              {loading ? "Pulling…" : "Pull CT invoices"}
            </button>
            <a
              className="ct-btn ct-btn--ghost"
              href="https://manage.shippingsaint.com/"
              target="_blank"
              rel="noreferrer"
            >
              Open Shipping Saint
            </a>
          </div>
        </header>

        {connected === false ? (
          <p className="ct-error">
            Connect CT Seller under <Link href="/account">Settings</Link> to pull invoices.
          </p>
        ) : null}
        {selectedSale && !selectedSale.ctseller_sale_id ? (
          <p className="ct-muted">
            This Estate-EZ sale is not mapped to a CT Seller auction yet — pull will show recent invoices
            across your CT location (not filtered to one sale). Map the auction from the Sales dashboard
            for a tighter list.
          </p>
        ) : null}
        {error ? <p className="ct-error">{error}</p> : null}

        <section className="ct-card ct-card--padded" style={{ marginBottom: "1rem" }}>
          {!invoices.length && !loading ? (
            <p className="ct-muted" style={{ margin: 0 }}>
              No invoices loaded yet. After a sale closes and buyers pay on CTBids, pull invoices here to
              see who still needs a shipping invoice.
            </p>
          ) : (
            <div className="ct-table-wrap">
              <table className="ct-table">
                <thead>
                  <tr>
                    <th>Invoice</th>
                    <th>Sale</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>Type</th>
                    <th>Created</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {invoices.map((inv) => (
                    <tr key={inv.id}>
                      <td style={{ fontWeight: 600 }}>{inv.invoice_number || `#${inv.id}`}</td>
                      <td>{inv.sale_title || inv.sale_id || "—"}</td>
                      <td>{formatMoney(inv.invoice_amount)}</td>
                      <td>{inv.invoice_status || "—"}</td>
                      <td>{inv.type || "—"}</td>
                      <td className="ct-muted" style={{ fontSize: "0.85rem" }}>
                        {inv.created_date || "—"}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="ct-btn ct-btn--ghost"
                          disabled={detailBusy}
                          onClick={() => void openDetail(inv.id)}
                        >
                          Inspect
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {detail ? (
          <section className="ct-card ct-card--padded">
            <h2 className="ct-page-title" style={{ fontSize: "1.15rem", marginTop: 0 }}>
              Invoice {detail.invoice_number || detail.id}
            </h2>
            <dl
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(140px, 180px) 1fr",
                gap: "0.45rem 1rem",
                margin: 0
              }}
            >
              <dt className="ct-muted">Lot invoice status</dt>
              <dd style={{ margin: 0 }}>{detail.invoice_status || "—"}</dd>
              <dt className="ct-muted">Amount / pending</dt>
              <dd style={{ margin: 0 }}>
                {formatMoney(detail.invoice_amount)} · pending {formatMoney(detail.pending_amount)}
              </dd>
              <dt className="ct-muted">Wants shipping</dt>
              <dd style={{ margin: 0 }}>
                {detail.wants_shipping == null ? "Unknown" : detail.wants_shipping ? "Yes" : "No"}
                {detail.receipt_method ? (
                  <span className="ct-muted" style={{ display: "block", fontSize: "0.85rem" }}>
                    {detail.receipt_method}
                  </span>
                ) : null}
              </dd>
              <dt className="ct-muted">Shipping vendor</dt>
              <dd style={{ margin: 0 }}>{detail.shipping_vendor || "—"}</dd>
              <dt className="ct-muted">Shipment status</dt>
              <dd style={{ margin: 0 }}>{detail.shipment_status || "—"}</dd>
              <dt className="ct-muted">Shipping Saint on sale</dt>
              <dd style={{ margin: 0 }}>
                {detail.sale_uses_shipping_saint == null
                  ? "Unknown"
                  : detail.sale_uses_shipping_saint
                    ? "Yes — fulfillment owned in Shipping Saint"
                    : "No — CT manual fulfillment"}
              </dd>
              <dt className="ct-muted">Child invoices</dt>
              <dd style={{ margin: 0 }}>{detail.child_invoice_count}</dd>
              <dt className="ct-muted">Pending fulfillment items</dt>
              <dd style={{ margin: 0 }}>{detail.pending_fulfillment_count}</dd>
            </dl>
            <p className="ct-muted" style={{ marginTop: "1rem", marginBottom: "0.75rem" }}>
              Next spike step: if lot invoice is Paid + wants shipping + no shipping child yet, offer
              “Create shipping invoice” (CT addon) or “Open in Shipping Saint.”
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
              <a
                className="ct-btn ct-btn--primary"
                href={detail.shipping_saint_manage_url}
                target="_blank"
                rel="noreferrer"
              >
                Open Shipping Saint
              </a>
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => setDetail(null)}>
                Close
              </button>
            </div>
          </section>
        ) : null}
      </div>
    </AppShell>
  );
}
