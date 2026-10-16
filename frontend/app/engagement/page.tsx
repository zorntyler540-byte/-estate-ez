"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { LotThumbnail } from "../../components/LotThumbnail";
import { apiFetch } from "../../lib/api";

type SaleSummary = {
  ctseller_sale_id: number;
  sale_title: string;
  auction_url: string;
  sale_ends_at?: string | null;
  captured_at?: string | null;
  item_count: number;
  rescue: number;
  push: number;
  protect: number;
  let_ride: number;
};

type QueueItem = {
  sku: string;
  title: string;
  views: number;
  watchers: number;
  bids: number;
  price: number;
  reserve: number;
  item_url: string;
  bucket: string;
  engagement_score: string;
  momentum: string;
  notes: string;
  late_surge: string;
  push_rank?: number | null;
  lot_id?: string | null;
  has_thumbnail: boolean;
  photo_ids: string[];
  ctseller_item_id?: number | null;
};

type SaleDetail = SaleSummary & { items: QueueItem[] };

type CalendarPost = {
  order: number;
  post_date: string;
  local_time: string;
  zone: string;
  platform: string;
  hook: string;
  post_type: string;
  sku: string;
  title: string;
  item_url: string;
  lot_id?: string | null;
  photo_ids: string[];
  paste_copy: string;
  status: string;
  preflight: string;
};

const FILTERS = ["Push", "Rescue", "Protect", "Let Ride"] as const;

function money(value: number): string {
  const rounded = Math.round(value);
  if (Math.abs(value - rounded) < 0.001) return `$${rounded}`;
  return `$${value.toFixed(2)}`;
}

