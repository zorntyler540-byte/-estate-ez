"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { JobFields } from "../../../../components/operations/JobFields";
import { OperationsShell, OperationsSignInPrompt, operationsActions } from "../../../../components/operations/OperationsShell";
import { apiFetch } from "../../../../lib/api";
import { emptyJobWrite, isAuthFailureMessage, type OpsJob, type OpsJobWrite, type OpsSaleOption } from "../../../../lib/ops";

export default function NewOperationsJobPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [form, setForm] = useState<OpsJobWrite>(emptyJobWrite());
  const [sales, setSales] = useState<OpsSaleOption[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) return;
    void apiFetch("/sales", t)
      .then((data) => setSales(data as OpsSaleOption[]))
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load sales";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
        }
      });
  }, [router]);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    if (!form.client_name.trim()) {
      setError("Enter the client name.");
      return;
    }
    setPending(true);
    setError("");
    try {
      const created = (await apiFetch("/ops/jobs", token, {
        method: "POST",
        body: JSON.stringify(form)
      })) as OpsJob;
      router.replace(`/operations/jobs/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the job.");
      setPending(false);
    }
  }

  if (!token) return <OperationsSignInPrompt />;

  return (
    <OperationsShell actions={operationsActions(logout)}>
      <div className="ct-dashboard" style={{ maxWidth: 760 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              New job
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Square footage and density drive the hours. The sale fee is figured after you enter expected proceeds.
            </p>
          </div>
        </header>
        <form className="ct-card ct-card--padded" onSubmit={onSubmit}>
          {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
          <JobFields value={form} onChange={setForm} sales={sales} />
          <div className="ct-form-actions">
            <button className="ct-btn ct-btn--primary" type="submit" disabled={pending}>
              {pending ? "Saving…" : "Create job"}
            </button>
          </div>
        </form>
      </div>
    </OperationsShell>
  );
}
