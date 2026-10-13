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
  sale_id?: string | null;
  sale_name?: string | null;
  from_other_sale?: boolean;
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
  sale_id?: string | null;
  sale_name?: string | null;
  from_other_sale?: boolean;
};

type CoverPhotosOut = {
  sale_id: string;
  ctseller_sale_id: number | null;
  catalog_sale_name?: string;
  selected_photo_ids: string[];
  suggested_photo_ids: string[];
  slots: CoverSlot[];
  remote?: { id: number | null; primary_url: string | null; secondary_urls: string[] } | null;
  remote_error?: string | null;
};

type CoverCandidatesOut = {
  sale_id: string;
  ctseller_sale_id?: number | null;
  catalog_sale_name?: string;
  suggested_photo_ids: string[];
  candidates: CoverCandidate[];
};

type Props = {
  saleId: string;
  saleName?: string;
  token: string;
  ctsellerSaleId: number | null;
  disabled?: boolean;
  onMapped?: (ctsellerSaleId: number) => void;
};

function blankSlot(slot: number): CoverSlot {
  return {
    slot,
    label: slot === 1 ? "Primary cover" : `Secondary ${slot - 1}`,
    photo_id: null,
    lot_id: null,
    sku: null,
    auction_title: "",
    photo_role: null,
    thumb_url: null,
    sale_id: null,
    sale_name: "",
    from_other_sale: false
  };
}

const EMPTY_SLOTS: CoverSlot[] = [1, 2, 3, 4].map((slot) => blankSlot(slot));

function ctsellerCoverQuery(ctsellerSaleId: number | null): string {
  return ctsellerSaleId ? `?ctseller_sale_id=${encodeURIComponent(String(ctsellerSaleId))}` : "";
}

function skuMatchesQuery(sku: string | null, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const raw = (sku || "").trim().toLowerCase();
  if (!raw) return false;
  if (raw.includes(q)) return true;
  // Digits-only queries match the numeric part (001 finds TG001SK).
  if (/^\d+$/.test(q)) {
    const skuDigits = raw.replace(/\D/g, "");
    return skuDigits.includes(q);
  }
  return false;
}

function candidateMatchesQuery(candidate: CoverCandidate, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (skuMatchesQuery(candidate.sku, query)) return true;
  if ((candidate.auction_title || "").toLowerCase().includes(q)) return true;
  return false;
}

function slotFromCandidate(
  slot: number,
  candidate: CoverCandidate | undefined,
  currentSaleId: string
): CoverSlot {
  if (!candidate) return blankSlot(slot);
  const saleId = candidate.sale_id || currentSaleId || null;
  return {
    slot,
    label: slot === 1 ? "Primary cover" : `Secondary ${slot - 1}`,
    photo_id: candidate.photo_id,
    lot_id: candidate.lot_id,
    sku: candidate.sku,
    auction_title: candidate.auction_title,
    photo_role: candidate.photo_role,
    thumb_url: candidate.thumb_url,
    sale_id: saleId,
    sale_name: candidate.sale_name || "",
    from_other_sale: Boolean(saleId && saleId !== currentSaleId)
  };
}