function when(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export default function EngagementPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [sales, setSales] = useState<SaleSummary[]>([]);
  const [saleId, setSaleId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SaleDetail | null>(null);
  const [posts, setPosts] = useState<CalendarPost[]>([]);
  const [bucket, setBucket] = useState<(typeof FILTERS)[number]>("Push");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState<number | null>(null);

  const loadSale = useCallback(
    async (t: string, id: number) => {
      const [sale, calendar] = await Promise.all([
        apiFetch(`/engagement/sales/${id}`, t) as Promise<SaleDetail>,
        apiFetch(`/engagement/sales/${id}/calendar`, t) as Promise<CalendarPost[]>
      ]);
      setDetail(sale);
      setPosts(calendar);
    },
    []
  );

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    void apiFetch("/engagement/sales", t)
      .then((data) => {
        const rows = data as SaleSummary[];
        setSales(rows);
        if (rows[0]) setSaleId(rows[0].ctseller_sale_id);
        else setLoading(false);
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load the Push queue";
        if (msg.toLowerCase().includes("401") || msg.toLowerCase().includes("authentication")) {
          localStorage.removeItem("token");
          router.replace("/");
          return;
        }
        setError(msg);
        setLoading(false);
      });
  }, [router]);

  useEffect(() => {
    if (!token || saleId == null) return;
    setLoading(true);
    void loadSale(token, saleId)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load that sale"))
      .finally(() => setLoading(false));
  }, [token, saleId, loadSale]);

  const visible = useMemo(
    () => (detail?.items || []).filter((item) => item.bucket === bucket),
    [detail, bucket]
  );

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function copyPost(post: CalendarPost) {
    try {
      await navigator.clipboard.writeText(post.paste_copy);
      setCopied(post.order);
    } catch {
      setCopied(null);
    }
  }

  if (!token) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Please sign in first.</p>
          <p style={{ margin: "1rem 0 0" }}>
            <Link href="/">Go to sign in</Link>
          </p>
        </div>
      </main>
    );
  }

  return (
    <AppShell
      actions={
        <>
          <Link href="/dashboard" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
            Dashboard
          </Link>
          <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
            Log out
          </button>
        </>
      }
    >
      <div className="ct-dashboard" style={{ maxWidth: 1200 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Push queue
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              The Watcher sends views, watchers, bids, price, and the lot link after each refresh.
              Staff still post. Nothing here publishes to Facebook, Instagram, or TikTok.
            </p>
          </div>
        </header>

        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}

        {sales.length > 0 ? (
          <label className="ct-field" style={{ maxWidth: 420, display: "block", marginBottom: "1rem" }}>
            <span className="ct-muted">CT Bids sale</span>
            <select
              value={saleId ?? ""}
              onChange={(e) => setSaleId(Number(e.target.value))}
              style={{ display: "block", marginTop: "0.35rem", width: "100%" }}
            >
              {sales.map((sale) => (
                <option key={sale.ctseller_sale_id} value={sale.ctseller_sale_id}>
                  {sale.sale_title || `Sale ${sale.ctseller_sale_id}`} · {sale.push} Push
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {loading ? <p className="ct-muted">Loading…</p> : null}

        {!loading && sales.length === 0 ? (
          <section className="ct-card ct-card--padded">
            <p style={{ margin: 0 }}>
              No live snapshot yet. Sign in to Estate-EZ from the Watcher, open the sale, and refresh.
              Counts show up here after that refresh.
            </p>
          </section>
        ) : null}

        {detail ? (
          <>
            <p className="ct-muted" style={{ marginTop: 0 }}>
              Snapshot {when(detail.captured_at)}
              {detail.sale_ends_at ? ` · closes ${when(detail.sale_ends_at)}` : ""}
              {detail.auction_url ? (
                <>
                  {" · "}
                  <a href={detail.auction_url} target="_blank" rel="noreferrer">
                    Auction page
                  </a>
                </>
              ) : null}
            </p>
            <section className="ct-dash-stats" aria-label="Engagement buckets" style={{ marginBottom: "1rem" }}>
              {FILTERS.map((name) => {
                const count =
                  name === "Push"
                    ? detail.push
                    : name === "Rescue"
                      ? detail.rescue
                      : name === "Protect"
                        ? detail.protect
                        : detail.let_ride;
                return (
                  <button
                    key={name}
                    type="button"
                    className="ct-stat-card"
                    onClick={() => setBucket(name)}
                    style={{
                      textAlign: "left",
                      cursor: "pointer",
                      outline: bucket === name ? "2px solid var(--ct-primary)" : undefined
                    }}
                  >
                    <span className="ct-stat-card__value">{count}</span>
                    <span className="ct-stat-card__label">{name}</span>
                  </button>
                );
              })}
            </section>

            <div className="ct-table-wrap">
              <table className="ct-table">
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Photo</th>
                    <th>Lot</th>
                    <th className="ct-table__num">Views</th>
                    <th className="ct-table__num">Watchers</th>
                    <th className="ct-table__num">Bids</th>
                    <th className="ct-table__num">Price</th>
                    <th>Note</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.length === 0 ? (
                    <tr>
                      <td colSpan={8}>No {bucket} lots in this snapshot.</td>
                    </tr>
                  ) : (
                    visible.map((item) => (
                      <tr key={`${item.ctseller_item_id}-${item.sku}`}>
                        <td>{item.push_rank ?? "—"}</td>
                        <td>
                          {item.lot_id ? (
                            <LotThumbnail
                              lotId={item.lot_id}
                              token={token}
                              hasThumbnail={item.has_thumbnail}
                              size={56}
                              maxPx={220}
                            />
                          ) : (
                            <span className="ct-muted">No catalog photo</span>
                          )}
                        </td>
                        <td>
                          <div>
                            {item.lot_id ? (
                              <Link href={`/lots/${item.lot_id}`}>{item.sku || "Lot"}</Link>
                            ) : (
                              <strong>{item.sku || "No SKU"}</strong>
                            )}
                          </div>
                          <div>{item.title}</div>
                          {item.item_url ? (
                            <a href={item.item_url} target="_blank" rel="noreferrer">
                              Listing
                            </a>
                          ) : null}
                          {!item.lot_id ? (
                            <div className="ct-muted">Cataloged lots match on CT Bids item id, then SKU.</div>
                          ) : null}
                        </td>
                        <td className="ct-table__num">{item.views}</td>
                        <td className="ct-table__num">{item.watchers}</td>
                        <td className="ct-table__num">{item.bids}</td>
                        <td className="ct-table__num">
                          {money(item.price)}
                          <div className="ct-muted">reserve {money(item.reserve)}</div>
                        </td>
                        <td>
                          {item.engagement_score} · {item.momentum}
                          <div>{item.notes}</div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <h2 className="ct-page-title" style={{ fontSize: "1.25rem", marginTop: "1.5rem" }}>
              Social calendar
            </h2>
            <p className="ct-muted">
              Planned posts for Push lots only, from the snapshot through the close. Confirm the lot is
              still open, then paste the copy yourself.
            </p>
            {posts.length === 0 ? (
              <p className="ct-muted">No Push lots, so there is nothing to schedule.</p>
            ) : (
              <div className="ct-table-wrap">
                <table className="ct-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Platform</th>
                      <th>Lot</th>
                      <th>Copy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {posts.map((post) => (
                      <tr key={post.order}>
                        <td>
                          {post.post_date}
                          <div>
                            {post.local_time} {post.zone}
                          </div>
                          <div className="ct-muted">{post.status}</div>
                        </td>
                        <td>
                          {post.platform}
                          <div className="ct-muted">{post.hook}</div>
                          <div className="ct-muted">{post.post_type}</div>
                        </td>
                        <td>
                          {post.lot_id ? (
                            <Link href={`/lots/${post.lot_id}`}>{post.sku}</Link>
                          ) : (
                            post.sku
                          )}
                          <div>{post.title}</div>
                          {post.item_url ? (
                            <a href={post.item_url} target="_blank" rel="noreferrer">
                              Listing
                            </a>
                          ) : null}
                        </td>
                        <td>
                          <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>
                            {post.paste_copy}
                          </pre>
                          <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void copyPost(post)}>
                            {copied === post.order ? "Copied" : "Copy"}
                          </button>
                          <div className="ct-muted">{post.preflight}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
