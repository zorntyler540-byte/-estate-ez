"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Images, X } from "lucide-react";
import { API_BASE, apiFetch, parseApiErrorMessage } from "../lib/api";

type CoverSlot = {
  slot: number;
  label: string;
  photo_id: string | null;
  lot_id: string | null;
  sku: string | null;
  auction_title: string;
  photo_role: string | null;
  thumb_url: string | null;
};

type CoverCandidate = {
  photo_id: string;
  lot_id: string;
  sku: string | null;
  auction_title: string;
  photo_role: string | null;
  is_hero: boolean;
  suggested: boolean;
  thumb_url: string | null;
};

type CoverPhotosOut = {
  sale_id: string;
  ctseller_sale_id: number | null;
  selected_photo_ids: string[];
  suggested_photo_ids: string[];
  slots: CoverSlot[];
  remote?: { id: number | null; primary_url: string | null; secondary_urls: string[] } | null;
  remote_error?: string | null;
};

type CoverCandidatesOut = {
  sale_id: string;
  suggested_photo_ids: string[];
  candidates: CoverCandidate[];
};

type Props = {
  saleId: string;
  token: string;
  ctsellerSaleId: number | null;
  disabled?: boolean;
  onMapped?: (ctsellerSaleId: number) => void;
};

const EMPTY_SLOTS: CoverSlot[] = [1, 2, 3, 4].map((slot) => ({
  slot,
  label: slot === 1 ? "Primary cover" : `Secondary ${slot - 1}`,
  photo_id: null,
  lot_id: null,
  sku: null,
  auction_title: "",
  photo_role: null,
  thumb_url: null
}));

function slotFromCandidate(slot: number, candidate: CoverCandidate | undefined): CoverSlot {
  const label = slot === 1 ? "Primary cover" : `Secondary ${slot - 1}`;
  if (!candidate) {
    return { slot, label, photo_id: null, lot_id: null, sku: null, auction_title: "", photo_role: null, thumb_url: null };
  }
  return {
    slot,
    label,
    photo_id: candidate.photo_id,
    lot_id: candidate.lot_id,
    sku: candidate.sku,
    auction_title: candidate.auction_title,
    photo_role: candidate.photo_role,
    thumb_url: candidate.thumb_url
  };
}

