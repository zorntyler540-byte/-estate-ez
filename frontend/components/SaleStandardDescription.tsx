"use client";

import { useEffect, useState } from "react";
import { apiFetch, parseApiErrorMessage } from "../lib/api";

type SaveResponse = {
  standard_description?: string | null;
  posting_description_suffix?: string;
  lots_updated?: number;
};

type Props = {
  saleId: string;
  token: string;
  accountDefault: string;
  saleOverride: string | null;
  unpostedCount: number;
  lotCount: number;
  postedCount: number;
  disabled?: boolean;
  onSaved?: (next: {
    standard_description: string | null;
    posting_description_suffix: string;
    lotsUpdated: number;
  }) => void;
  onDescriptionsUpdated?: (websiteUpdated: number) => void;
};

export function SaleStandardDescription({
  saleId,
  token,
  accountDefault,
  saleOverride,
  unpostedCount,
  lotCount,
  postedCount,
  disabled,
  onSaved,
  onDescriptionsUpdated
}: Props) {
  const inheriting = saleOverride == null;
  const [useOverride, setUseOverride] = useState(!inheriting);
  const [draft, setDraft] = useState(saleOverride ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    setUseOverride(saleOverride != null);
    setDraft(saleOverride ?? "");
    setMsg("");
    setErr("");
  }, [saleId]);

  useEffect(() => {
    // Pick up an override that arrives after the sale is selected, without wiping the save confirmation.
    setUseOverride(saleOverride != null);
    setDraft(saleOverride ?? "");
  }, [saleId, saleOverride]);

  const effective = useOverride ? draft.trim() : accountDefault.trim();

  const save = async (applyToUnposted: boolean) => {
    if (!token || !saleId) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = (await apiFetch(`/sales/${encodeURIComponent(saleId)}/standard-description`, token, {
        method: "POST",
        body: JSON.stringify(
          useOverride
            ? { standard_description: draft, apply_to_unposted: applyToUnposted }
            : { inherit_account: true, apply_to_unposted: applyToUnposted }
        )
      })) as SaveResponse;
      const nextOverride = useOverride ? (res.standard_description ?? "") : null;
      const suffix = res.posting_description_suffix ?? "";
      const lotsUpdated = res.lots_updated ?? 0;
      onSaved?.({
        standard_description: nextOverride,
        posting_description_suffix: suffix,
        lotsUpdated
      });
      if (applyToUnposted) {
        setMsg(
          lotsUpdated
            ? `Updated ${lotsUpdated} unposted lot${lotsUpdated === 1 ? "" : "s"}. Posted lots stay as they are.`
            : "No unposted lots needed a change. Posted lots stay as they are."
        );
      } else if (!suffix) {
        setMsg("Saved. This sale will not add a standard note.");
      } else {
        setMsg("Saved. Future CT Seller posts add this note. Posted lots stay as they are.");
      }
    } catch (e) {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not save the standard description");
    } finally {
      setBusy(false);
    }
  };

  const updateDescriptions = async () => {
    if (!token || !saleId || lotCount < 1) return;
    const postedNote =
      postedCount > 0
        ? `${postedCount} already on CT Seller will be updated there too.`
        : "None of these lots are on CT Seller yet, so only the website descriptions change.";
    if (
      !window.confirm(
        `Update the description on all ${lotCount} lot${lotCount === 1 ? "" : "s"} in this sale?\n\nThe standard note is written onto each description. ${postedNote}`
      )
    ) {
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = (await apiFetch(`/sales/${encodeURIComponent(saleId)}/description/publish`, token, {
        method: "POST"
      })) as {
        lots_total?: number;
        website_updated?: number;
        ctseller_updated?: number;
        ctseller_skipped?: number;
        ctseller_failed?: number;
        errors?: string[];
      };
      const websiteUpdated = res.website_updated ?? 0;
      const sellerUpdated = res.ctseller_updated ?? 0;
      const sellerFailed = res.ctseller_failed ?? 0;
      onDescriptionsUpdated?.(websiteUpdated);
      const parts = [
        websiteUpdated
          ? `Updated ${websiteUpdated} description${websiteUpdated === 1 ? "" : "s"} on the website.`
          : "Website descriptions were already up to date."
      ];
      if (sellerUpdated) {
        parts.push(`Updated ${sellerUpdated} on CT Seller.`);
      } else if ((res.ctseller_skipped ?? 0) > 0 && !sellerFailed) {
        parts.push("No posted CT Seller listings needed a change.");
      }
      setMsg(parts.join(" "));
      if (sellerFailed) {
        const detail = (res.errors || []).filter(Boolean).slice(0, 2).join(" ");
        setErr(
          `${sellerFailed} CT Seller update${sellerFailed === 1 ? "" : "s"} failed${detail ? `: ${detail}` : "."}`
        );
      }
    } catch (e) {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not update descriptions");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ct-sale-reserve">
      <div className="ct-sale-reserve__head">
        <div>
          <h3 className="ct-sale-reserve__title">Standard description</h3>
          <p className="ct-sale-reserve__hint">
            Added at the end of every lot when it is posted to CT Seller. It is not written by the
            AI. A note already in the description is not added twice.
          </p>
        </div>
      </div>
      <p className="ct-sale-reserve__hint">
        {accountDefault.trim()
          ? `Account default: ${accountDefault.trim()}`
          : "No account default yet. An owner can set one on Account."}
      </p>
      <label className="ct-sale-reserve__check">
        <input
          type="checkbox"
          checked={useOverride}
          disabled={disabled || busy}
          onChange={(e) => setUseOverride(e.target.checked)}
        />
        Use a different note for this sale
      </label>
      {useOverride ? (
        <textarea
          className="ct-textarea"
          rows={3}
          maxLength={4000}
          placeholder="Leave blank to add nothing on this sale"
          value={draft}
          disabled={disabled || busy}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Standard description for this sale"
        />
      ) : null}
      <p className="ct-sale-reserve__hint">
        {effective
          ? `Lots in this sale will post with: “${effective}”`
          : "Lots in this sale will post without a standard note."}
      </p>
      <div className="ct-sale-reserve__row">
        <button
          type="button"
          className="ct-btn ct-btn--primary"
          disabled={disabled || busy || !saleId}
          onClick={() => void save(false)}
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          className="ct-btn"
          disabled={disabled || busy || !saleId || unpostedCount < 1}
          onClick={() => void save(true)}
          title="Writes the note onto lots that are not on CT Seller yet"
        >
          {effective ? `Add to ${unpostedCount} unposted` : `Remove from ${unpostedCount} unposted`}
        </button>
        <button
          type="button"
          className="ct-btn"
          disabled={disabled || busy || !saleId || lotCount < 1}
          onClick={() => void updateDescriptions()}
          title="Writes the current note onto every lot, and updates lots already posted to CT Seller"
        >
          {busy ? "Updating…" : "Update descriptions"}
        </button>
      </div>
      {msg ? <div className="ct-banner ct-banner--success">{msg}</div> : null}
      {err ? <div className="ct-banner ct-banner--error">{err}</div> : null}
    </div>
  );
}
