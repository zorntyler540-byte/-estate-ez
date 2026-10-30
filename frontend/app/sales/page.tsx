"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { apiFetch } from "../../lib/api";
import { isOwnerRole, rememberAccountRole, storedAccountRole } from "../../lib/roles";

const SALE_STORAGE_KEY = "ct_selected_sale_id";

type SaleRow = {
  id: string;
  name: string;
  closed_at: string | null;
  created_at: string;
  ctseller_sale_id?: number | null;
  lot_count?: number;
  job_count?: number;
  active_job_count?: number;
};

type Pending =
  | { kind: "delete"; id: string }
  | { kind: "move"; id: string; targetId: string }
  | { kind: "close"; id: string }
  | { kind: "reopen"; id: string }
  | { kind: "rename"; id: string };

function saleLabel(sale: SaleRow | undefined, fallback: string): string {
  return sale?.name?.trim() || fallback;
}

export default function SalesManagePage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [owner, setOwner] = useState(false);
  const [sales, setSales] = useState<SaleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [showClosed, setShowClosed] = useState(true);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [moveTargetById, setMoveTargetById] = useState<Record<string, string>>({});
  const [renameById, setRenameById] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Pending | null>(null);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) router.replace("/");
  }, [router]);

  async function loadSales(t: string) {
    const rows = (await apiFetch("/sales", t)) as SaleRow[];
    setSales(Array.isArray(rows) ? rows : []);
  }

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void (async () => {
      try {
        const me = (await apiFetch("/auth/me", token)) as { role?: string };
        const role = String(me.role || storedAccountRole());
        rememberAccountRole(role);
        if (!cancelled) setOwner(isOwnerRole(role));
        await loadSales(token);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load sales");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sales
      .filter((sale) => (showClosed ? true : !sale.closed_at))
      .filter((sale) => (q ? sale.name.toLowerCase().includes(q) : true))
      .slice()
      .sort((a, b) => {
        const aClosed = a.closed_at ? 1 : 0;
        const bClosed = b.closed_at ? 1 : 0;
        if (aClosed !== bClosed) return aClosed - bClosed;
        return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
      });
  }, [query, sales, showClosed]);

  const openSales = sales.filter((sale) => !sale.closed_at);

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  function openInCatalog(saleId: string) {
    localStorage.setItem(SALE_STORAGE_KEY, saleId);
    router.push("/dashboard");
  }

  function rememberSale(nextId: string) {
    const stored = localStorage.getItem(SALE_STORAGE_KEY) || "";
    if (stored && stored !== nextId && sales.some((sale) => sale.id === stored)) return;
    if (nextId) localStorage.setItem(SALE_STORAGE_KEY, nextId);
  }

  async function createSale() {
    const name = newName.trim();
    if (!token || !name || !owner) return;
    setCreating(true);
    setError("");
    setNotice("");
    try {
      const created = (await apiFetch("/sales", token, {
        method: "POST",
        body: JSON.stringify({ name })
      })) as SaleRow;
      setNewName("");
      await loadSales(token);
      if (created?.id) rememberSale(created.id);
      setNotice(`Added “${created.name}”.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add the sale");
    } finally {
      setCreating(false);
    }
  }

  async function confirmPending() {
    if (!token || !pending || !owner) return;
    const sale = sales.find((row) => row.id === pending.id);
    if (!sale) return;
    setBusyId(sale.id);
    setError("");
    setNotice("");
    try {
      if (pending.kind === "delete") {
        await apiFetch(`/sales/${encodeURIComponent(sale.id)}/remove`, token, {
          method: "POST",
          body: JSON.stringify({ confirm_name: sale.name })
        });
        if (localStorage.getItem(SALE_STORAGE_KEY) === sale.id) {
          const next = sales.find((row) => row.id !== sale.id);
          if (next) localStorage.setItem(SALE_STORAGE_KEY, next.id);
          else localStorage.removeItem(SALE_STORAGE_KEY);
        }
        setNotice(`Deleted “${sale.name}” from Estate-EZ. CT Bids was not changed.`);
      } else if (pending.kind === "move") {
        const target = sales.find((row) => row.id === pending.targetId);
        const res = (await apiFetch(`/sales/${encodeURIComponent(pending.targetId)}/merge`, token, {
          method: "POST",
          body: JSON.stringify({ source_sale_id: sale.id })
        })) as { lots_moved?: number; duplicate_sku_count?: number };
        if (localStorage.getItem(SALE_STORAGE_KEY) === sale.id) {
          localStorage.setItem(SALE_STORAGE_KEY, pending.targetId);
        }
        const moved = res.lots_moved || 0;
        const dup =
          (res.duplicate_sku_count || 0) > 0
            ? ` ${res.duplicate_sku_count} lots now share a SKU.`
            : "";
        setNotice(
          `Moved ${moved} lot${moved === 1 ? "" : "s"} from “${sale.name}” into “${saleLabel(target, "the other sale")}”.${dup}`
        );
      } else if (pending.kind === "close" || pending.kind === "reopen") {
        const closing = pending.kind === "close";
        await apiFetch(`/sales/${encodeURIComponent(sale.id)}`, token, {
          method: "PATCH",
          body: JSON.stringify({ is_closed: closing })
        });
        setNotice(closing ? `Closed “${sale.name}”.` : `Reopened “${sale.name}”.`);
      } else if (pending.kind === "rename") {
        const name = (renameById[sale.id] || "").trim();
        if (!name) {
          setError("Enter a sale name.");
          return;
        }
        await apiFetch(`/sales/${encodeURIComponent(sale.id)}`, token, {
          method: "PATCH",
          body: JSON.stringify({ name })
        });
        setNotice(`Renamed the sale to “${name}”.`);
      }
      setPending(null);
      await loadSales(token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update the sale");
    } finally {
      setBusyId("");
    }
  }

  const shellActions = (
    <>
      <Link href="/dashboard" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
        Catalog
      </Link>
      <Link href="/account" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
        Account
      </Link>
      <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
        Log out
      </button>
    </>
  );

  if (!token) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Please sign in first.</p>
        </div>
      </main>
    );
  }

  return (
    <AppShell actions={shellActions}>
      <div className="ct-dashboard">
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Sales
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              Add, rename, close, move, or delete Estate-EZ catalogs. Moving sends every lot into another
              open sale and removes the old catalog. CT Bids auctions stay as they are.
            </p>
          </div>
        </header>

        {error ? <p className="ct-banner ct-banner--error">{error}</p> : null}
        {notice ? <p className="ct-banner ct-banner--success">{notice}</p> : null}
        {!owner && !loading ? (
          <p className="ct-banner ct-banner--warning">Only the account owner can change sales.</p>
        ) : null}

        <section className="ct-card ct-card--padded">
          <div className="ct-sale-manage__actions" style={{ justifyContent: "flex-start" }}>
            <input
              className="ct-input"
              style={{ maxWidth: 280, flex: "1 1 180px" }}
              placeholder="New sale name"
              value={newName}
              disabled={!owner || creating}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void createSale();
              }}
            />
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              disabled={!owner || creating || !newName.trim()}
              onClick={() => void createSale()}
            >
              {creating ? "Adding…" : "Add sale"}
            </button>
            <input
              className="ct-input"
              style={{ maxWidth: 240, flex: "1 1 160px" }}
              placeholder="Find a sale"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Find a sale"
            />
            <label className="ct-muted" style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}>
              <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
              Show closed
            </label>
          </div>
        </section>

        {loading ? <p className="ct-muted">Loading sales…</p> : null}
        {!loading && visible.length === 0 ? (
          <p className="ct-muted">No sales match this list.</p>
        ) : null}

        <div className="ct-sale-manage">
          {visible.map((sale) => {
            const lots = sale.lot_count || 0;
            const jobs = sale.job_count || 0;
            const active = sale.active_job_count || 0;
            const closed = Boolean(sale.closed_at);
            const targets = openSales.filter((row) => row.id !== sale.id);
            const targetId = moveTargetById[sale.id] || "";
            const rowPending = pending?.id === sale.id ? pending : null;
            const busy = busyId === sale.id;
            return (
              <article key={sale.id} className="ct-card ct-card--padded">
                <div className="ct-sale-manage__row">
                  <div>
                    <h2 className="ct-section-title" style={{ margin: 0 }}>
                      {sale.name}
                    </h2>
                    <p className="ct-muted" style={{ margin: "0.3rem 0 0", fontSize: "0.88rem" }}>
                      {closed ? "Closed" : "Open"}
                      {" · "}
                      {lots} lot{lots === 1 ? "" : "s"}
                      {" · "}
                      {jobs} job{jobs === 1 ? "" : "s"}
                      {active > 0 ? ` · ${active} still uploading` : ""}
                      {sale.ctseller_sale_id ? " · linked to CT Bids" : ""}
                    </p>
                  </div>
                  {owner ? (
                    <div className="ct-sale-manage__actions">
                      <button type="button" className="ct-btn" onClick={() => openInCatalog(sale.id)}>
                        Open
                      </button>
                      <button
                        type="button"
                        className="ct-btn"
                        disabled={busy}
                        onClick={() => {
                          setRenameById((prev) => ({ ...prev, [sale.id]: sale.name }));
                          setPending({ kind: "rename", id: sale.id });
                        }}
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        className="ct-btn"
                        disabled={busy}
                        onClick={() => setPending({ kind: closed ? "reopen" : "close", id: sale.id })}
                      >
                        {closed ? "Reopen" : "Close"}
                      </button>
                      {!closed && targets.length > 0 ? (
                        <>
                          <select
                            className="ct-input"
                            aria-label={`Move ${sale.name} into`}
                            value={targetId}
                            disabled={busy || active > 0}
                            onChange={(e) =>
                              setMoveTargetById((prev) => ({ ...prev, [sale.id]: e.target.value }))
                            }
                          >
                            <option value="">Move into…</option>
                            {targets.map((row) => (
                              <option key={row.id} value={row.id}>
                                {row.name}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className="ct-btn"
                            disabled={busy || !targetId || active > 0}
                            onClick={() => setPending({ kind: "move", id: sale.id, targetId })}
                          >
                            Move
                          </button>
                        </>
                      ) : null}
                      <button
                        type="button"
                        className="ct-btn ct-btn--danger-outline"
                        disabled={busy || active > 0}
                        onClick={() => setPending({ kind: "delete", id: sale.id })}
                      >
                        Delete
                      </button>
                    </div>
                  ) : (
                    <button type="button" className="ct-btn" onClick={() => openInCatalog(sale.id)}>
                      Open
                    </button>
                  )}
                </div>
                {active > 0 && owner ? (
                  <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.85rem" }}>
                    Wait until uploads finish before moving or deleting this sale.
                  </p>
                ) : null}
                {rowPending ? (
                  <div className="ct-banner ct-banner--warning" style={{ marginTop: "0.75rem" }}>
                    {rowPending.kind === "rename" ? (
                      <label className="ct-label" style={{ display: "block", marginBottom: "0.55rem" }}>
                        Sale name
                        <input
                          className="ct-input"
                          style={{ display: "block", marginTop: 6, maxWidth: 360 }}
                          value={renameById[sale.id] ?? sale.name}
                          onChange={(e) =>
                            setRenameById((prev) => ({ ...prev, [sale.id]: e.target.value }))
                          }
                        />
                      </label>
                    ) : (
                      <p style={{ margin: "0 0 0.55rem" }}>{confirmCopy(rowPending, sale, sales)}</p>
                    )}
                    <div className="ct-sale-manage__actions" style={{ justifyContent: "flex-start" }}>
                      <button
                        type="button"
                        className={rowPending.kind === "delete" ? "ct-btn ct-btn--danger" : "ct-btn ct-btn--primary"}
                        disabled={busy}
                        onClick={() => void confirmPending()}
                      >
                        {busy ? "Saving…" : confirmLabel(rowPending.kind)}
                      </button>
                      <button type="button" className="ct-btn" disabled={busy} onClick={() => setPending(null)}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      </div>
    </AppShell>
  );
}

function confirmLabel(kind: Pending["kind"]): string {
  if (kind === "delete") return "Delete catalog";
  if (kind === "move") return "Move lots";
  if (kind === "close") return "Close sale";
  if (kind === "reopen") return "Reopen sale";
  return "Save name";
}

function confirmCopy(pending: Pending, sale: SaleRow, sales: SaleRow[]): string {
  const lots = sale.lot_count || 0;
  if (pending.kind === "delete") {
    const linked = sale.ctseller_sale_id
      ? " This catalog is linked to a CT Bids auction. If that auction is still open, Sync can add the name back — Close it instead if you only want it off the working list."
      : "";
    return `Delete “${sale.name}” and its ${lots} lot${lots === 1 ? "" : "s"} from Estate-EZ? Photos on this website go with it. CT Bids listings stay up.${linked}`;
  }
  if (pending.kind === "move") {
    const target = sales.find((row) => row.id === pending.targetId);
    return `Move ${lots} lot${lots === 1 ? "" : "s"} from “${sale.name}” into “${saleLabel(target, "the other sale")}”, then remove “${sale.name}” from this list? CT Bids auctions stay separate.`;
  }
  if (pending.kind === "close") {
    return `Close “${sale.name}”? It stays in this list and drops off the open catalog picker.`;
  }
  if (pending.kind === "reopen") {
    return `Reopen “${sale.name}” so it can take new photos again?`;
  }
  return "";
}
