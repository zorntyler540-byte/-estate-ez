"use client";

import { useEffect, useMemo, useState } from "react";
import { apiFetch, parseApiErrorMessage } from "../lib/api";

export type SaleReserveLot = {
  id: string;
  sku: string | null;
  auction_title: string;
  reserve_price?: number | null;
  ctseller_item_id?: number | null;
};

export type SaleReserveLotUpdate = {
  id: string;
  reserve_price: number | null;
};

type ApplyMode = "all" | "missing" | "selected" | "one";

type Props = {
  saleId: string;
  token: string;
  initialReserve: number | null;
  lots: SaleReserveLot[];
  postedCount: number;
  disabled?: boolean;
  onApplied?: (args: {
    saleDefault?: number | null;
    lotUpdates: SaleReserveLotUpdate[];
  }) => void;
};

type ReserveResponse = {
  reserve_price?: number | null;
  lots_updated?: number;
  posted_updated?: number;
  posted_failed?: number;
  posted_skipped?: number;
};

function formatReserve(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `$${value.toFixed(value % 1 === 0 ? 0 : 2)}`;
}

function parseReserveDraft(raw: string): { ok: boolean; value: number | null } {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: null };
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) return { ok: false, value: null };
  return { ok: true, value: n > 0 ? n : null };
}

