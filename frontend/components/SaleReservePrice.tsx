"use client";

import { useEffect, useState } from "react";
import { apiFetch, parseApiErrorMessage } from "../lib/api";

type Props = {
  saleId: string;
  token: string;
  initialReserve: number | null;
  postedCount: number;
  disabled?: boolean;
  onApplied?: (reserve: number | null) => void;
};

export function SaleReservePrice({
  saleId,
  token,
  initialReserve,
  postedCount,
  disabled,
  onApplied
}: Props) {
  const [draft, setDraft] = useState(initialReserve != null ? String(initialReserve) : "");
  const [pushPosted, setPushPosted] = useState(postedCount > 0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    setDraft(initialReserve != null ? String(initialReserve) : "");
  }, [saleId, initialReserve]);

  useEffect(() => {
    setPushPosted(postedCount > 0);
  }, [saleId, postedCount]);

  const parsed = (): number | null => {
    const raw = draft.trim();
    if (!raw) return null;
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 0) return null;
    return n > 0 ? n : null;
  };

  const apply = async () => {
    if (!token || !saleId) return;
    const reserve = parsed();
    if (draft.trim() && reserve == null) {
      setErr("Enter a number (or leave blank to clear reserves).");
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = (await apiFetch(`/sales/${encodeURIComponent(saleId)}/reserve-price`, token, {
        method: "POST",
        body: JSON.stringify({
          reserve_price: reserve,
          apply_to_lots: true,
          push_posted: Boolean(pushPosted && postedCount > 0)
        })
      })) as {
        reserve_price?: number | null;
        lots_updated?: number;
        posted_updated?: number;
        posted_failed?: number;
        posted_skipped?: number;
      };
      const saved = res.reserve_price ?? null;
      onApplied?.(saved);
      const parts = [`Set reserve on ${res.lots_updated ?? 0} lot${res.lots_updated === 1 ? "" : "s"}`];
      if (pushPosted && postedCount > 0) {
        parts.push(`updated ${res.posted_updated ?? 0} posted on CT Seller`);
        if (res.posted_failed) parts.push(`${res.posted_failed} failed`);
      }
      setMsg(`${parts.join(" — ")}. New posts will include this reserve.`);
    } catch (e) {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not set reserve");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ct-sale-reserve">
      <div className="ct-sale-reserve__head">
        <div>
          <h3 className="ct-sale-reserve__title">CT Seller reserve</h3>
          <p className="ct-sale-reserve__hint">
            One hidden reserve for every lot in this sale. Bidding still starts at the lot starting
            price (usually $1). Posted lots are updated in place — photos are not re-sent.
          </p>
        </div>
      </div>
      <div className="ct-sale-reserve__row">
        <label className="ct-label" htmlFor="sale-reserve-price">
          Reserve for all lots
        </label>
        <div className="ct-input-prefix ct-sale-reserve__input">
          <span className="ct-input-prefix__symbol" aria-hidden>
            $
          </span>
          <input
            id="sale-reserve-price"
            className="ct-input"
            type="number"
            inputMode="decimal"
            step="0.01"
            min="0"
            placeholder="none"
            value={draft}
            disabled={disabled || busy}
            onChange={(e) => setDraft(e.target.value)}
          />
        </div>
        <button
          type="button"
          className="ct-btn ct-btn--primary"
          disabled={disabled || busy || !saleId}
          onClick={() => void apply()}
        >
          {busy ? "Saving…" : "Apply to all lots"}
        </button>
      </div>
      {postedCount > 0 ? (
        <label className="ct-sale-reserve__check">
          <input
            type="checkbox"
            checked={pushPosted}
            disabled={disabled || busy}
            onChange={(e) => setPushPosted(e.target.checked)}
          />
          Also update {postedCount} lot{postedCount === 1 ? "" : "s"} already on CT Seller
        </label>
      ) : (
        <p className="ct-sale-reserve__hint">Not on CT Seller yet — this reserve goes out when you post.</p>
      )}
      {msg ? <div className="ct-banner ct-banner--success">{msg}</div> : null}
      {err ? <div className="ct-banner ct-banner--error">{err}</div> : null}
    </div>
  );
}
