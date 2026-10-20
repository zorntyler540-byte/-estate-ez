"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { OperationsShell, OperationsSignInPrompt, operationsActions } from "../../../components/operations/OperationsShell";
import { apiFetch } from "../../../lib/api";
import { isAuthFailureMessage, type OpsMonth, type OpsSettings } from "../../../lib/ops";
import { money, monthLabel, percent, shiftMonth, thisMonth } from "../../../lib/opsFormat";

function MonthInner() {
  const router = useRouter();
  const search = useSearchParams();
  const requested = search.get("m") || "";
  const month = /^\d{4}-\d{2}$/.test(requested) ? requested : thisMonth();
  const [token, setToken] = useState("");
  const [data, setData] = useState<OpsMonth | null>(null);
  const [laborTarget, setLaborTarget] = useState(0.33);
  const [consultations, setConsultations] = useState(0);
  const [marketingSpend, setMarketingSpend] = useState(0);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void Promise.all([
      apiFetch(`/ops/month?m=${encodeURIComponent(month)}`, t) as Promise<OpsMonth>,
      apiFetch("/ops/settings", t) as Promise<OpsSettings>
    ])
      .then(([monthData, settings]) => {
        setData(monthData);
        setConsultations(monthData.consultations);
        setMarketingSpend(monthData.marketing_spend);
        setLaborTarget(settings.rates.labor_target);
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load month";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
      })
      .finally(() => setLoading(false));
  }, [month, router]);

  const totals = useMemo(() => {
    const rows = data?.jobs || [];
    return rows.reduce(
      (sum, row) => ({
        invoiced: sum.invoiced + row.result.invoiced,
        labor: sum.labor + row.result.laborCost,
        royalty: sum.royalty + row.result.royalty,
        royaltyBase: sum.royaltyBase + row.result.royaltyBase,
        contribution: sum.contribution + row.result.contribution,
        setup: sum.setup + row.result.setup,
        relocation: sum.relocation + row.result.relocation,
        cleanout: sum.cleanout + row.result.cleanout,
        inPerson: sum.inPerson + row.settlement.in_person_proceeds,
        online: sum.online + row.settlement.online_proceeds,
        commission: sum.commission + row.result.commission,
        premium: sum.premium + row.settlement.buyer_premium,
        shipping: sum.shipping + row.settlement.shipping,
        misc: sum.misc + row.settlement.misc_revenue
      }),
      {
        invoiced: 0,
        labor: 0,
        royalty: 0,
        royaltyBase: 0,
        contribution: 0,
        setup: 0,
        relocation: 0,
        cleanout: 0,
        inPerson: 0,
        online: 0,
        commission: 0,
        premium: 0,
        shipping: 0,
        misc: 0
      }
    );
  }, [data]);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function saveNotes(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setPending(true);
    try {
      const saved = (await apiFetch("/ops/month", token, {
        method: "PUT",
        body: JSON.stringify({
          month,
          consultations,
          marketing_spend: marketingSpend
        })
      })) as OpsMonth;
      setData(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save month notes.");
    } finally {
      setPending(false);
    }
  }

  if (!token) return <OperationsSignInPrompt />;
  const rows = data?.jobs || [];

  return (
    <OperationsShell actions={operationsActions(logout)}>
      <div className="ct-dashboard" style={{ maxWidth: 960 }}>
        <header className="ct-page-header">
          <h1 className="ct-page-title">{monthLabel(month)}</h1>
          <div className="ct-row">
            <Link href={`/operations/month?m=${shiftMonth(month, -1)}`}>Previous</Link>
            <Link href={`/operations/month?m=${shiftMonth(month, 1)}`}>Next</Link>
          </div>
        </header>
        <p className="ct-subtitle">
          Settled jobs only. Royalty base follows the monthly workbook: sale proceeds are included, and the commission
          is not added on top of those proceeds. A fee-only job with no proceeds still includes the sale fee.
        </p>
        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
        {loading ? <p className="ct-muted">Loading…</p> : null}
        <div className="ct-ops-tiles ct-ops-tiles--4">
          <Tile label="Settled jobs" value={String(rows.length)} />
          <Tile label="Invoiced" value={money(totals.invoiced)} />
          <Tile label="Labor" value={totals.invoiced > 0 ? percent(totals.labor / totals.invoiced) : "—"} />
          <Tile label="Job contribution" value={money(totals.contribution)} />
        </div>
        {totals.invoiced > 0 && totals.labor / totals.invoiced > laborTarget ? (
          <p className="ct-ops-warn">Labor is above the {percent(laborTarget)} target in rates.</p>
        ) : null}
        <dl className="ct-card ct-card--padded ct-ops-dl ct-ops-dl--2">
          <Line label="Sorting and setup" value={totals.setup} />
          <Line label="Relocation" value={totals.relocation} />
          <Line label="Cleanout" value={totals.cleanout} />
          <Line label="In-person proceeds" value={totals.inPerson} />
          <Line label="Online proceeds" value={totals.online} />
          <Line label="Commission / minimum" value={totals.commission} />
          <Line label="Buyer premium" value={totals.premium} />
          <Line label="Shipping and handling" value={totals.shipping} />
          <Line label="Misc" value={totals.misc} />
          <Line label="Royalty base" value={totals.royaltyBase} />
          <Line label="Royalty" value={totals.royalty} />
        </dl>
        <div className="ct-table-wrap">
          <table className="ct-table">
            <thead>
              <tr>
                <th>Client</th>
                <th className="ct-table__num">Invoiced</th>
                <th className="ct-table__num">Labor</th>
                <th className="ct-table__num">Contribution</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td className="ct-muted" colSpan={4}>
                    No settled jobs this month.
                  </td>
                </tr>
              ) : (
                rows.map(({ job, result }) => (
                  <tr key={job.id}>
                    <td>
                      <Link href={`/operations/jobs/${job.id}`}>{job.client_name}</Link>
                    </td>
                    <td className="ct-table__num">{money(result.invoiced)}</td>
                    <td className="ct-table__num">{percent(result.laborPercent)}</td>
                    <td className="ct-table__num">{money(result.contribution)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <form className="ct-card ct-card--padded ct-ops-form-grid ct-ops-form-grid--3" onSubmit={saveNotes}>
          <label className="ct-label">
            Consultations
            <input
              className="ct-input"
              type="number"
              min={0}
              value={consultations}
              onChange={(e) => setConsultations(Math.max(0, Math.round(Number(e.target.value) || 0)))}
            />
          </label>
          <label className="ct-label">
            Marketing spend
            <input
              className="ct-input"
              type="number"
              min={0}
              step="0.01"
              value={marketingSpend}
              onChange={(e) => setMarketingSpend(Number(e.target.value) || 0)}
            />
          </label>
          <div className="ct-form-actions" style={{ alignItems: "flex-end" }}>
            <button className="ct-btn ct-btn--primary" type="submit" disabled={pending}>
              {pending ? "Saving…" : "Save month notes"}
            </button>
          </div>
          <p className="ct-muted ct-ops-span-3">
            {consultations > 0
              ? `Settled jobs ÷ consultations: ${percent(rows.length / consultations)}.`
              : "Enter consultations to see settled jobs divided by consultations."}{" "}
            Marketing spend is overhead. It is not inside job contribution.
          </p>
        </form>
      </div>
    </OperationsShell>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="ct-stat-card">
      <p className="ct-stat-card__label">{label}</p>
      <p className="ct-stat-card__value">{value}</p>
    </div>
  );
}

function Line({ label, value }: { label: string; value: number }) {
  return (
    <div className="ct-ops-dl__row">
      <dt className="ct-muted">{label}</dt>
      <dd className="ct-ops-num">{money(value)}</dd>
    </div>
  );
}

export default function OperationsMonthPage() {
  return (
    <Suspense fallback={<p className="ct-muted">Loading…</p>}>
      <MonthInner />
    </Suspense>
  );
}
