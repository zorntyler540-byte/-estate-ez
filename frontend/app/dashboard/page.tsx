"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { apiFetch, API_BASE } from "../../lib/api";

type Lot = {
  id: string;
  sku: string | null;
  auction_title: string;
  starting_price: number | null;
  condition_text: string;
  photo_count: number;
  created_at: string;
};

type Job = {
  id: string;
  status: string;
  input_folder: string;
  created_at: string;
  error_message?: string;
};

export default function DashboardPage() {
  const [token, setToken] = useState<string>("");
  const [lots, setLots] = useState<Lot[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [folder, setFolder] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    Promise.all([apiFetch("/lots", t), apiFetch("/jobs", t)])
      .then(([lotsData, jobsData]) => {
        setLots(lotsData);
        setJobs(jobsData);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Load failed"))
      .finally(() => setLoading(false));
  }, []);

  async function enqueueJob() {
    setError("");
    try {
      const created = await apiFetch("/jobs", token, {
        method: "POST",
        body: JSON.stringify({ input_folder: folder })
      });
      setJobs((prev) => [created, ...prev]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create job");
    }
  }

  async function runJob(jobId: string) {
    setError("");
    try {
      const updated = await apiFetch(`/jobs/${jobId}/run`, token, { method: "POST" });
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
      const freshLots = await apiFetch("/lots", token);
      setLots(freshLots);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed");
    }
  }

  const exportLinks = useMemo(
    () => ({
      markdown: `${API_BASE}/exports/markdown`,
      json: `${API_BASE}/exports/json`,
      csv: `${API_BASE}/exports/csv`
    }),
    []
  );

  if (!token) return <p>Please login first.</p>;
  if (loading) return <p>Loading dashboard...</p>;

  return (
    <main>
      <h1>Lots Dashboard</h1>
      <div style={{ marginBottom: 16 }}>
        <h3>Run photo processing job</h3>
        <input
          style={{ width: 480 }}
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          placeholder="Folder path on server, e.g. C:\photos\batch1"
        />
        <button style={{ marginLeft: 8 }} onClick={enqueueJob} disabled={!folder}>
          Queue job
        </button>
      </div>

      <h3>Jobs</h3>
      <ul>
        {jobs.map((job) => (
          <li key={job.id}>
            <strong>{job.status}</strong> - {job.input_folder}{" "}
            {job.status === "queued" && <button onClick={() => runJob(job.id)}>Run now</button>}
            {job.error_message ? ` (error: ${job.error_message})` : ""}
          </li>
        ))}
      </ul>

      <h3>Lots ({lots.length})</h3>
      <table cellPadding={8} cellSpacing={0} border={1}>
        <thead>
          <tr>
            <th>SKU</th>
            <th>Auction Title</th>
            <th>Price</th>
            <th>Condition</th>
            <th>Photos</th>
            <th>Edit</th>
          </tr>
        </thead>
        <tbody>
          {lots.map((lot) => (
            <tr key={lot.id}>
              <td>{lot.sku || "-"}</td>
              <td>{lot.auction_title}</td>
              <td>{lot.starting_price ?? "-"}</td>
              <td>{lot.condition_text}</td>
              <td>{lot.photo_count}</td>
              <td>
                <Link href={`/lots/${lot.id}`}>Open</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Exports</h3>
      <p>
        Use your Bearer token in API client for secure downloads:
        <br />
        <code>{exportLinks.markdown}</code>
        <br />
        <code>{exportLinks.json}</code>
        <br />
        <code>{exportLinks.csv}</code>
      </p>
      {error && <p style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{error}</p>}
    </main>
  );
}

