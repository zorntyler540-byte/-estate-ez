"use client";

import { hours, money, percent } from "../../lib/opsFormat";
import type { OpsSettlement } from "../../lib/ops";
import type { SettlementResult } from "../../lib/quote";

type Draft = {
  actual_hours: number;
  in_person_proceeds: number;
  online_proceeds: number;
  buyer_premium: number;
  shipping: number;
  misc_revenue: number;
  direct_expenses: number;
  deposit_received: number;
  balance_received: number;
};

export function emptySettlementDraft(settlement: OpsSettlement | null): Draft {
  return {
    actual_hours: settlement?.actual_hours ?? 0,
    in_person_proceeds: settlement?.in_person_proceeds ?? 0,
    online_proceeds: settlement?.online_proceeds ?? 0,
    buyer_premium: settlement?.buyer_premium ?? 0,
    shipping: settlement?.shipping ?? 0,
    misc_revenue: settlement?.misc_revenue ?? 0,
    direct_expenses: settlement?.direct_expenses ?? 0,
    deposit_received: settlement?.deposit_received ?? 0,
    balance_received: settlement?.balance_received ?? 0
  };
}

export function SettlementForm({
  draft,
  onChange,
  onSave,
  pending,
  error,
  settlement,
  result,
  laborTarget
}: {
  draft: Draft;
  onChange: (next: Draft) => void;
  onSave: () => void;
  pending: boolean;
  error: string;
  settlement: OpsSettlement | null;
  result: SettlementResult | null;
  laborTarget: number;
}) {
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => onChange({ ...draft, [key]: value });
  return (
    <section className="ct-card ct-card--padded">
      <h2 className="ct-section-title" style={{ marginTop: 0 }}>
        Settlement
      </h2>
      <p className="ct-muted">
        Service prices stay on the quote. The sale fee is recalculated from the proceeds you enter here.
      </p>
      {error ? <p className="ct-error-text">{error}</p> : null}
      <div className="ct-ops-form-grid ct-ops-form-grid--3">
        <NumberField label="Actual hours" value={draft.actual_hours} step="0.5" onChange={(n) => set("actual_hours", n)} />
        <NumberField
          label="In-person proceeds"
          value={draft.in_person_proceeds}
          onChange={(n) => set("in_person_proceeds", n)}
        />
        <NumberField label="Online proceeds" value={draft.online_proceeds} onChange={(n) => set("online_proceeds", n)} />
        <NumberField label="Buyer premium" value={draft.buyer_premium} onChange={(n) => set("buyer_premium", n)} />
        <NumberField label="Shipping and handling" value={draft.shipping} onChange={(n) => set("shipping", n)} />
        <NumberField label="Misc revenue" value={draft.misc_revenue} onChange={(n) => set("misc_revenue", n)} />
        <NumberField label="Direct expenses" value={draft.direct_expenses} onChange={(n) => set("direct_expenses", n)} />
        <NumberField label="Deposit received" value={draft.deposit_received} onChange={(n) => set("deposit_received", n)} />
        <NumberField label="Balance received" value={draft.balance_received} onChange={(n) => set("balance_received", n)} />
      </div>
      <div className="ct-form-actions">
        <button type="button" className="ct-btn ct-btn--primary" disabled={pending} onClick={onSave}>
          {pending ? "Saving…" : "Save settlement"}
        </button>
      </div>
      {result && settlement ? (
        <dl className="ct-ops-stats">
          <Stat label="Invoiced" value={money(result.invoiced)} />
          <Stat label="Sale fee" value={money(result.commission)} />
          <Stat label="Labor cost" value={money(result.laborCost)} />
          <Stat label="Labor" value={percent(result.laborPercent)} warn={result.laborPercent > laborTarget} />
          <Stat label="Royalty" value={money(result.royalty)} />
          <Stat label="Job contribution" value={money(result.contribution)} />
          <Stat label="Hours worked" value={hours(settlement.actual_hours)} />
          <Stat
            label="Still due"
            value={money(result.invoiced - settlement.deposit_received - settlement.balance_received)}
          />
        </dl>
      ) : null}
    </section>
  );
}

function NumberField({
  label,
  value,
  onChange,
  step = "1"
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  step?: string;
}) {
  return (
    <label className="ct-label">
      {label}
      <input
        className="ct-input"
        type="number"
        min={0}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value) || 0)}
      />
    </label>
  );
}

function Stat({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <dt className="ct-muted">{label}</dt>
      <dd className={`ct-ops-num${warn ? " ct-ops-warn" : ""}`}>{value}</dd>
    </div>
  );
}
