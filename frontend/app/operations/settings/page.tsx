"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { OperationsShell, OperationsSignInPrompt, operationsActions } from "../../../components/operations/OperationsShell";
import { apiFetch } from "../../../lib/api";
import { isAuthFailureMessage, type OpsEmployee, type OpsRates, type OpsSettings } from "../../../lib/ops";
import { money } from "../../../lib/opsFormat";
import { DENSITIES, DENSITY_LABELS, SERVICE_KEYS, SERVICE_LABELS, type Factors } from "../../../lib/quote";

export default function OperationsSettingsPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [rates, setRates] = useState<OpsRates | null>(null);
  const [factors, setFactors] = useState<Factors | null>(null);
  const [employees, setEmployees] = useState<OpsEmployee[]>([]);
  const [error, setError] = useState("");
  const [pendingRates, setPendingRates] = useState(false);
  const [pendingFactors, setPendingFactors] = useState(false);
  const [empName, setEmpName] = useState("");
  const [empPay, setEmpPay] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void apiFetch("/ops/settings", t)
      .then((data) => applySettings(data as OpsSettings))
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load rates";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
      })
      .finally(() => setLoading(false));
  }, [router]);

  function applySettings(data: OpsSettings) {
    setRates(data.rates);
    setFactors(data.factors as Factors);
    setEmployees(data.employees);
  }

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function saveRates(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !rates) return;
    setPendingRates(true);
    setError("");
    try {
      const saved = (await apiFetch("/ops/settings", token, {
        method: "PUT",
        body: JSON.stringify(rates)
      })) as OpsSettings;
      applySettings(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save rates.");
    } finally {
      setPendingRates(false);
    }
  }

  async function saveFactors(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !factors) return;
    setPendingFactors(true);
    setError("");
    try {
      const saved = (await apiFetch("/ops/factors", token, {
        method: "PUT",
        body: JSON.stringify({ factors })
      })) as OpsSettings;
      applySettings(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save factors.");
    } finally {
      setPendingFactors(false);
    }
  }

  async function addEmployee(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !empName.trim()) return;
    try {
      await apiFetch("/ops/employees", token, {
        method: "POST",
        body: JSON.stringify({ name: empName.trim(), pay_rate: empPay })
      });
      const refreshed = (await apiFetch("/ops/settings", token)) as OpsSettings;
      applySettings(refreshed);
      setEmpName("");
      setEmpPay(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add employee.");
    }
  }

  async function removeEmployee(id: string, name: string) {
    if (!token || !window.confirm(`Remove ${name}?`)) return;
    try {
      await apiFetch(`/ops/employees/${id}`, token, { method: "DELETE" });
      const refreshed = (await apiFetch("/ops/settings", token)) as OpsSettings;
      applySettings(refreshed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove employee.");
    }
  }

  if (!token) return <OperationsSignInPrompt />;

  return (
    <OperationsShell actions={operationsActions(logout)}>
      <div className="ct-dashboard" style={{ maxWidth: 960 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Operations rates
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Rates apply to the next quote. A settled job keeps the prices it was quoted at. Labor cost on a settlement
              uses average pay plus burden when you have employees, otherwise the billable rate times the labor target.
            </p>
          </div>
        </header>
        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
        {loading || !rates || !factors ? (
          <p className="ct-muted">Loading…</p>
        ) : (
          <>
            <form className="ct-card ct-card--padded ct-ops-form-grid" onSubmit={saveRates}>
              <label className="ct-label">
                Location name
                <input
                  className="ct-input"
                  value={rates.location_name}
                  onChange={(e) => setRates({ ...rates, location_name: e.target.value })}
                />
              </label>
              <label className="ct-label">
                Owner name
                <input
                  className="ct-input"
                  value={rates.owner_name}
                  onChange={(e) => setRates({ ...rates, owner_name: e.target.value })}
                />
              </label>
              <label className="ct-label">
                Billable hourly rate
                <input
                  className="ct-input"
                  type="number"
                  min={0}
                  step={1}
                  value={rates.bill_rate}
                  onChange={(e) => setRates({ ...rates, bill_rate: Number(e.target.value) || 0 })}
                />
              </label>
              <label className="ct-label">
                Minimum sale fee
                <input
                  className="ct-input"
                  type="number"
                  min={0}
                  step={1}
                  value={rates.minimum_fee}
                  onChange={(e) => setRates({ ...rates, minimum_fee: Number(e.target.value) || 0 })}
                />
              </label>
              <PercentField
                label="Labor target"
                value={rates.labor_target}
                onChange={(n) => setRates({ ...rates, labor_target: n })}
              />
              <PercentField
                label="Labor burden"
                value={rates.burden_rate}
                onChange={(n) => setRates({ ...rates, burden_rate: n })}
              />
              <PercentField
                label="Commission"
                value={rates.commission_rate}
                onChange={(n) => setRates({ ...rates, commission_rate: n })}
              />
              <PercentField
                label="Royalty"
                value={rates.royalty_rate}
                onChange={(n) => setRates({ ...rates, royalty_rate: n })}
              />
              <PercentField
                label="Deposit"
                value={rates.deposit_rate}
                onChange={(n) => setRates({ ...rates, deposit_rate: n })}
              />
              <PercentField
                label="Card fee"
                value={rates.card_fee_rate}
                onChange={(n) => setRates({ ...rates, card_fee_rate: n })}
              />
              <div className="ct-form-actions ct-ops-span-2">
                <button className="ct-btn ct-btn--primary" type="submit" disabled={pendingRates}>
                  {pendingRates ? "Saving…" : "Save rates"}
                </button>
              </div>
            </form>

            <section>
              <h2 className="ct-section-title">Pace factors</h2>
              <p className="ct-muted">
                Hours = cubic footage ÷ factor. A factor of 30 is 30 cubic feet per labor hour. Online auction starts
                from the estate-sale factors until this crew has its own pace.
              </p>
              <form className="ct-card ct-card--padded" onSubmit={saveFactors}>
                <div className="ct-table-wrap" style={{ marginBottom: "1rem" }}>
                  <table className="ct-table">
                    <thead>
                      <tr>
                        <th>Service</th>
                        {DENSITIES.map((density) => (
                          <th key={density}>{DENSITY_LABELS[density]}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {SERVICE_KEYS.map((service) => (
                        <tr key={service}>
                          <td>{SERVICE_LABELS[service]}</td>
                          {DENSITIES.map((density) => (
                            <td key={density}>
                              <input
                                className="ct-input"
                                type="number"
                                min={0}
                                step={1}
                                value={factors[service][density]}
                                onChange={(e) =>
                                  setFactors({
                                    ...factors,
                                    [service]: {
                                      ...factors[service],
                                      [density]: Number(e.target.value) || 0
                                    }
                                  })
                                }
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button className="ct-btn ct-btn--primary" type="submit" disabled={pendingFactors}>
                  {pendingFactors ? "Saving…" : "Save factors"}
                </button>
              </form>
            </section>

            <section>
              <h2 className="ct-section-title">Employees</h2>
              <p className="ct-muted">Pay rates are used for actual labor cost. They are not shown on the client proposal.</p>
              <form className="ct-row" onSubmit={addEmployee} style={{ marginBottom: "1rem" }}>
                <label className="ct-label">
                  Name
                  <input className="ct-input" value={empName} onChange={(e) => setEmpName(e.target.value)} />
                </label>
                <label className="ct-label">
                  Pay rate
                  <input
                    className="ct-input"
                    type="number"
                    min={0}
                    step="0.01"
                    value={empPay}
                    onChange={(e) => setEmpPay(Number(e.target.value) || 0)}
                  />
                </label>
                <button className="ct-btn ct-btn--primary" type="submit" style={{ alignSelf: "flex-end" }}>
                  Add
                </button>
              </form>
              <ul className="ct-ops-list">
                {employees.length === 0 ? <li className="ct-muted">No employees yet.</li> : null}
                {employees.map((employee) => (
                  <li key={employee.id} className="ct-ops-list__row">
                    <span>{employee.name}</span>
                    <span className="ct-row">
                      <span className="ct-ops-num">{money(employee.pay_rate)}/hr</span>
                      <button
                        type="button"
                        className="ct-btn ct-btn--danger-outline"
                        onClick={() => removeEmployee(employee.id, employee.name)}
                      >
                        Remove
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
    </OperationsShell>
  );
}

function PercentField({
  label,
  value,
  onChange
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <label className="ct-label">
      {label} %
      <input
        className="ct-input"
        type="number"
        min={0}
        step={0.1}
        value={Math.round(value * 1000) / 10}
        onChange={(e) => onChange(Math.max(0, (Number(e.target.value) || 0) / 100))}
      />
    </label>
  );
}
