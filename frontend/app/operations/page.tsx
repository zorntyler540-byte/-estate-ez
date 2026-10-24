"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { OperationsShell, OperationsSignInPrompt, operationsActions } from "../../components/operations/OperationsShell";
import { apiFetch } from "../../lib/api";
import { OPS_STATUS_LABELS, isAuthFailureMessage, type OpsJob } from "../../lib/ops";
import { money, monthLabel, percent, thisMonth } from "../../lib/opsFormat";

export default function OperationsHomePage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [jobs, setJobs] = useState<OpsJob[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void apiFetch("/ops/jobs", t)
      .then((data) => setJobs(data as OpsJob[]))
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load operations";
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

  const month = thisMonth();
  const open = useMemo(() => jobs.filter((job) => job.status !== "settled"), [jobs]);
  const monthSettled = useMemo(
    () => jobs.filter((job) => job.settled_on && job.settled_on.startsWith(month) && job.settlement_result),
    [jobs, month]
  );
  const invoiced = monthSettled.reduce((sum, job) => sum + (job.settlement_result?.invoiced || 0), 0);
  const labor = monthSettled.reduce((sum, job) => sum + (job.settlement_result?.laborCost || 0), 0);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  if (!token) return <OperationsSignInPrompt />;

  return (
    <OperationsShell actions={operationsActions(logout)}>
      <div className="ct-dashboard" style={{ maxWidth: 960 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Operations
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Quotes, proposals, and settlements for franchise jobs. Catalog photos stay on the Dashboard.
            </p>
          </div>
          <Link href="/operations/jobs/new" className="ct-btn ct-btn--primary">
            New job
          </Link>
        </header>
        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
        {loading ? <p className="ct-muted">Loading…</p> : null}
        <div className="ct-ops-tiles">
          <Tile label="Open jobs" value={String(open.length)} />
          <Tile label={`${monthLabel(month)} invoiced`} value={money(invoiced)} />
          <Tile label="Labor this month" value={invoiced > 0 ? percent(labor / invoiced) : "—"} />
        </div>
        <h2 className="ct-section-title">Open jobs</h2>
        {open.length === 0 && !loading ? (
          <p className="ct-muted">No open jobs. Start with the house, the services, and a cubic footage.</p>
        ) : (
          <ul className="ct-ops-list">
            {open.map((job) => (
              <li key={job.id}>
                <Link href={`/operations/jobs/${job.id}`} className="ct-ops-list__row">
                  <span>
                    <span className="ct-ops-list__title">{job.client_name}</span>
                    <span className="ct-muted">{job.address || "No address yet"}</span>
                  </span>
                  <span className="ct-ops-list__meta">
                    <span>{OPS_STATUS_LABELS[job.status]}</span>
                    <span className="ct-ops-num ct-muted">{job.client_total != null ? money(job.client_total) : "No quote"}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
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
