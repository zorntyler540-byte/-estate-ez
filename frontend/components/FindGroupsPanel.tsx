"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AudienceHuntProgress,
  audienceHuntPhaseLabel
} from "./AudienceHuntProgress";
import { apiFetch } from "../lib/api";

type HuntLot = {
  lot_id: string;
  sku?: string | null;
  status: string;
  phase?: string | null;
};

type HuntStatus = {
  active: boolean;
  total: number;
  done: number;
  current_lot_id?: string | null;
  current_sku?: string | null;
  current_phase?: string | null;
  lots?: HuntLot[];
};

type GroupHit = {
  name: string;
  url?: string;
  why?: string;
  kind?: string;
  lot_id?: string | null;
  sku?: string | null;
};

type GroupSearch = {
  query: string;
  url: string;
  why?: string;
  kind?: string;
};

type GroupsPayload = {
  sale_id: string;
  sale_name?: string;
  location?: string;
  locations?: string[];
  groups: GroupHit[];
  searches: GroupSearch[];
  hunted_lots?: number;
  lot_count?: number;
  tips?: string;
  hunt?: HuntStatus;
};

type Props = {
  token: string;
  saleId: string;
  saleName?: string;
  isOwner: boolean;
};

function huntBarPercent(st: HuntStatus): number {
  if (st.total < 1) return 0;
  const frac =
    st.current_phase === "structuring"
      ? 0.75
      : st.current_phase === "searching"
        ? 0.4
        : st.active
          ? 0.08
          : 0;
  return Math.min(100, Math.round(((st.done + frac) / st.total) * 100));
}