export function SaleReservePrice({
  saleId,
  token,
  initialReserve,
  lots,
  postedCount,
  disabled,
  onApplied
}: Props) {
  const [draft, setDraft] = useState(initialReserve != null ? String(initialReserve) : "");
  const [pushPosted, setPushPosted] = useState(postedCount > 0);
  const [busy, setBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [openList, setOpenList] = useState(false);
  const [filter, setFilter] = useState<"all" | "missing" | "set">("all");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [rowDrafts, setRowDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    setDraft(initialReserve != null ? String(initialReserve) : "");
  }, [saleId, initialReserve]);

  useEffect(() => {
    setPushPosted(postedCount > 0);
    setSelected({});
    setRowDrafts({});
    setOpenList(false);
    setFilter("all");
    setMsg("");
    setErr("");
  }, [saleId, postedCount]);

  const missingCount = lots.filter((lot) => lot.reserve_price == null).length;
  const setCount = lots.length - missingCount;

  const visibleLots = useMemo(() => {
    const filtered = lots.filter((lot) => {
      if (filter === "missing") return lot.reserve_price == null;
      if (filter === "set") return lot.reserve_price != null;
      return true;
    });
    return [...filtered].sort((a, b) =>
      (a.sku || "").localeCompare(b.sku || "", undefined, { numeric: true, sensitivity: "base" })
    );
  }, [lots, filter]);

  const selectedIds = Object.keys(selected).filter((id) => selected[id]);
  const selectedPostedCount = lots.filter(
    (lot) => selected[lot.id] && lot.ctseller_item_id
  ).length;

  const summarize = (res: ReserveResponse, mode: ApplyMode, count: number) => {
    const verb =
      mode === "missing"
        ? `Set reserve on ${res.lots_updated ?? 0} lot${res.lots_updated === 1 ? "" : "s"} that had none`
        : mode === "selected"
          ? `Set reserve on ${res.lots_updated ?? 0} selected lot${res.lots_updated === 1 ? "" : "s"}`
          : mode === "one"
            ? `Updated reserve on ${count} lot`
            : `Set reserve on ${res.lots_updated ?? 0} lot${res.lots_updated === 1 ? "" : "s"}`;
    const parts = [verb];
    if (pushPosted && postedCount > 0) {
      parts.push(`updated ${res.posted_updated ?? 0} posted on CT Seller`);
      if (res.posted_failed) parts.push(`${res.posted_failed} failed`);
    }
    return `${parts.join(" — ")}.`;
  };

  const postReserve = async (body: Record<string, unknown>) =>
    (await apiFetch(`/sales/${encodeURIComponent(saleId)}/reserve-price`, token, {
      method: "POST",
      body: JSON.stringify(body)
    })) as ReserveResponse;

  const apply = async (mode: ApplyMode, lotIds?: string[], rowAmount?: number | null) => {
    if (!token || !saleId) return;
    const parsed = parseReserveDraft(draft);
    const reserve = mode === "one" ? (rowAmount ?? null) : parsed.value;
    if (mode !== "one" && draft.trim() && !parsed.ok) {
      setErr("Enter a number (or leave blank to clear reserves).");
      return;
    }
    if (mode === "selected" && (!lotIds || lotIds.length === 0)) {
      setErr("Select one or more lots in the list.");
      return;
    }
    if (mode === "missing" && missingCount === 0) {
      setErr("Every lot already has a reserve.");
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = await postReserve({
        reserve_price: reserve,
        apply_to_lots: true,
        push_posted: Boolean(pushPosted && postedCount > 0),
        only_without_reserve: mode === "missing",
        update_sale_default: mode === "all" || mode === "missing",
        lot_ids: lotIds && lotIds.length ? lotIds : null
      });
      const saved = res.reserve_price ?? null;
      const targetLots =
        mode === "missing"
          ? lots.filter((lot) => lot.reserve_price == null)
          : mode === "all"
            ? lots
            : lots.filter((lot) => lotIds?.includes(lot.id));
      const amount = mode === "one" ? (rowAmount ?? null) : saved;
      onApplied?.({
        saleDefault: mode === "all" || mode === "missing" ? saved : undefined,
        lotUpdates: targetLots.map((lot) => ({ id: lot.id, reserve_price: amount }))
      });
      setMsg(summarize(res, mode, targetLots.length));
      if (mode === "selected") setSelected({});
    } catch (e) {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not set reserve");
    } finally {
      setBusy(false);
    }
  };

  const saveRow = async (lot: SaleReserveLot) => {
    const parsed = parseReserveDraft(rowDrafts[lot.id] ?? String(lot.reserve_price ?? ""));
    if (!parsed.ok) {
      setErr(`Enter a number for ${lot.sku || "this lot"} (or leave blank to clear).`);
      return;
    }
    setRowBusy(lot.id);
    setErr("");
    setMsg("");
    try {
      const res = await postReserve({
        reserve_price: parsed.value,
        apply_to_lots: true,
        push_posted: Boolean(pushPosted && lot.ctseller_item_id),
        update_sale_default: false,
        lot_ids: [lot.id]
      });
      onApplied?.({
        lotUpdates: [{ id: lot.id, reserve_price: parsed.value }]
      });
      setRowDrafts((prev) => {
        const next = { ...prev };
        delete next[lot.id];
        return next;
      });
      setMsg(summarize(res, "one", 1));
    } catch (e) {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not set reserve");
    } finally {
      setRowBusy(null);
    }
  };

  const toggleAllVisible = (on: boolean) => {
    setSelected((prev) => {
      const next = { ...prev };
      for (const lot of visibleLots) next[lot.id] = on;
      return next;
    });
  };

  return (
    <div className="ct-sale-reserve">
      <div className="ct-sale-reserve__head">
        <div>
          <h3 className="ct-sale-reserve__title">CT Seller reserve</h3>
          <p className="ct-sale-reserve__hint">
            Hidden reserve on CT Seller. Bidding still starts at the lot starting price (usually $1).
            Apply a blanket amount, fill only lots with no reserve, or change individual lots below.
            Posted lots are updated in place — photos are not re-sent.
          </p>
        </div>
      </div>
      <div className="ct-sale-reserve__row">
        <label className="ct-label" htmlFor="sale-reserve-price">
          Reserve amount
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
          onClick={() => void apply("all")}
        >
          {busy ? "Saving…" : "Apply to all lots"}
        </button>
        <button
          type="button"
          className="ct-btn"
          disabled={disabled || busy || !saleId || missingCount === 0}
          onClick={() => void apply("missing")}
          title="Leaves lots that already have a reserve alone"
        >
          Fill {missingCount} with no reserve
        </button>
      </div>
      {postedCount > 0 ? (
        <label className="ct-sale-reserve__check">
          <input
            type="checkbox"
            checked={pushPosted}
            disabled={disabled || busy || Boolean(rowBusy)}
            onChange={(e) => setPushPosted(e.target.checked)}
          />
          Also update posted lots on CT Seller
        </label>
      ) : (
        <p className="ct-sale-reserve__hint">Not on CT Seller yet — this reserve goes out when you post.</p>
      )}
      <button
        type="button"
        className="ct-sale-reserve__toggle"
        disabled={!lots.length}
        onClick={() => setOpenList((open) => !open)}
      >
        {openList ? "Hide" : "Show"} lot reserves ({setCount} set · {missingCount} none)
      </button>
      {openList ? (
        <div className="ct-sale-reserve-list">
          <div className="ct-sale-reserve-list__toolbar">
            <div className="ct-sale-reserve-list__filters" role="tablist" aria-label="Reserve filter">
              {(
                [
                  ["all", `All (${lots.length})`],
                  ["missing", `No reserve (${missingCount})`],
                  ["set", `Has reserve (${setCount})`]
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={filter === key}
                  className={
                    filter === key
                      ? "ct-btn ct-btn--primary"
                      : "ct-btn ct-btn--ghost"
                  }
                  onClick={() => setFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="ct-sale-reserve-list__actions">
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => toggleAllVisible(true)}>
                Select visible
              </button>
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => setSelected({})}>
                Clear
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                disabled={disabled || busy || selectedIds.length === 0}
                onClick={() => void apply("selected", selectedIds)}
              >
                Apply to {selectedIds.length} selected
              </button>
            </div>
          </div>
          {selectedIds.length && selectedPostedCount ? (
            <p className="ct-sale-reserve__hint">
              {selectedPostedCount} selected lot{selectedPostedCount === 1 ? " is" : "s are"} already on CT
              Seller{pushPosted ? " and will be updated" : " — check the box above to update them"}.
            </p>
          ) : null}
          <div className="ct-sale-reserve-list__table-wrap">
            <table className="ct-sale-reserve-list__table">
              <thead>
                <tr>
                  <th className="ct-sale-reserve-list__check-col">
                    <span className="ct-sr-only">Select</span>
                  </th>
                  <th>SKU</th>
                  <th>Title</th>
                  <th>Current</th>
                  <th>New</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {visibleLots.map((lot) => {
                  const rowValue = rowDrafts[lot.id] ?? (lot.reserve_price != null ? String(lot.reserve_price) : "");
                  const dirty =
                    parseReserveDraft(rowValue).value !== (lot.reserve_price ?? null) ||
                    (rowDrafts[lot.id] != null && rowDrafts[lot.id] !== String(lot.reserve_price ?? ""));
                  return (
                    <tr key={lot.id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={Boolean(selected[lot.id])}
                          onChange={(e) =>
                            setSelected((prev) => ({ ...prev, [lot.id]: e.target.checked }))
                          }
                          aria-label={`Select ${lot.sku || lot.auction_title || "lot"}`}
                        />
                      </td>
                      <td className="ct-sale-reserve-list__sku">{lot.sku || "—"}</td>
                      <td className="ct-sale-reserve-list__title" title={lot.auction_title}>
                        {lot.auction_title || "—"}
                      </td>
                      <td>{formatReserve(lot.reserve_price)}</td>
                      <td>
                        <div className="ct-input-prefix ct-sale-reserve__row-input">
                          <span className="ct-input-prefix__symbol" aria-hidden>
                            $
                          </span>
                          <input
                            className="ct-input"
                            type="number"
                            inputMode="decimal"
                            step="0.01"
                            min="0"
                            placeholder="none"
                            value={rowValue}
                            disabled={disabled || busy || rowBusy === lot.id}
                            onChange={(e) =>
                              setRowDrafts((prev) => ({ ...prev, [lot.id]: e.target.value }))
                            }
                          />
                        </div>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="ct-btn ct-btn--ghost"
                          disabled={disabled || busy || rowBusy === lot.id || !dirty}
                          onClick={() => void saveRow(lot)}
                        >
                          {rowBusy === lot.id ? "Saving…" : "Save"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {visibleLots.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="ct-sale-reserve-list__empty">
                      No lots in this filter.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {msg ? <div className="ct-banner ct-banner--success">{msg}</div> : null}
      {err ? <div className="ct-banner ct-banner--error">{err}</div> : null}
    </div>
  );
}
