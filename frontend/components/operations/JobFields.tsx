"use client";

import type { OpsJobWrite, OpsSaleOption } from "../../lib/ops";
import { OPS_STATUS_LABELS, OPS_STATUSES } from "../../lib/ops";
import { DENSITY_LABELS, LIQUIDATION_LABELS } from "../../lib/quote";

export function JobFields({
  value,
  onChange,
  sales,
  locked,
  showStatus
}: {
  value: OpsJobWrite;
  onChange: (next: OpsJobWrite) => void;
  sales: OpsSaleOption[];
  locked?: boolean;
  showStatus?: boolean;
}) {
  const set = <K extends keyof OpsJobWrite>(key: K, field: OpsJobWrite[K]) => onChange({ ...value, [key]: field });

  return (
    <div className="ct-ops-form-grid">
      <label className="ct-label">
        Client name
        <input
          className="ct-input"
          value={value.client_name}
          onChange={(e) => set("client_name", e.target.value)}
          required
        />
      </label>
      {showStatus ? (
        <label className="ct-label">
          Status
          <select
            className="ct-input"
            value={value.status}
            disabled={locked}
            onChange={(e) => set("status", e.target.value as OpsJobWrite["status"])}
          >
            {OPS_STATUSES.map((status) => (
              <option key={status} value={status} disabled={status === "settled" && !locked}>
                {OPS_STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <label className="ct-label">
        Phone
        <input className="ct-input" value={value.phone} onChange={(e) => set("phone", e.target.value)} />
      </label>
      <label className="ct-label">
        Email
        <input className="ct-input" type="email" value={value.email} onChange={(e) => set("email", e.target.value)} />
      </label>
      <label className="ct-label ct-ops-span-2">
        Address
        <input className="ct-input" value={value.address} onChange={(e) => set("address", e.target.value)} />
      </label>
      <label className="ct-label">
        ZIP
        <input className="ct-input" value={value.zip} onChange={(e) => set("zip", e.target.value)} />
      </label>
      <label className="ct-label">
        Estate-EZ sale
        <select
          className="ct-input"
          value={value.sale_id || ""}
          onChange={(e) => set("sale_id", e.target.value || null)}
        >
          <option value="">None</option>
          {sales.map((sale) => (
            <option key={sale.id} value={sale.id}>
              {sale.name}
              {sale.closed_at ? " (closed)" : ""}
            </option>
          ))}
        </select>
      </label>
      <label className="ct-label">
        Cubic footage
        <input
          className="ct-input"
          type="number"
          min={0}
          step={1}
          value={value.cuft}
          disabled={locked}
          onChange={(e) => set("cuft", Number(e.target.value) || 0)}
        />
        <span className="ct-field-hint">Volume of the job — rooms, piles, and packed goods, not floor area.</span>
      </label>
      <label className="ct-label">
        Density
        <select
          className="ct-input"
          value={value.density}
          disabled={locked}
          onChange={(e) => set("density", e.target.value as OpsJobWrite["density"])}
        >
          {(Object.keys(DENSITY_LABELS) as Array<keyof typeof DENSITY_LABELS>).map((key) => (
            <option key={key} value={key}>
              {DENSITY_LABELS[key]}
            </option>
          ))}
        </select>
      </label>
      <label className="ct-label">
        Sale type
        <select
          className="ct-input"
          value={value.liquidation}
          disabled={locked}
          onChange={(e) => set("liquidation", e.target.value as OpsJobWrite["liquidation"])}
        >
          {(Object.keys(LIQUIDATION_LABELS) as Array<keyof typeof LIQUIDATION_LABELS>).map((key) => (
            <option key={key} value={key}>
              {LIQUIDATION_LABELS[key]}
            </option>
          ))}
        </select>
      </label>
      <label className="ct-label">
        Expected proceeds
        <input
          className="ct-input"
          type="number"
          min={0}
          step={1}
          value={value.projected_proceeds}
          disabled={locked}
          onChange={(e) => set("projected_proceeds", Number(e.target.value) || 0)}
        />
      </label>
      <label className="ct-label">
        Space-plan hours
        <input
          className="ct-input"
          type="number"
          min={0}
          step={0.5}
          value={value.space_plan_hours}
          disabled={locked}
          onChange={(e) => set("space_plan_hours", Number(e.target.value) || 0)}
        />
      </label>
      <fieldset className="ct-ops-span-2 ct-ops-checks" disabled={locked}>
        <legend className="ct-label">Services</legend>
        <label>
          <input
            type="checkbox"
            checked={value.include_sorting}
            onChange={(e) => set("include_sorting", e.target.checked)}
          />{" "}
          Sorting and organizing
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.include_cleanout}
            onChange={(e) => set("include_cleanout", e.target.checked)}
          />{" "}
          Cleanout
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.include_packing}
            onChange={(e) => set("include_packing", e.target.checked)}
          />{" "}
          Packing
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.include_resettling}
            onChange={(e) => set("include_resettling", e.target.checked)}
          />{" "}
          Resettling
        </label>
      </fieldset>
      <label className="ct-label ct-ops-span-2">
        Notes
        <textarea className="ct-textarea ct-textarea--compact" value={value.notes} onChange={(e) => set("notes", e.target.value)} />
      </label>
    </div>
  );
}