function GroupList({ rows, empty }: { rows: GroupHit[]; empty: string }) {
  if (!rows.length) {
    return <p className="ct-muted">{empty}</p>;
  }
  return (
    <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.15rem" }}>
      {rows.map((g, i) => (
        <li key={`${g.name}-${g.url || i}`} style={{ marginBottom: "0.45rem" }}>
          {g.url ? (
            <a href={g.url} target="_blank" rel="noopener noreferrer">
              {g.name}
            </a>
          ) : (
            <strong>{g.name}</strong>
          )}
          {g.sku ? <span className="ct-muted"> · {g.sku}</span> : null}
          {g.why ? <span className="ct-muted"> — {g.why}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function SearchList({ rows }: { rows: GroupSearch[] }) {
  if (!rows.length) return null;
  return (
    <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.15rem" }}>
      {rows.map((row) => (
        <li key={row.query} style={{ marginBottom: "0.45rem" }}>
          <a href={row.url} target="_blank" rel="noopener noreferrer">
            {row.query}
          </a>
          {row.why ? <span className="ct-muted"> — {row.why}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function FindGroupsPanel({ token, saleId, saleName, isOwner }: Props) {
  const [data, setData] = useState<GroupsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [force, setForce] = useState(false);
  const [kick, setKick] = useState(0);

  const load = useCallback(async () => {
    if (!token || !saleId) {
      setData(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const row = (await apiFetch(
        `/sales/${encodeURIComponent(saleId)}/facebook-groups`,
        token
      )) as GroupsPayload;
      setData(row);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load groups");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [token, saleId]);

  useEffect(() => {
    void load();
  }, [load, kick]);

  const hunt = data?.hunt;
  useEffect(() => {
    if (!token || !saleId || !hunt?.active) return;
    const timer = window.setTimeout(() => setKick((n) => n + 1), 1500);
    return () => window.clearTimeout(timer);
  }, [token, saleId, hunt?.active, hunt?.done, hunt?.current_phase, kick]);

  async function findGroups() {
    if (!token || !saleId) return;
    if (
      !window.confirm(
        force
          ? "Find national US collector groups for lots in this sale, including everyday household lots?\n\nPrimary: US-wide hobby/interest groups (ships nationwide). Local city groups are extra only. Uses Gemini Pro + Google Search (not the Graph API). Does not scrape members or auto-post."
          : "Find national US collector groups for nicer lots already in this sale?\n\nPrimary: US-wide hobby/interest groups (ships nationwide). Local city groups are extra only — city is not a filter. Everyday household lots are skipped. Already-hunted lots are skipped. Uses Gemini Pro + Google Search (not the Graph API). Does not recatalog photos or auto-post."
      )
    ) {
      return;
    }
    setBusy(true);
    setError("");
    setNotice("Queueing group hunt…");
    try {
      const res = (await apiFetch(`/sales/${encodeURIComponent(saleId)}/audience-hunt`, token, {
        method: "POST",
        body: JSON.stringify({ force, only_unhunted: true, limit: 40 })
      })) as { queued: number; skipped: number };
      if (res.queued < 1) {
        setNotice(
          `Nothing queued — ${res.skipped} skipped (household, already hunted, or already running). Search links below still work.`
        );
      } else {
        setNotice("");
      }
      setKick((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Group hunt failed");
      setNotice("");
    } finally {
      setBusy(false);
    }
  }

  const groups = data?.groups || [];
  const localGroups = groups.filter((g) => g.kind === "local");
  const interestGroups = groups.filter((g) => g.kind !== "local");
  const searches = data?.searches || [];
  const localSearches = searches.filter((s) => s.kind === "local");
  const interestSearches = searches.filter((s) => s.kind !== "local");
  const fewGroups = groups.length < 4;
  const location = data?.location || "";

  const huntLabel = useMemo(() => {
    if (!hunt?.active) return "";
    if (hunt.current_sku) {
      return `Lot ${hunt.current_sku}: ${audienceHuntPhaseLabel(hunt.current_phase)}`;
    }
    return audienceHuntPhaseLabel(hunt.current_phase);
  }, [hunt]);

  return (
    <section className="ct-card ct-card--padded" style={{ marginBottom: "1.25rem" }}>
      <div className="ct-row" style={{ justifyContent: "space-between", gap: "0.75rem", flexWrap: "wrap" }}>
        <div>
          <h2 className="ct-dash-panel__title" style={{ margin: 0 }}>
            Find groups
          </h2>
          <p className="ct-muted" style={{ margin: "0.35rem 0 0", maxWidth: 720 }}>
            National and US-wide collector groups first (items ship nationwide)
            {saleName ? ` · ${saleName}` : ""}. Local groups
            {location ? ` in ${location}` : ""} are a secondary extra, never a filter. Meta cannot
            search Groups through the Page API, so staff still join and share by hand. Search links
            below are extras when the hunt finds few groups.
          </p>
        </div>
        {isOwner ? (
          <div className="ct-row" style={{ gap: "0.65rem", alignItems: "center", flexWrap: "wrap" }}>
            <label className="ct-row" style={{ gap: "0.4rem", fontSize: "0.85rem" }}>
              <input
                type="checkbox"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
                disabled={busy || Boolean(hunt?.active)}
              />
              <span className="ct-muted">Include everyday lots</span>
            </label>
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              onClick={() => void findGroups()}
              disabled={busy || Boolean(hunt?.active) || !saleId}
            >
              {busy && !hunt?.active
                ? "Queueing…"
                : hunt?.active
                  ? `Finding ${Math.min(hunt.total, hunt.done + 1)}/${hunt.total}…`
                  : "Find groups"}
            </button>
          </div>
        ) : (
          <p className="ct-muted" style={{ margin: 0, fontSize: "0.85rem" }}>
            Ask an owner to run a group hunt. Existing results stay visible.
          </p>
        )}
      </div>

      {error ? (
        <div className="ct-banner ct-banner--error" style={{ marginTop: "0.75rem" }}>
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="ct-banner" style={{ marginTop: "0.75rem" }}>
          {notice}
        </div>
      ) : null}

      {hunt?.active ? (
        <div style={{ marginTop: "0.85rem" }}>
          <AudienceHuntProgress
            title="Finding groups"
            done={Math.min(hunt.total, hunt.done + 1)}
            total={hunt.total}
            percent={huntBarPercent(hunt)}
            phaseLabel={huntLabel}
            lots={hunt.lots}
          />
        </div>
      ) : null}

      {loading && !data ? (
        <p className="ct-muted" style={{ margin: "0.85rem 0 0" }}>
          Loading groups…
        </p>
      ) : (
        <>
          <div style={{ marginTop: "1rem" }}>
            <h3 style={{ margin: 0, fontSize: "1rem" }}>Groups found</h3>
            <p className="ct-muted" style={{ margin: "0.25rem 0 0" }}>
              {groups.length
                ? `${groups.length} public group${groups.length === 1 ? "" : "s"} from the Gemini + Google Search hunt${
                    data?.hunted_lots ? ` · ${data.hunted_lots} lot${data.hunted_lots === 1 ? "" : "s"} hunted` : ""
                  }.`
                : "No hunted groups yet. Run Find groups, or use the Facebook search links below."}
            </p>
            {interestGroups.length ? (
              <>
                <p style={{ margin: "0.75rem 0 0", fontWeight: 600 }}>National / collector</p>
                <GroupList rows={interestGroups} empty="" />
              </>
            ) : null}
            {localGroups.length ? (
              <>
                <p style={{ margin: "0.75rem 0 0", fontWeight: 600 }}>
                  Local{location ? ` (${location})` : ""} — secondary
                </p>
                <GroupList rows={localGroups} empty="" />
              </>
            ) : null}
          </div>

          <div style={{ marginTop: "1.1rem" }}>
            <h3 style={{ margin: 0, fontSize: "1rem" }}>
              {fewGroups ? "Facebook group searches" : "More Facebook group searches"}
            </h3>
            <p className="ct-muted" style={{ margin: "0.25rem 0 0" }}>
              {fewGroups
                ? "Fallback searches on facebook.com — not a Graph API scrape. Open a link, then join groups that look active."
                : "Extra searches if you want more groups than the hunt returned."}
            </p>
            {interestSearches.length ? (
              <>
                <p style={{ margin: "0.75rem 0 0", fontWeight: 600 }}>National / collector</p>
                <SearchList rows={interestSearches} />
              </>
            ) : null}
            {localSearches.length ? (
              <>
                <p style={{ margin: "0.75rem 0 0", fontWeight: 600 }}>
                  Local{location ? ` (${location})` : ""} — secondary
                </p>
                <SearchList rows={localSearches} />
              </>
            ) : null}
          </div>

          {data?.tips ? (
            <p className="ct-field-hint" style={{ marginTop: "1rem" }}>
              {data.tips}{" "}
              <Link href="/engagement">Copy for groups</Link> is on each Push calendar row and in
              the Page post preview.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