export function SaleCoverPhotos({ saleId, token, ctsellerSaleId, disabled, onMapped }: Props) {
  const [slots, setSlots] = useState<CoverSlot[]>(EMPTY_SLOTS);
  const [suggestedIds, setSuggestedIds] = useState<string[]>([]);
  const [candidates, setCandidates] = useState<CoverCandidate[]>([]);
  const [remoteReady, setRemoteReady] = useState(false);
  const [remoteError, setRemoteError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [pickerSlot, setPickerSlot] = useState<number | null>(null);
  const [candidatesLoaded, setCandidatesLoaded] = useState(false);

  const selectedIds = useMemo(
    () => slots.map((s) => s.photo_id).filter((id): id is string => Boolean(id)),
    [slots]
  );
  const filled = selectedIds.length;
  const canPush = filled === 4 && Boolean(ctsellerSaleId) && !disabled && !busy;

  const applyCoverPayload = useCallback((data: CoverPhotosOut) => {
    const next = EMPTY_SLOTS.map((blank, idx) => data.slots[idx] || blank);
    setSlots(next);
    setSuggestedIds(data.suggested_photo_ids || []);
    const remote = data.remote;
    const hasRemote =
      Boolean(remote?.primary_url) && Array.isArray(remote?.secondary_urls) && remote.secondary_urls.length >= 3;
    setRemoteReady(hasRemote);
    setRemoteError((data.remote_error || "").trim());
  }, []);

  useEffect(() => {
    if (!token || !saleId) {
      setSlots(EMPTY_SLOTS);
      setCandidates([]);
      setCandidatesLoaded(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr("");
    setMsg("");
    setCandidatesLoaded(false);
    setCandidates([]);
    void apiFetch(`/sales/${encodeURIComponent(saleId)}/cover-photos`, token)
      .then((raw) => {
        if (cancelled) return;
        applyCoverPayload(raw as CoverPhotosOut);
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not load sale covers");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, saleId, applyCoverPayload]);

  const loadCandidates = useCallback(async (): Promise<CoverCandidatesOut | null> => {
    if (!token || !saleId) return null;
    if (candidatesLoaded) return { sale_id: saleId, suggested_photo_ids: suggestedIds, candidates };
    const raw = (await apiFetch(
      `/sales/${encodeURIComponent(saleId)}/cover-candidates`,
      token
    )) as CoverCandidatesOut;
    const rows = Array.isArray(raw.candidates) ? raw.candidates : [];
    setCandidates(rows);
    if (raw.suggested_photo_ids?.length) setSuggestedIds(raw.suggested_photo_ids);
    setCandidatesLoaded(true);
    return { ...raw, candidates: rows };
  }, [token, saleId, candidatesLoaded, candidates, suggestedIds]);

  const openPicker = (slot: number) => {
    if (disabled || busy) return;
    setPickerSlot(slot);
    setErr("");
    void loadCandidates().catch((e) => {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not load photos");
    });
  };

  const pickCandidate = (candidate: CoverCandidate) => {
    if (pickerSlot == null) return;
    setSlots((prev) =>
      prev.map((slot) => {
        if (slot.photo_id === candidate.photo_id && slot.slot !== pickerSlot) {
          return { ...slot, photo_id: null, lot_id: null, sku: null, auction_title: "", photo_role: null, thumb_url: null };
        }
        if (slot.slot !== pickerSlot) return slot;
        return slotFromCandidate(slot.slot, candidate);
      })
    );
    setPickerSlot(null);
    setMsg("");
  };

  const clearSlot = (slotNo: number) => {
    setSlots((prev) =>
      prev.map((slot) =>
        slot.slot === slotNo
          ? { ...slot, photo_id: null, lot_id: null, sku: null, auction_title: "", photo_role: null, thumb_url: null }
          : slot
      )
    );
  };

  const applySuggestedFrom = (rows: CoverCandidate[], ids: string[]) => {
    const byId = new Map(rows.map((c) => [c.photo_id, c]));
    const next = EMPTY_SLOTS.map((blank, idx) => slotFromCandidate(blank.slot, byId.get(ids[idx] || "")));
    setSlots(next);
    setMsg("Suggested lot heroes — review, then push to CT Seller.");
    setErr("");
  };

  const applySuggested = () => {
    if (!candidatesLoaded) {
      void loadCandidates()
        .then((raw) => {
          if (!raw) return;
          applySuggestedFrom(raw.candidates, raw.suggested_photo_ids || suggestedIds);
        })
        .catch((e) => {
          setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not load photos");
        });
      return;
    }
    applySuggestedFrom(candidates, suggestedIds);
  };

  const savePicks = async () => {
    if (!token || !saleId) return;
    setBusy(true);
    setErr("");
    try {
      const raw = (await apiFetch(`/sales/${encodeURIComponent(saleId)}/cover-photos`, token, {
        method: "PUT",
        body: JSON.stringify({ photo_ids: selectedIds })
      })) as CoverPhotosOut;
      applyCoverPayload(raw);
      setMsg(selectedIds.length ? `Saved ${selectedIds.length} cover pick${selectedIds.length === 1 ? "" : "s"}.` : "Cleared cover picks.");
    } catch (e) {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not save covers");
    } finally {
      setBusy(false);
    }
  };

  const pushCovers = async () => {
    if (!token || !saleId) return;
    if (selectedIds.length !== 4) {
      setErr("Pick 4 photos — 1 primary and 3 secondary.");
      return;
    }
    if (!ctsellerSaleId) {
      setErr("Pick a CT Seller auction first.");
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("Pushing sale covers to CT Seller…");
    try {
      await apiFetch(`/sales/${encodeURIComponent(saleId)}/cover-photos/push`, token, {
        method: "POST",
        body: JSON.stringify({
          photo_ids: selectedIds,
          ctseller_sale_id: ctsellerSaleId,
          save_sale_mapping: true
        })
      });
      onMapped?.(ctsellerSaleId);
      setRemoteReady(true);
      setRemoteError("");
      setMsg("Sale covers saved on CT Seller. Items can move to Ready once the auction has these 4 photos.");
    } catch (e) {
      setMsg("");
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not push covers to CT Seller");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ct-sale-covers">
      <div className="ct-sale-covers__head">
        <div>
          <h3 className="ct-sale-covers__title">CT Seller sale covers</h3>
          <p className="ct-sale-covers__hint">
            CT Seller needs 4 cover photos before lots can move to Ready. Pick them from this sale here
            instead of hunting files on seller.ctbids.com.
          </p>
        </div>
        <div className="ct-sale-covers__status" aria-live="polite">
          {loading ? "Loading…" : `${filled}/4 selected`}
          {remoteReady ? " · already on CT Seller" : ""}
        </div>
      </div>
      <div className="ct-sale-covers__slots">
        {slots.map((slot) => (
          <button
            key={slot.slot}
            type="button"
            className={`ct-sale-covers__slot${slot.slot === 1 ? " ct-sale-covers__slot--primary" : ""}${
              slot.photo_id ? " is-filled" : ""
            }`}
            onClick={() => openPicker(slot.slot)}
            disabled={disabled || busy || loading}
            title={slot.photo_id ? `${slot.label}: ${slot.sku || "lot"} — click to change` : `Choose ${slot.label}`}
          >
            {slot.photo_id ? (
              <CoverThumb
                token={token}
                lotId={slot.lot_id}
                photoId={slot.photo_id}
                thumbUrl={slot.thumb_url}
                label={slot.label}
              />
            ) : (
              <span className="ct-sale-covers__empty">
                <Images size={18} aria-hidden />
                Choose
              </span>
            )}
            <span className="ct-sale-covers__slot-label">
              {slot.label}
              {slot.sku ? ` · ${slot.sku}` : ""}
            </span>
            {slot.photo_id ? (
              <span
                className="ct-sale-covers__clear"
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  clearSlot(slot.slot);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    e.stopPropagation();
                    clearSlot(slot.slot);
                  }
                }}
                title="Clear this slot"
              >
                <X size={14} aria-hidden />
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="ct-sale-covers__actions">
        <button
          type="button"
          className="ct-btn ct-btn--ghost"
          disabled={disabled || busy || loading}
          onClick={() => applySuggested()}
        >
          Suggest from lot heroes
        </button>
        <button type="button" className="ct-btn" disabled={disabled || busy || loading} onClick={() => void savePicks()}>
          Save picks
        </button>
        <button
          type="button"
          className="ct-btn ct-btn--primary"
          disabled={!canPush}
          onClick={() => void pushCovers()}
          title={
            !ctsellerSaleId
              ? "Pick a CT Seller auction first"
              : filled !== 4
                ? "Select 4 photos"
                : "Upload these 4 photos as the sale covers on CT Seller"
          }
        >
          {busy ? "Pushing…" : "Push covers to CT Seller"}
        </button>
      </div>
      {msg ? <div className="ct-banner ct-banner--success">{msg}</div> : null}
      {err ? <div className="ct-banner ct-banner--error">{err}</div> : null}
      {remoteError && !err ? (
        <div className="ct-banner ct-banner--warning">CT Seller covers: {remoteError}</div>
      ) : null}

      {pickerSlot != null ? (
        <div
          className="ct-sale-covers-modal"
          role="dialog"
          aria-modal="true"
          aria-label={`Choose ${EMPTY_SLOTS[pickerSlot - 1]?.label || "cover photo"}`}
          onClick={() => setPickerSlot(null)}
        >
          <div className="ct-sale-covers-modal__panel" onClick={(e) => e.stopPropagation()}>
            <div className="ct-sale-covers-modal__head">
              <h3>
                Choose {EMPTY_SLOTS[pickerSlot - 1]?.label || "cover"}
                {pickerSlot === 1 ? " (landscape works best)" : " (square / portrait)"}
              </h3>
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => setPickerSlot(null)}>
                Close
              </button>
            </div>
            <p className="ct-sale-covers__hint">
              Photos already used in another slot move here. SKU-tape frames are hidden.
            </p>
            {!candidatesLoaded && !candidates.length ? (
              <p className="ct-sale-covers__hint">Loading catalog photos…</p>
            ) : null}
            <div className="ct-sale-covers-grid">
              {candidates.map((candidate) => {
                const usedHere = slots.some((s) => s.photo_id === candidate.photo_id && s.slot === pickerSlot);
                const usedOther = slots.some((s) => s.photo_id === candidate.photo_id && s.slot !== pickerSlot);
                return (
                  <button
                    key={candidate.photo_id}
                    type="button"
                    className={`ct-sale-covers-grid__card${usedHere ? " is-current" : ""}${
                      usedOther ? " is-used" : ""
                    }${candidate.suggested ? " is-suggested" : ""}`}
                    onClick={() => pickCandidate(candidate)}
                  >
                    <CoverThumb
                      token={token}
                      lotId={candidate.lot_id}
                      photoId={candidate.photo_id}
                      thumbUrl={candidate.thumb_url}
                      label={candidate.sku || "lot"}
                    />
                    <span className="ct-sale-covers-grid__meta">
                      <strong>{candidate.sku || "No SKU"}</strong>
                      {candidate.is_hero ? " · hero" : ""}
                      {usedOther ? " · used" : ""}
                      <br />
                      {(candidate.auction_title || "").slice(0, 72) || "Untitled lot"}
                    </span>
                  </button>
                );
              })}
            </div>
            {candidatesLoaded && !candidates.length ? (
              <p className="ct-sale-covers__hint">No listing photos in this sale yet.</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function CoverThumb({
  token,
  lotId,
  photoId,
  thumbUrl,
  label
}: {
  token: string;
  lotId: string | null;
  photoId: string | null;
  thumbUrl: string | null;
  label: string;
}) {
  const [src, setSrc] = useState<string | null>(thumbUrl);
  const blobRef = useRef<string | null>(null);

  useEffect(() => {
    if (thumbUrl) {
      setSrc(thumbUrl);
      return;
    }
    if (!token || !lotId || !photoId) {
      setSrc(null);
      return;
    }
    let cancelled = false;
    const ac = new AbortController();
    void (async () => {
      try {
        const mediaRes = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/media-url?max_px=220`,
          { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: ac.signal }
        );
        if (mediaRes.ok) {
          const data = (await mediaRes.json()) as { url?: string };
          if (!cancelled && data?.url) {
            setSrc(data.url);
            return;
          }
        }
        const res = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/thumbnail?max_px=220`,
          { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: ac.signal }
        );
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        if (cancelled) return;
        if (blobRef.current) URL.revokeObjectURL(blobRef.current);
        const u = URL.createObjectURL(blob);
        blobRef.current = u;
        setSrc(u);
      } catch {
        if (!cancelled) setSrc(null);
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
      if (blobRef.current) {
        URL.revokeObjectURL(blobRef.current);
        blobRef.current = null;
      }
    };
  }, [token, lotId, photoId, thumbUrl]);

  const box: CSSProperties = { width: "100%", height: "100%", objectFit: "cover", display: "block" };
  if (!src) {
    return <span className="ct-sale-covers__ph" aria-label={label} />;
  }
  return <img src={src} alt={label} style={box} />;
}
