"use client";

import { useMemo, useState } from "react";
import { apiFetch, parseApiErrorMessage } from "../lib/api";

export type MoveSaleOption = {
  id: string;
  name: string;
  closed_at?: string | null;
};

type MoveResult = {
  destination_sale_id: string;
  destination_sale_name: string;
  moved_count: number;
  skipped_already_there?: string[];
};

type Props = {
  token: string;
  lotIds: string[];
  currentSaleId: string;
  currentSaleName?: string;
  sales: MoveSaleOption[];
  postedCount?: number;
  disabled?: boolean;
  compact?: boolean;
  buttonLabel?: string;
  onMoved: (result: MoveResult) => void;
};

export function MoveLotsToSale({
  token,
  lotIds,
  currentSaleId,
  currentSaleName,
  sales,
  postedCount = 0,
  disabled = false,
  compact = false,
  buttonLabel,
  onMoved,
}: Props) {
  const openOthers = useMemo(
    () =>
      sales.filter((s) => s.id !== currentSaleId && !s.closed_at),
    [sales, currentSaleId]
  );
  const [destId, setDestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const dest = openOthers.find((s) => s.id === destId) ?? null;
  const count = lotIds.length;
  const label =
    buttonLabel ||
    (count === 1 ? "Move lot" : `Move ${count} lots`);

  async function moveLots() {
    if (!dest || count < 1 || disabled) return;
    const postedNote =
      postedCount > 0
        ? ` ${postedCount} already posted on CT Seller — those listings stay on the old auction until you edit them there.`
        : "";
    const ok = window.confirm(
      `Move ${count === 1 ? "this lot" : `${count} lots`} from ${
        currentSaleName || "the current sale"
      } to ${dest.name}? Photos and videos stay with the lot.${postedNote}`
    );
    if (!ok) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const body = (await apiFetch("/lots/move-to-sale", token, {
        method: "POST",
        body: JSON.stringify({ lot_ids: lotIds, sale_id: dest.id }),
      })) as MoveResult;
      setMsg(
        `Moved ${body.moved_count} lot${body.moved_count === 1 ? "" : "s"} to ${
          body.destination_sale_name
        }.`
      );
      onMoved(body);
    } catch (e) {
      setErr(parseApiErrorMessage(e instanceof Error ? e.message : "", "Could not move lots"));
    } finally {
      setBusy(false);
    }
  }

  if (openOthers.length < 1) {
    return (
      <p className="ct-muted" style={{ margin: compact ? 0 : "0.35rem 0 0" }}>
        No other open sales to move into. Create or reopen a sale first.
      </p>
    );
  }

  return (
    <div className={compact ? "ct-move-sale ct-move-sale--compact" : "ct-move-sale"}>
      {!compact ? (
        <p className="ct-upload-intro" style={{ marginTop: 0 }}>
          Move {count === 1 ? "this lot" : "the selected lots"} to another Estate-EZ sale.
          Photos and videos stay on the lot. This does not re-post or remove CT Seller items.
        </p>
      ) : null}
      <div className="ct-row ct-row--gap-lg" style={{ alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
        <label className="ct-row" style={{ alignItems: "center", gap: "0.4rem", flex: "1 1 14rem" }}>
          <span className="ct-muted" style={{ fontSize: "0.82rem", whiteSpace: "nowrap" }}>
            Move to
          </span>
          <select
            className="ct-input"
            value={destId}
            onChange={(e) => {
              setDestId(e.target.value);
              setErr("");
              setMsg("");
            }}
            disabled={disabled || busy || count < 1}
            aria-label="Destination sale"
          >
            <option value="">Select sale…</option>
            {openOthers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="ct-btn"
          onClick={() => void moveLots()}
          disabled={disabled || busy || count < 1 || !destId}
          title={count < 1 ? "Select lots first" : !destId ? "Pick a destination sale" : label}
        >
          {busy ? "Moving…" : label}
        </button>
      </div>
      {msg ? (
        <div className="ct-banner ct-banner--success" style={{ marginTop: "0.55rem" }}>
          {msg}
        </div>
      ) : null}
      {err ? (
        <div className="ct-banner ct-banner--error" style={{ marginTop: "0.55rem" }}>
          {err}
        </div>
      ) : null}
    </div>
  );
}