export function SaleCoverPhotos({ saleId, saleName, token, ctsellerSaleId, disabled, onMapped }: Props) {
  const [slots, setSlots] = useState<CoverSlot[]>(EMPTY_SLOTS);
  const [candidates, setCandidates] = useState<CoverCandidate[]>([]);
  const [coverSaleName, setCoverSaleName] = useState("");
  const [remoteReady, setRemoteReady] = useState(false);
  const [remoteError, setRemoteError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [pickerSlot, setPickerSlot] = useState<number | null>(null);
  const [pickerSkuQuery, setPickerSkuQuery] = useState("");
  const [candidatesLoaded, setCandidatesLoaded] = useState(false);
  const [lotFrames, setLotFrames] = useState<Record<string, CoverCandidate[]>>({});
  const [lotFramesBusy, setLotFramesBusy] = useState<string | null>(null);
  const [openLotMenu, setOpenLotMenu] = useState<string | null>(null);
  const skuSearchRef = useRef<HTMLInputElement | null>(null);
  const saleIdRef = useRef(saleId);
  saleIdRef.current = saleId;

  const selectedIds = useMemo(
    () => slots.map((s) => s.photo_id).filter((id): id is string => Boolean(id)),
    [slots]
  );
  const filled = selectedIds.length;
  const canPush = filled === 4 && Boolean(ctsellerSaleId) && !disabled && !busy;
  const pickerDisabled = disabled || busy || loading;
  const openCatalogName = (saleName || "").trim();

  const applyCoverPayload = useCallback((data: CoverPhotosOut) => {
    const next = EMPTY_SLOTS.map((blank, idx) => data.slots[idx] || blank);
    setSlots(next);
    setCoverSaleName((data.catalog_sale_name || "").trim());
    const remote = data.remote;
    const hasRemote =
      Boolean(remote?.primary_url) && Array.isArray(remote?.secondary_urls) && remote.secondary_urls.length >= 3;
    setRemoteReady(hasRemote);
    setRemoteError((data.remote_error || "").trim());
  }, []);

  useEffect(() => {
    const fallbackSaleId = saleIdRef.current;
    if (!token || !fallbackSaleId) {
      setSlots(EMPTY_SLOTS);
      setCandidates([]);
      setCandidatesLoaded(false);
      setPickerSlot(null);
      setPickerSkuQuery("");
      setCoverSaleName("");
      return;
    }
    setErr("");
    setMsg("");
    if (!ctsellerSaleId) {
      setSlots(EMPTY_SLOTS);
      setCoverSaleName("");
      setRemoteReady(false);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    void apiFetch(
      `/sales/${encodeURIComponent(fallbackSaleId)}/cover-photos${ctsellerCoverQuery(ctsellerSaleId)}`,
      token
    )
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
  }, [token, ctsellerSaleId, applyCoverPayload]);

  useEffect(() => {
    setCandidatesLoaded(false);
    setCandidates([]);
    setPickerSlot(null);
    setPickerSkuQuery("");
    setLotFrames({});
    setLotFramesBusy(null);
    setOpenLotMenu(null);
    setSlots((prev) =>
      prev.map((slot) => ({
        ...slot,
        from_other_sale: Boolean(slot.photo_id && slot.sale_id && slot.sale_id !== saleId)
      }))
    );
  }, [saleId]);

  const loadCandidates = useCallback(async (): Promise<CoverCandidatesOut | null> => {
    if (!token || !saleId) return null;
    if (candidatesLoaded) return { sale_id: saleId, suggested_photo_ids: [], candidates };
    const raw = (await apiFetch(
      `/sales/${encodeURIComponent(saleId)}/cover-candidates`,
      token
    )) as CoverCandidatesOut;
    const rows = Array.isArray(raw.candidates) ? raw.candidates : [];
    setCandidates(rows);
    setCandidatesLoaded(true);
    return { ...raw, candidates: rows };
  }, [token, saleId, candidatesLoaded, candidates]);

  const closePicker = () => {
    setPickerSlot(null);
    setPickerSkuQuery("");
    setOpenLotMenu(null);
  };

  const openPicker = (slot: number) => {
    if (pickerDisabled) return;
    setPickerSkuQuery("");
    setPickerSlot(slot);
    setErr("");
    void loadCandidates().catch((e) => {
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not load photos");
    });
  };

  useEffect(() => {
    if (pickerSlot == null) return;
    skuSearchRef.current?.focus();
  }, [pickerSlot]);

  const coverCandidates = useMemo(
    () =>
      candidates.filter(
        (candidate) => candidate.photo_role !== "tape" && candidate.photo_role !== "unusable"
      ),
    [candidates]
  );
  const visibleCandidates = useMemo(
    () => coverCandidates.filter((candidate) => candidateMatchesQuery(candidate, pickerSkuQuery)),
    [coverCandidates, pickerSkuQuery]
  );
  const lotCards = useMemo(() => {
    const order: string[] = [];
    const byLot = new Map<string, CoverCandidate>();
    for (const row of visibleCandidates) {
      if (!byLot.has(row.lot_id)) order.push(row.lot_id);
      const prev = byLot.get(row.lot_id);
      if (!prev || (row.is_hero && !prev.is_hero)) byLot.set(row.lot_id, row);
    }
    return order.map((id) => byLot.get(id)!);
  }, [visibleCandidates]);

  const toggleLotPhotos = async (lotId: string) => {
    if (!lotId) return;
    if (openLotMenu === lotId) {
      setOpenLotMenu(null);
      return;
    }
    setOpenLotMenu(lotId);
    if (lotFrames[lotId] || !token || !saleId) return;
    setLotFramesBusy(lotId);
    try {
      const raw = (await apiFetch(
        `/sales/${encodeURIComponent(saleId)}/cover-candidates?lot_id=${encodeURIComponent(lotId)}`,
        token
      )) as CoverCandidatesOut;
      const rows = (Array.isArray(raw.candidates) ? raw.candidates : []).filter(
        (candidate) => candidate.photo_role !== "tape" && candidate.photo_role !== "unusable"
      );
      setLotFrames((prev) => ({ ...prev, [lotId]: rows }));
    } catch (e) {
      setOpenLotMenu(null);
      setErr(e instanceof Error ? parseApiErrorMessage(e.message) : "Could not load this lot's photos");
    } finally {
      setLotFramesBusy((current) => (current === lotId ? null : current));
    }
  };

  const pickCandidate = (candidate: CoverCandidate) => {
    if (pickerSlot == null) return;
    setSlots((prev) =>
      prev.map((slot) => {
        if (slot.photo_id === candidate.photo_id && slot.slot !== pickerSlot) {
          return blankSlot(slot.slot);
        }
        if (slot.slot !== pickerSlot) return slot;
        return slotFromCandidate(slot.slot, candidate, saleId);
      })
    );
    closePicker();
    setMsg("");
  };

  const clearSlot = (slotNo: number) => {
    setSlots((prev) =>
      prev.map((slot) =>
        slot.slot === slotNo ? blankSlot(slot.slot) : slot
      )
    );
  };

  const savePicks = async () => {
    if (!token || !saleId) return;
    if (!ctsellerSaleId) {
      setErr("Pick a CT Seller auction first.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const raw = (await apiFetch(`/sales/${encodeURIComponent(saleId)}/cover-photos`, token, {
        method: "PUT",
        body: JSON.stringify({ photo_ids: selectedIds, ctseller_sale_id: ctsellerSaleId })
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
            {ctsellerSaleId
              ? `CT Seller needs 4 cover photos on auction #${ctsellerSaleId}. Pick from the catalog you are on${
                  openCatalogName ? ` (${openCatalogName})` : ""
                }. Switch Change sale to add photos from another catalog — the 4 slots stay. Save and push go to that auction${
                  coverSaleName ? ` (${coverSaleName})` : ""
                }. Nothing is pre-filled.`
              : "Pick a CT Seller auction above. Then pick photos from any catalog in Change sale — the 4 slots stay, and save/push go to that auction."}
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
            disabled={pickerDisabled}
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
              {slot.from_other_sale && slot.sale_name ? ` · ${slot.sale_name}` : ""}
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
          className="ct-btn"
          disabled={pickerDisabled || !ctsellerSaleId}
          onClick={() => void savePicks()}
        >
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
          onClick={closePicker}
        >
          <div className="ct-sale-covers-modal__panel" onClick={(e) => e.stopPropagation()}>
            <div className="ct-sale-covers-modal__head">
              <h3>
                Choose {EMPTY_SLOTS[pickerSlot - 1]?.label || "cover"}
                {pickerSlot === 1 ? " (landscape works best)" : " (square / portrait)"}
              </h3>
              <button type="button" className="ct-btn ct-btn--ghost" onClick={closePicker}>
                Close
              </button>
            </div>
            <p className="ct-sale-covers__hint">
              Photos from {openCatalogName || "the catalog you are on"}. One card per lot.
              More photos opens the rest of that SKU so you can use a different frame. Switch
              Change sale to pick from another catalog — the 4 slots stay. SKU-tape frames are
              not shown.
            </p>
            <label className="ct-sale-covers-modal__search">
              <span className="ct-sale-covers-modal__search-label">Search SKU</span>
              <input
                ref={skuSearchRef}
                className="ct-input"
                type="search"
                value={pickerSkuQuery}
                onChange={(e) => setPickerSkuQuery(e.target.value)}
                placeholder="TG001 or 001"
                autoComplete="off"
                spellCheck={false}
                aria-label="Search cover photos by SKU"
              />
            </label>
            {!candidatesLoaded && !candidates.length ? (
              <p className="ct-sale-covers__hint">Loading catalog photos…</p>
            ) : null}
            <div className="ct-sale-covers-grid">
              {lotCards.map((candidate) => {
                const usedHere = slots.some((s) => s.photo_id === candidate.photo_id && s.slot === pickerSlot);
                const usedOther = slots.some((s) => s.photo_id === candidate.photo_id && s.slot !== pickerSlot);
                const menuOpen = openLotMenu === candidate.lot_id;
                const frames = lotFrames[candidate.lot_id] || [];
                const otherFrames = frames.filter((frame) => frame.photo_id !== candidate.photo_id);
                return (
                  <div
                    key={candidate.lot_id}
                    className={`ct-sale-covers-grid__card${usedHere ? " is-current" : ""}${
                      usedOther ? " is-used" : ""
                    }`}
                  >
                    <button
                      type="button"
                      className="ct-sale-covers-grid__pick"
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
                    <button
                      type="button"
                      className="ct-sale-covers-more-btn"
                      aria-expanded={menuOpen}
                      onClick={() => void toggleLotPhotos(candidate.lot_id)}
                    >
                      {menuOpen ? "Hide photos" : "More photos"}
                    </button>
                    {menuOpen ? (
                      <div className="ct-sale-covers-more" role="listbox" aria-label={`Other photos of ${candidate.sku || "this lot"}`}>
                        {lotFramesBusy === candidate.lot_id && !frames.length ? (
                          <p className="ct-sale-covers__hint">Loading photos…</p>
                        ) : null}
                        {frames.length && !otherFrames.length ? (
                          <p className="ct-sale-covers__hint">This lot has no other listing photo.</p>
                        ) : null}
                        {otherFrames.map((frame) => {
                          const frameUsed = slots.some((s) => s.photo_id === frame.photo_id && s.slot !== pickerSlot);
                          return (
                            <button
                              key={frame.photo_id}
                              type="button"
                              role="option"
                              className="ct-sale-covers-more__shot"
                              onClick={() => pickCandidate(frame)}
                            >
                              <CoverThumb
                                token={token}
                                lotId={frame.lot_id}
                                photoId={frame.photo_id}
                                thumbUrl={frame.thumb_url}
                                label={frame.sku || "lot"}
                              />
                              <span>
                                {frame.is_hero ? "hero" : frame.photo_role || "photo"}
                                {frameUsed ? " · used" : ""}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
            {candidatesLoaded && !coverCandidates.length ? (
              <p className="ct-sale-covers__hint">No listing photos in this sale yet.</p>
            ) : null}
            {candidatesLoaded && coverCandidates.length && !visibleCandidates.length ? (
              <p className="ct-sale-covers__hint">
                No lots match SKU “{pickerSkuQuery.trim()}”.
              </p>
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
  const hostRef = useRef<HTMLSpanElement | null>(null);
  const blobRef = useRef<string | null>(null);
  const [visible, setVisible] = useState(false);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
        }
      },
      { rootMargin: "280px", threshold: 0.01 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
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
        // Sign the original only — generating 220px thumbs here stalls Railway on large sales.
        const mediaRes = await fetch(
          `${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/${encodeURIComponent(photoId)}/media-url?generate_if_missing=false`,
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
        const objectUrl = URL.createObjectURL(blob);
        blobRef.current = objectUrl;
        setSrc(objectUrl);
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
  }, [visible, token, lotId, photoId, thumbUrl]);

  const box: CSSProperties = { width: "100%", height: "100%", objectFit: "cover", display: "block" };
  return (
    <span ref={hostRef} className="ct-sale-covers__ph" aria-label={label}>
      {src ? <img src={src} alt={label} style={box} loading="lazy" decoding="async" /> : null}
    </span>
  );
}
