"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { JobFields } from "../../../../components/operations/JobFields";
import { OperationsShell, OperationsSignInPrompt, operationsActions } from "../../../../components/operations/OperationsShell";
import { QuoteSummary } from "../../../../components/operations/QuoteSummary";
import { SettlementForm, emptySettlementDraft } from "../../../../components/operations/SettlementForm";
import { apiFetch } from "../../../../lib/api";
import { isAuthFailureMessage, jobToWrite, type OpsJob, type OpsJobWrite, type OpsSaleOption, type OpsSettings } from "../../../../lib/ops";
import { DENSITY_LABELS, SERVICE_LABELS } from "../../../../lib/quote";

export default function OperationsJobPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const jobId = params?.id || "";
  const [token, setToken] = useState("");
  const [job, setJob] = useState<OpsJob | null>(null);
  const [form, setForm] = useState<OpsJobWrite | null>(null);
  const [sales, setSales] = useState<OpsSaleOption[]>([]);
  const [laborTarget, setLaborTarget] = useState(0.33);
  const [settlementDraft, setSettlementDraft] = useState(emptySettlementDraft(null));
  const [error, setError] = useState("");
  const [settleError, setSettleError] = useState("");
  const [pending, setPending] = useState(false);
  const [settlePending, setSettlePending] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (t: string) => {
      const [jobData, settings, saleRows] = await Promise.all([
        apiFetch(`/ops/jobs/${jobId}`, t) as Promise<OpsJob>,
        apiFetch("/ops/settings", t) as Promise<OpsSettings>,
        apiFetch("/sales", t) as Promise<OpsSaleOption[]>
      ]);
      setJob(jobData);
      setForm(jobToWrite(jobData));
      setLaborTarget(settings.rates.labor_target);
      setSettlementDraft(emptySettlementDraft(jobData.settlement));
      setSales(saleRows);
    },
    [jobId]
  );

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void load(t)
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load job";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
      })
      .finally(() => setLoading(false));
  }, [load, router]);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function saveJob(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !form) return;
    setPending(true);
    setError("");
    try {
      const updated = (await apiFetch(`/ops/jobs/${jobId}`, token, {
        method: "PUT",
        body: JSON.stringify(form)
      })) as OpsJob;
      setJob(updated);
      setForm(jobToWrite(updated));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the job.");
    } finally {
      setPending(false);
    }
  }

  async function saveSettlement() {
    if (!token) return;
    setSettlePending(true);
    setSettleError("");
    try {
      const updated = (await apiFetch(`/ops/jobs/${jobId}/settlement`, token, {
        method: "PUT",
        body: JSON.stringify(settlementDraft)
      })) as OpsJob;
      setJob(updated);
      setForm(jobToWrite(updated));
      setSettlementDraft(emptySettlementDraft(updated.settlement));
    } catch (err) {
      setSettleError(err instanceof Error ? err.message : "Could not save the settlement.");
    } finally {
      setSettlePending(false);
    }
  }

  async function reopen() {
    if (!token || !window.confirm("Reopen this job and remove the settlement?")) return;
    try {
      const updated = (await apiFetch(`/ops/jobs/${jobId}/settlement`, token, { method: "DELETE" })) as OpsJob;
      setJob(updated);
      setForm(jobToWrite(updated));
      setSettlementDraft(emptySettlementDraft(null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reopen the job.");
    }
  }

  async function remove() {
    if (!token || !window.confirm("Delete this job?")) return;
    try {
      await apiFetch(`/ops/jobs/${jobId}`, token, { method: "DELETE" });
      router.replace("/operations/jobs");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the job.");
    }
  }

  async function applySuggestedFactor() {
    if (!token || !job?.suggested_factor || !job.suggested_service) return;
    try {
      await apiFetch("/ops/factors/apply", token, {
        method: "POST",
        body: JSON.stringify({
          service: job.suggested_service,
          density: job.density,
          factor: job.suggested_factor
        })
      });
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the factor.");
    }
  }

  if (!token) return <OperationsSignInPrompt />;

  const locked = job?.status === "settled";
  const primaryLabel =
    job?.suggested_service && job.suggested_service in SERVICE_LABELS
      ? SERVICE_LABELS[job.suggested_service as keyof typeof SERVICE_LABELS]
      : job?.suggested_service;

  return (
    <OperationsShell actions={operationsActions(logout)}>
      <div className="ct-dashboard" style={{ maxWidth: 1100 }}>
        {loading ? <p className="ct-muted">Loading…</p> : null}
        {!loading && !job ? (
          <div className="ct-banner ct-banner--error">{error || "Job not found."}</div>
        ) : null}
        {job && form ? (
          <>
            <header className="ct-page-header">
              <div>
                <p className="ct-ops-kicker">Job</p>
                <h1 className="ct-page-title">{job.client_name}</h1>
              </div>
              <Link href={`/operations/jobs/${job.id}/proposal`} className="ct-btn ct-btn--ghost">
                Proposal
              </Link>
            </header>
            {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
            <div className="ct-ops-job-layout">
              <form className="ct-card ct-card--padded" onSubmit={saveJob}>
                {locked ? (
                  <p className="ct-muted">
                    This job is settled. Contact details still save. Reopen the settlement to change the quote.
                  </p>
                ) : null}
                <JobFields
                  value={form}
                  onChange={setForm}
                  sales={sales}
                  locked={locked}
                  showStatus
                  saleLotCount={job.sale_lot_count}
                />
                <div className="ct-form-actions">
                  <button className="ct-btn ct-btn--primary" type="submit" disabled={pending}>
                    {pending ? "Saving…" : "Save job"}
                  </button>
                </div>
              </form>
              {job.quote ? (
                <QuoteSummary quote={job.quote} laborTarget={laborTarget} settlement={job.settlement_result} />
              ) : (
                <aside className="ct-card ct-card--padded ct-muted">Save the job to build the quote.</aside>
              )}
            </div>
            {job.quote ? (
              <div className="ct-table-wrap">
                <table className="ct-table">
                  <thead>
                    <tr>
                      <th>Line</th>
                      <th className="ct-table__num">Hours</th>
                      <th className="ct-table__num">Price</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.quote.lines.map((line) => (
                      <tr key={line.key}>
                        <td>
                          {line.label}
                          {line.billable ? null : <span className="ct-muted"> Inside the sale fee</span>}
                        </td>
                        <td className="ct-table__num">{line.hours.toLocaleString("en-US", { maximumFractionDigits: 1 })}</td>
                        <td className="ct-table__num">
                          {line.billable
                            ? line.price.toLocaleString("en-US", { style: "currency", currency: "USD" })
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {job.quote ? (
              <SettlementForm
                draft={settlementDraft}
                onChange={setSettlementDraft}
                onSave={saveSettlement}
                pending={settlePending}
                error={settleError}
                settlement={job.settlement}
                result={job.settlement_result}
                laborTarget={laborTarget}
              />
            ) : null}
            {job.suggested_factor && primaryLabel ? (
              <section className="ct-card ct-card--padded">
                <p>
                  This job worked at about {job.suggested_factor} cubic feet per hour. Applying it updates {primaryLabel} /{" "}
                  {DENSITY_LABELS[job.density]} for the next quote. Hours are for the whole job, so this is a blended pace.
                </p>
                <button type="button" className="ct-btn ct-btn--ghost" onClick={applySuggestedFactor}>
                  Use this factor
                </button>
              </section>
            ) : null}
            <div className="ct-form-actions">
              {job.settlement ? (
                <button type="button" className="ct-btn ct-btn--danger-outline" onClick={reopen}>
                  Reopen settlement
                </button>
              ) : null}
              <button type="button" className="ct-btn ct-btn--danger-outline" onClick={remove}>
                Delete job
              </button>
            </div>
          </>
        ) : null}
      </div>
    </OperationsShell>
  );
}
