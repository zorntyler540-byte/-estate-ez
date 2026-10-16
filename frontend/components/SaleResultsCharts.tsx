"use client";

import { useMemo } from "react";
import {
  donutArcs,
  hammerTimeBuckets,
  outcomeSlices,
  topAvgBars,
  topHammerBars,
  type HammerBar,
  type OutcomeSlice,
  type TimeBucket
} from "../lib/saleResultsCharts";

function money(n: number): string {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: n >= 100 ? 0 : 2
  });
}

function OutcomeDonut({ slices }: { slices: OutcomeSlice[] }) {
  const total = slices.reduce((s, x) => s + x.count, 0);
  const r = 42;
  const c = 2 * Math.PI * r;
  const arcs = donutArcs(slices, c);
  return (
    <div className="ct-sale-chart">
      <h3 className="ct-sale-chart__title">Results mix</h3>
      <div className="ct-sale-chart__donut-wrap">
        <svg viewBox="0 0 120 120" className="ct-sale-chart__donut" aria-hidden="true">
          <circle cx="60" cy="60" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="14" />
          {arcs.map((arc) => (
            <circle
              key={arc.key}
              cx="60"
              cy="60"
              r={r}
              fill="none"
              stroke={arc.color}
              strokeWidth="14"
              strokeDasharray={`${arc.dash} ${c - arc.dash}`}
              strokeDashoffset={arc.offset}
              strokeLinecap="butt"
              transform="rotate(-90 60 60)"
            />
          ))}
          <text x="60" y="56" textAnchor="middle" fill="#f4f4f5" fontSize="16" fontWeight="700">
            {total.toLocaleString()}
          </text>
          <text x="60" y="72" textAnchor="middle" fill="#a1a1aa" fontSize="9">
            posted
          </text>
        </svg>
        <ul className="ct-sale-chart__legend">
          {slices.map((s) => (
            <li key={s.key}>
              <span className="ct-sale-chart__swatch" style={{ background: s.color }} />
              <span>
                {s.label} · <strong>{s.count.toLocaleString()}</strong>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function CategoryMetricBars({
  title,
  bars,
  metric,
  picked,
  onPick,
  wide
}: {
  title: string;
  bars: HammerBar[];
  metric: "hammer" | "avg";
  picked: string | null;
  onPick: (category: string) => void;
  wide?: boolean;
}) {
  const max = Math.max(...bars.map((b) => (metric === "avg" ? b.avg : b.hammer)), 1);
  return (
    <div className={`ct-sale-chart${wide ? " ct-sale-chart--wide" : ""}`}>
      <h3 className="ct-sale-chart__title">{title}</h3>
      <div className="ct-sale-bars" role="list">
        {bars.map((bar) => {
          const value = metric === "avg" ? bar.avg : bar.hammer;
          const pct = Math.max(2, (value / max) * 100);
          const active = picked === bar.category;
          return (
            <button
              key={bar.category}
              type="button"
              role="listitem"
              className={`ct-sale-bar${active ? " is-active" : ""}`}
              onClick={() => onPick(bar.category)}
              title={`${bar.category}: ${money(value)} ${metric === "avg" ? "avg" : "hammer"} · ${bar.sold} sold`}
            >
              <span className="ct-sale-bar__label">{bar.category}</span>
              <span className="ct-sale-bar__track">
                <span
                  className={`ct-sale-bar__fill${metric === "avg" ? " ct-sale-bar__fill--avg" : ""}`}
                  style={{ width: `${pct}%` }}
                />
              </span>
              <span className="ct-sale-bar__value">{money(value)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MonthBars({
  buckets,
  metric
}: {
  buckets: TimeBucket[];
  metric: "hammer" | "avg";
}) {
  const max = Math.max(...buckets.map((b) => (metric === "avg" ? b.avg : b.hammer)), 1);
  const title = metric === "avg" ? "Average over time" : "Hammer over time";
  return (
    <div className="ct-sale-chart">
      <h3 className="ct-sale-chart__title">{title}</h3>
      <div className="ct-sale-colbars" role="img" aria-label={title}>
        {buckets.map((b) => {
          const value = metric === "avg" ? b.avg : b.hammer;
          const h = Math.max(6, (value / max) * 100);
          return (
            <div key={b.key} className="ct-sale-colbar" title={`${b.label}: ${money(value)} · ${b.sold} sold`}>
              <div className="ct-sale-colbar__track">
                <div
                  className={`ct-sale-colbar__fill${metric === "avg" ? " ct-sale-colbar__fill--avg" : ""}`}
                  style={{ height: `${h}%` }}
                />
              </div>
              <div className="ct-sale-colbar__label">{b.label}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function SaleResultsCharts({
  items,
  categories,
  since,
  until,
  pickedCategory,
  onPickCategory
}: {
  items: Array<{
    outcome?: string;
    sold_price?: number;
    posted_at?: string | null;
    category?: string;
  }>;
  categories: Array<{
    category: string;
    revenue: number;
    sold_count: number;
    posted_count?: number;
    avg_price?: number;
  }>;
  since: string;
  until: string;
  pickedCategory: string | null;
  onPickCategory: (category: string | null) => void;
}) {
  const slices = useMemo(() => outcomeSlices(items), [items]);
  const bars = useMemo(() => topHammerBars(categories), [categories]);
  const avgBars = useMemo(() => topAvgBars(categories), [categories]);
  const times = useMemo(() => hammerTimeBuckets(items, since, until), [items, since, until]);
  if (!items.length) return null;
  const pick = (cat: string) => onPickCategory(pickedCategory === cat ? null : cat);
  return (
    <div className="ct-sale-charts">
      <OutcomeDonut slices={slices} />
      {bars.length ? (
        <CategoryMetricBars
          title="Hammer by category"
          bars={bars}
          metric="hammer"
          picked={pickedCategory}
          onPick={pick}
        />
      ) : null}
      {avgBars.length ? (
        <CategoryMetricBars
          title="Average by category"
          bars={avgBars}
          metric="avg"
          picked={pickedCategory}
          onPick={pick}
          wide
        />
      ) : null}
      {times.length >= 2 ? <MonthBars buckets={times} metric="hammer" /> : null}
      {times.length >= 2 ? <MonthBars buckets={times} metric="avg" /> : null}
    </div>
  );
}
