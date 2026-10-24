"use client";

import type { OpsJobWrite, OpsSaleOption } from "../../lib/ops";
import { OPS_STATUS_LABELS, OPS_STATUSES } from "../../lib/ops";
import { DENSITY_LABELS, LIQUIDATION_LABELS, spacePlanHoursFrom } from "../../lib/quote";

export function JobFields({
  value,
  onChange,
  sales,
  locked,
  showStatus,
  saleLotCount
}: {
  value: OpsJobWrite;
  onChange: (next: OpsJobWrite) => void;
  sales: OpsSaleOption[];
  locked?: boolean;
  showStatus?: boolean;
  saleLotCount?: number | null;
}) {
  const set = <K extends keyof OpsJobWrite>(key: K, field: OpsJobWrite[K]) => onChange({ ...value, [key]: field });
  const setNum = (key: keyof OpsJobWrite, raw: string) => set(key, (Number(raw) || 0) as never);
  const computedSpaceHours = spacePlanHoursFrom(value.space_plan_areas, value.space_plan_items, value.space_plan_hours);

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
      {value.liquidation === "auction" ? (
        <>
          <label className="ct-label">
            Projected lots
            <input
              className="ct-input"
              type="number"
              min={0}
              step={1}
              value={value.projected_lots}
              disabled={locked}
              onChange={(e) => setNum("projected_lots", e.target.value)}
            />
            <span className="ct-field-hint">
              Photograph 4/hr, edit 10/hr, pickup prep on 30% of lots at 10/hr. Leave 0 to use cubic footage.
            </span>
            {saleLotCount && saleLotCount > 0 && !locked ? (
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                style={{ marginTop: "0.4rem" }}
                onClick={() => set("projected_lots", saleLotCount)}
              >
                Use {saleLotCount} cataloged lots
              </button>
            ) : null}
          </label>
          <label className="ct-label">
            Buyer questions
            <input
              className="ct-input"
              type="number"
              min={0}
              step={1}
              value={value.auction_questions}
              disabled={locked}
              onChange={(e) => setNum("auction_questions", e.target.value)}
            />
            <span className="ct-field-hint">10 questions per labor hour. Used only when lots are entered.</span>
          </label>
          <label className="ct-label">
            Pickup days
            <input
              className="ct-input"
              type="number"
              min={0}
              step={1}
              value={value.pickup_days}
              disabled={locked}
              onChange={(e) => setNum("pickup_days", e.target.value)}
            />
          </label>
          <label className="ct-label">
            Pickup staff
            <input
              className="ct-input"
              type="number"
              min={0}
              step={1}
              value={value.pickup_staff}
              disabled={locked}
              onChange={(e) => setNum("pickup_staff", e.target.value)}
            />
          </label>
          <label className="ct-label">
            Pickup hours / day
            <input
              className="ct-input"
              type="number"
              min={0}
              step={0.5}
              value={value.pickup_hours_per_day}
              disabled={locked}
              onChange={(e) => setNum("pickup_hours_per_day", e.target.value)}
            />
          </label>
        </>
      ) : null}
      {value.liquidation === "estate" || value.liquidation === "flash" ? (
        <>
          <label className="ct-label">
            Sale days
            <input
              className="ct-input"
              type="number"
              min={0}
              step={1}
              value={value.sale_days}
              disabled={locked}
              onChange={(e) => setNum("sale_days", e.target.value)}
            />
            <span className="ct-field-hint">Staffed sale-day hours add to prep, not a second hourly invoice.</span>
          </label>
          <label className="ct-label">
            Sale staff
            <input
              className="ct-input"
              type="number"
              min={0}
              step={1}
              value={value.sale_staff}
              disabled={locked}
              onChange={(e) => setNum("sale_staff", e.target.value)}
            />
          </label>
          <label className="ct-label">
            Hours / sale day
            <input
              className="ct-input"
              type="number"
              min={0}
              step={0.5}
              value={value.sale_hours_per_day}
              disabled={locked}
              onChange={(e) => setNum("sale_hours_per_day", e.target.value)}
            />
          </label>
        </>
      ) : null}
      <label className="ct-label">
        Space-plan hours
        <input
          className="ct-input"
          type="number"
          min={0}
          step={0.5}
          value={value.space_plan_hours}
          disabled={locked || value.space_plan_areas > 0 || value.space_plan_items > 0}
          onChange={(e) => setNum("space_plan_hours", e.target.value)}
        />
        {value.space_plan_areas > 0 || value.space_plan_items > 0 ? (
          <span className="ct-field-hint">Using the takeoff: {computedSpaceHours.toLocaleString("en-US", { maximumFractionDigits: 1 })} hours (4-hour minimum).</span>
        ) : (
          <span className="ct-field-hint">Or fill areas and items below. 8 minutes per new area, 10 per item, 4-hour minimum.</span>
        )}
      </label>
      <label className="ct-label">
        New-home areas
        <input
          className="ct-input"
          type="number"
          min={0}
          step={1}
          value={value.space_plan_areas}
          disabled={locked}
          onChange={(e) => setNum("space_plan_areas", e.target.value)}
        />
      </label>
      <label className="ct-label">
        Items to measure
        <input
          className="ct-input"
          type="number"
          min={0}
          step={1}
          value={value.space_plan_items}
          disabled={locked}
          onChange={(e) => setNum("space_plan_items", e.target.value)}
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
