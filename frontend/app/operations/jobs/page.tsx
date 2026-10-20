"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { OperationsShell, OperationsSignInPrompt, operationsActions } from "../../../components/operations/OperationsShell";
import { apiFetch } from "../../../lib/api";
import { OPS_STATUS_LABELS, isAuthFailureMessage, type OpsJob } from "../../../lib/ops";
import { money } from "../../../lib/opsFormat";
import { LIQUIDATION_LABELS } from "../../../lib/quote";

export default function OperationsJobsPage() {
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
        const msg = e instanceof Error ? e.message : "Could not load jobs";
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

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  if (!token) return <OperationsSignInPrompt />;

  return (
    <OperationsShell actions={operationsActions(logout)}>
      <div className="ct-dashboard" style={{ maxWidth: 960 }}>
        <header className="ct-page-header">
          <h1 className="ct-page-title">Jobs</h1>
          <Link href="/operations/jobs/new" className="ct-btn ct-btn--primary">
            New job
          </Link>
        </header>
        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
        {loading ? <p className="ct-muted">Loading…</p> : null}
        {!loading && jobs.length === 0 ? (
          <p className="ct-muted">No jobs yet.</p>
        ) : (
          <div className="ct-table-wrap">
            <table className="ct-table">
              <thead>
                <tr>
                  <th>Client</th>
                  <th>Status</th>
                  <th>Sale</th>
                  <th className="ct-table__num">Quote</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <Link href={`/operations/jobs/${job.id}`}>{job.client_name}</Link>
                      <span className="ct-muted" style={{ display: "block" }}>
                        {job.address || job.zip || "—"}
                      </span>
                    </td>
                    <td>{OPS_STATUS_LABELS[job.status]}</td>
                    <td>{LIQUIDATION_LABELS[job.liquidation]}</td>
                    <td className="ct-table__num">{job.client_total != null ? money(job.client_total) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </OperationsShell>
  );
}
