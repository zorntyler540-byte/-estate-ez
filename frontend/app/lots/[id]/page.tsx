"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { LotPhotoThumb } from "../../../components/LotPhotoThumb";
import { LotPhotoViewer } from "../../../components/LotPhotoViewer";
import { LotThumbnail } from "../../../components/LotThumbnail";
import { AppShell } from "../../../components/AppShell";
import { API_BASE, apiFetch, apiUploadFiles } from "../../../lib/api";
import { fetchLotPhotoBlob, replaceLotPhotoBlob } from "../../../lib/lotPhotoReplace";
import { rotateImageBlob } from "../../../lib/photoEdit";

type LotPhoto = {
  id: string;
  lot_id: string;
  original_name: string;
  created_at: string;
  sort_order?: number;
  file_on_disk?: boolean;
};

type Lot = {
  id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  professional_description: string;
  specification_lines: string[];
  brands: string[];
  labels_and_tags: string[];
  starting_price: number | null;
  condition_text: string;
  is_completed: boolean;
  photo_count: number;
  has_thumbnail: boolean;
  sku_needs_review: boolean;
  many_photos_review: boolean;
  sku_duplicate_in_sale: boolean;
  sku_text_mismatch: boolean;
  ai_regenerating?: boolean;
  ai_regenerate_error?: string | null;
};

type SaleLotOption = {
  id: string;
  sku: string | null;
  auction_title: string;
  photo_count: number;
};

/** Destination sentinel: create a new lot with the checked photos (same API as split). */
const TRANSFER_DEST_NEW_LOT = "__create_new_lot__";

/** Set when using ``← Back to dashboard`` so the dashboard can restore scroll after Details. */
const DASHBOARD_SCROLL_RESTORE_PENDING_KEY = "ct_dashboard_scroll_restore_pending";

function rememberDashboardScrollBeforeLotDetails() {
  try {
    sessionStorage.setItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY, "1");
  } catch {
    /* private mode */
  }
}

/** Keep in sync with ``backend/app/catalog_limits.py`` ``CTBIDS_MAX_PHOTOS_PER_LOT`` (null = no cap). */
const CTBIDS_MAX_PHOTOS_PER_LOT: number | null = null;

function parseFastApiErrorMessage(raw: string): string {
  const t = raw.trim();
  if (!t.startsWith("{")) return raw;
  try {
    const j = JSON.parse(t) as { detail?: string | { msg?: string }[] };
    const d = j.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d[0] && typeof d[0] === "object" && "msg" in d[0]) {
      return String((d[0] as { msg: string }).msg);
    }
  } catch {
    /* keep raw */
  }
  return raw;
}

/** Next.js 15 may type dynamic segments as ``string | string[]`` at runtime. */
function singleDynamicRouteParam(raw: unknown): string {
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  if (Array.isArray(raw) && raw.length > 0 && typeof raw[0] === "string" && raw[0].trim()) return raw[0].trim();
  return "";
}

function readStoredToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

export default function LotDetailPage() {
  const router = useRouter();
  const params = useParams();
  const routeLotId = useMemo(() => singleDynamicRouteParam(params.id), [params.id]);

  function logout() {
    try {
      localStorage.removeItem("token");
    } catch {
      /* private mode */
    }
    router.replace("/");
  }
  const [token, setToken] = useState(readStoredToken);
  const [lot, setLot] = useState<Lot | null>(null);
  const [catalogPhotos, setCatalogPhotos] = useState<LotPhoto[]>([]);
  const [photosError, setPhotosError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [thumbCacheBust, setThumbCacheBust] = useState<Record<string, number>>({});
  const [quickRotatingId, setQuickRotatingId] = useState<string | null>(null);
  const [zipDownloading, setZipDownloading] = useState(false);
  const [saleLots, setSaleLots] = useState<SaleLotOption[]>([]);
  const [saleName, setSaleName] = useState("");
  const [selectedPhotoIds, setSelectedPhotoIds] = useState<Set<string>>(() => new Set());
  const [transferTargetId, setTransferTargetId] = useState("");
  const [mergeSourceId, setMergeSourceId] = useState("");
  const [mergePreferSourceSku, setMergePreferSourceSku] = useState(false);
  const [organizeBusy, setOrganizeBusy] = useState(false);
  const [organizeMsg, setOrganizeMsg] = useState("");
  const [splitNewLotTitle, setSplitNewLotTitle] = useState("");
  const [aiRegenerating, setAiRegenerating] = useState(false);
  const [aiRegenError, setAiRegenError] = useState("");
  const [aiRegenNotice, setAiRegenNotice] = useState("");
  /** Set after POST returns so pre-request polls do not clear the spinner. */
  const aiRegenPostAckRef = useRef(false);
  /** True after server has reported ``ai_regenerating`` at least once this run. */
  const aiRegenSawActiveRef = useRef(false);
  const [preserveSkuForAi, setPreserveSkuForAi] = useState(false);
  const [reorderBusy, setReorderBusy] = useState(false);
  /** Long lot page: show fixed jump scroll buttons (hidden while full-screen photo viewer is open). */
  const [showScrollJumpNav, setShowScrollJumpNav] = useState(false);

  const photoFileHealth = useMemo(() => {
    const total = catalogPhotos.length;
    const missing = catalogPhotos.filter((p) => p.file_on_disk === false).length;
    return { total, missing, allMissing: total > 0 && missing === total, someMissing: missing > 0 };
  }, [catalogPhotos]);
  const showAiRegenProgress = useMemo(
    () => aiRegenerating || Boolean(lot?.ai_regenerating),
    [aiRegenerating, lot?.ai_regenerating]
  );
  const lastPhotoSelectAnchorRef = useRef<number | null>(null);
  const dragPhotoIdRef = useRef<string | null>(null);
  const addPhotosInputRef = useRef<HTMLInputElement>(null);
  const [addPhotosBusy, setAddPhotosBusy] = useState(false);

  useEffect(() => {
    try {
      sessionStorage.removeItem("ct_from_lot_detail");
    } catch {
      /* private mode */
    }
    return () => {
      try {
        sessionStorage.setItem("ct_from_lot_detail", "1");
      } catch {
        /* private mode / SSR */
      }
    };
  }, [routeLotId]);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t || !routeLotId) return;
    let cancelled = false;
    setPhotosError("");
    void (async () => {
      try {
        const d = await apiFetch(`/lots/${encodeURIComponent(routeLotId)}`, t);
        if (cancelled) return;
        const row = d as Lot;
        setLot(row);
        setAiRegenerating(Boolean(row.ai_regenerating));
        if (row.ai_regenerating) {
          aiRegenPostAckRef.current = true;
          aiRegenSawActiveRef.current = true;
        }
        try {
          const photos = (await apiFetch(`/lots/${encodeURIComponent(routeLotId)}/photos`, t)) as LotPhoto[];
          if (cancelled) return;
          setCatalogPhotos(Array.isArray(photos) ? photos : []);
        } catch {
          if (!cancelled) {
            setCatalogPhotos([]);
            setPhotosError("Could not load the photo list for this lot.");
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Load failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [routeLotId]);

  useEffect(() => {
    if (!token || !routeLotId || !showAiRegenProgress) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const row = (await apiFetch(`/lots/${encodeURIComponent(routeLotId)}`, token)) as Lot;
        if (cancelled) return;
        setLot(row);
        if (row.ai_regenerating) {
          aiRegenSawActiveRef.current = true;
          setAiRegenerating(true);
        }
        if (!row.ai_regenerating && aiRegenPostAckRef.current && aiRegenSawActiveRef.current) {
          setAiRegenerating(false);
          if (row.ai_regenerate_error) {
            const msg = row.ai_regenerate_error;
            setAiRegenNotice("");
            setAiRegenError(parseFastApiErrorMessage(msg));
          } else {
            setAiRegenError("");
            setAiRegenNotice("Listing updated from AI.");
          }
        }
      } catch {
        /* keep polling until server reports done */
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [token, routeLotId, showAiRegenProgress]);

  useEffect(() => {
    if (!token || !lot?.sale_id) return;
    let cancelled = false;
    void (async () => {
      try {
        const qs = new URLSearchParams({
          sale_id: lot.sale_id,
          sort: "created_asc",
          limit: "500",
          offset: "0"
        });
        const rows = (await apiFetch(`/lots?${qs.toString()}`, token)) as SaleLotOption[];
        if (!cancelled && Array.isArray(rows)) {
          setSaleLots(
            rows.map((r) => ({
              id: r.id,
              sku: r.sku ?? null,
              auction_title: r.auction_title || "",
              photo_count: typeof r.photo_count === "number" ? r.photo_count : 0
            }))
          );
        }
      } catch {
        if (!cancelled) setSaleLots([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, lot?.sale_id]);

  useEffect(() => {
    if (!token || !lot?.sale_id) {
      setSaleName("");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const rows = (await apiFetch("/sales", token)) as { id: string; name?: string }[];
        if (cancelled || !Array.isArray(rows)) return;
        const hit = rows.find((s) => s.id === lot.sale_id);
        setSaleName((hit?.name || "").trim());
      } catch {
        if (!cancelled) setSaleName("");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, lot?.sale_id]);

  useEffect(() => {
    setSelectedPhotoIds(new Set());
    setTransferTargetId("");
    setMergeSourceId("");
    setOrganizeMsg("");
    setSplitNewLotTitle("");
    lastPhotoSelectAnchorRef.current = null;
  }, [routeLotId]);

  useEffect(() => {
    if (typeof window === "undefined" || !lot) return;
    if (window.location.hash !== "#listing-details") return;
    requestAnimationFrame(() => {
      document.getElementById("listing-details")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [lot?.id]);

  useEffect(() => {
    if (typeof window === "undefined" || !lot) return;
    const update = () => {
      const doc = document.documentElement;
      setShowScrollJumpNav(doc.scrollHeight > window.innerHeight + 120);
    };
    update();
    window.addEventListener("resize", update, { passive: true });
    window.addEventListener("scroll", update, { passive: true });
    const t1 = window.setTimeout(update, 400);
    const t2 = window.setTimeout(update, 1500);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update);
    };
  }, [lot?.id, catalogPhotos.length]);

  function openPhotoViewer(index: number) {
    setViewerIndex(index);
  }

  function closePhotoViewer() {
    setViewerIndex(null);
  }

  function handlePhotoSaved(photoId: string) {
    setThumbCacheBust((prev) => ({ ...prev, [photoId]: Date.now() }));
    setCatalogPhotos((prev) =>
      prev.map((p) => (p.id === photoId ? { ...p, file_on_disk: true } : p))
    );
  }

  async function quickRotatePhoto(photo: LotPhoto, degrees: 90 | -90) {
    if (!lot || !token || photo.file_on_disk === false) return;
    setQuickRotatingId(photo.id);
    setError("");
    try {
      const blob = await fetchLotPhotoBlob(lot.id, photo.id, token);
      const rotated = await rotateImageBlob(blob, degrees);
      await replaceLotPhotoBlob(lot.id, photo.id, token, rotated, photo.original_name);
      handlePhotoSaved(photo.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not rotate photo");
    } finally {
      setQuickRotatingId(null);
    }
  }

  async function downloadLotPhotosZip() {
    if (!lot || !token) return;
    setError("");
    setZipDownloading(true);
    try {
      const res = await fetch(`${API_BASE}/lots/${encodeURIComponent(lot.id)}/photos/zip`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store"
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Download failed (${res.status})`);
      }
      const blob = await res.blob();
      const sku = (lot.sku || lot.id).replace(/[^\w-]+/g, "_").slice(0, 48) || "lot";
      const fileName = `lot-${sku}-photos.zip`;
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not download ZIP");
    } finally {
      setZipDownloading(false);
    }
  }

  async function regenerateListingWithAi() {
    if (!lot || !token) return;
    if (catalogPhotos.length === 0) {
      setError("This lot has no catalog photos to send to the model.");
      return;
    }
    const n = selectedPhotoIds.size > 0 ? selectedPhotoIds.size : catalogPhotos.length;
    if (
      !window.confirm(
        `Run AI on ${n} photo frame(s)? Title, description, specs, and related fields will be overwritten${preserveSkuForAi ? " (tape SKU kept as on screen)" : ""}. This can take a while.`
      )
    ) {
      return;
    }
    aiRegenPostAckRef.current = false;
    aiRegenSawActiveRef.current = false;
    setAiRegenerating(true);
    setAiRegenError("");
    setAiRegenNotice("");
    try {
      const photoIdsOrdered =
        selectedPhotoIds.size > 0
          ? catalogPhotos.filter((p) => selectedPhotoIds.has(p.id)).map((p) => p.id)
          : undefined;
      const body: { photo_ids?: string[]; preserve_sku: boolean } = {
        preserve_sku: preserveSkuForAi
      };
      if (photoIdsOrdered && photoIdsOrdered.length > 0) {
        body.photo_ids = photoIdsOrdered;
      }
      const updated = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}/ai-regenerate`, token, {
        method: "POST",
        body: JSON.stringify(body)
      })) as Lot;
      setLot(updated);
      aiRegenPostAckRef.current = true;
      if (updated.ai_regenerating) {
        aiRegenSawActiveRef.current = true;
      }
      if (updated.ai_regenerate_error) {
        const msg = updated.ai_regenerate_error;
        setAiRegenerating(false);
        setAiRegenNotice("");
        setAiRegenError(parseFastApiErrorMessage(msg));
        return;
      }
      if (!updated.ai_regenerating) {
        setAiRegenerating(false);
        setAiRegenNotice("Listing updated from AI.");
        return;
      }
      setAiRegenerating(true);
    } catch (e) {
      aiRegenPostAckRef.current = false;
      aiRegenSawActiveRef.current = false;
      setAiRegenNotice("");
      setAiRegenError(
        e instanceof Error ? parseFastApiErrorMessage(e.message) || "AI regenerate failed" : "AI regenerate failed"
      );
      setAiRegenerating(false);
    }
  }

  async function save() {
    if (!lot) return;
    setSaving(true);
    setError("");
    try {
      const updated = await apiFetch(`/lots/${lot.id}`, token, {
        method: "PATCH",
        body: JSON.stringify({
          sku: lot.sku,
          auction_title: lot.auction_title,
          professional_description: lot.professional_description,
          specification_lines: lot.specification_lines,
          brands: lot.brands,
          labels_and_tags: lot.labels_and_tags,
          starting_price: lot.starting_price,
          condition_text: lot.condition_text,
          is_completed: lot.is_completed
        })
      });
      setLot(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function removeLot() {
    if (!lot) return;
    if (!window.confirm("Delete this lot permanently?")) return;
    setError("");
    try {
      await apiFetch(`/lots/${lot.id}`, token, { method: "DELETE" });
      router.replace("/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  function togglePhotoSelect(photoId: string) {
    setSelectedPhotoIds((prev) => {
      const next = new Set(prev);
      if (next.has(photoId)) next.delete(photoId);
      else next.add(photoId);
      return next;
    });
  }

  function handlePhotoCheckboxClick(index: number, photoId: string, e: React.MouseEvent<HTMLInputElement>) {
    if (e.shiftKey && lastPhotoSelectAnchorRef.current !== null) {
      const a = lastPhotoSelectAnchorRef.current;
      const lo = Math.min(a, index);
      const hi = Math.max(a, index);
      const rangeIds = catalogPhotos.slice(lo, hi + 1).map((p) => p.id);
      setSelectedPhotoIds(new Set(rangeIds));
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      togglePhotoSelect(photoId);
      lastPhotoSelectAnchorRef.current = index;
      return;
    }
    togglePhotoSelect(photoId);
    lastPhotoSelectAnchorRef.current = index;
  }

  async function persistCatalogPhotoOrder(orderedIds: string[]) {
    if (!lot || !token) return;
    setReorderBusy(true);
    setError("");
    try {
      await apiFetch(`/lots/${encodeURIComponent(lot.id)}/photos/reorder`, token, {
        method: "PATCH",
        body: JSON.stringify({ photo_ids: orderedIds })
      });
      await refetchLotAndPhotos();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save photo order");
    } finally {
      setReorderBusy(false);
    }
  }

  function applyPhotoReorder(fromIndex: number, toIndex: number) {
    if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return;
    const ids = catalogPhotos.map((p) => p.id);
    if (fromIndex >= ids.length || toIndex >= ids.length) return;
    const next = [...ids];
    const [removed] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, removed);
    void persistCatalogPhotoOrder(next);
  }

  function selectAllCatalogPhotos() {
    setSelectedPhotoIds(new Set(catalogPhotos.map((p) => p.id)));
    lastPhotoSelectAnchorRef.current = catalogPhotos.length > 0 ? catalogPhotos.length - 1 : null;
  }

  function clearPhotoSelection() {
    setSelectedPhotoIds(new Set());
    lastPhotoSelectAnchorRef.current = null;
  }

  async function refetchLotAndPhotos() {
    const id = lot?.id ?? routeLotId;
    if (!token || !id) return;
    const d = await apiFetch(`/lots/${encodeURIComponent(id)}`, token);
    setLot(d);
    try {
      const photos = (await apiFetch(`/lots/${encodeURIComponent(id)}/photos`, token)) as LotPhoto[];
      setCatalogPhotos(Array.isArray(photos) ? photos : []);
      setPhotosError("");
    } catch {
      setPhotosError("Could not load the photo list for this lot.");
    }
  }

  async function uploadAdditionalCatalogPhotos(fileList: FileList | null) {
    if (!lot || !token || !fileList || fileList.length === 0) return;
    const n = fileList.length;
    const existing = catalogPhotos.length;
    const cap = CTBIDS_MAX_PHOTOS_PER_LOT;
    if (cap != null && existing >= cap) {
      setError(
        `CTBids allows at most ${cap} photos per listing. Remove photos before adding more.`
      );
      return;
    }
    if (cap != null && existing + n > cap) {
      const room = cap - existing;
      setError(
        `CTBids allows at most ${cap} photos per listing. This lot has ${existing}; you can add at most ${room} more in one go.`
      );
      return;
    }
    setAddPhotosBusy(true);
    setError("");
    setOrganizeMsg("");
    try {
      const fd = new FormData();
      const files = Array.from(fileList);
      for (const f of files) {
        fd.append("files", f);
      }
      const raw = await apiUploadFiles(`/lots/${encodeURIComponent(lot.id)}/photos/upload`, token, fd);
      if (!Array.isArray(raw)) {
        throw new Error("Upload response was not a photo list — check API version.");
      }
      setCatalogPhotos(raw as LotPhoto[]);
      setPhotosError("");
      const d = await apiFetch(`/lots/${encodeURIComponent(lot.id)}`, token);
      setLot(d);
      await refetchSaleLotsList();
      setOrganizeMsg(`Added ${n} photo(s) to this listing.`);
    } catch (e) {
      const raw = e instanceof Error ? e.message : "Could not upload photos";
      setError(parseFastApiErrorMessage(raw));
    } finally {
      setAddPhotosBusy(false);
      if (addPhotosInputRef.current) addPhotosInputRef.current.value = "";
    }
  }

  async function refetchSaleLotsList() {
    if (!token || !lot?.sale_id) return;
    const qs = new URLSearchParams({
      sale_id: lot.sale_id,
      sort: "created_asc",
      limit: "500",
      offset: "0"
    });
    try {
      const rows = (await apiFetch(`/lots?${qs.toString()}`, token)) as SaleLotOption[];
      if (Array.isArray(rows)) {
        setSaleLots(
          rows.map((r) => ({
            id: r.id,
            sku: r.sku ?? null,
            auction_title: r.auction_title || "",
            photo_count: typeof r.photo_count === "number" ? r.photo_count : 0
          }))
        );
      }
    } catch {
      setSaleLots([]);
    }
  }

  async function createNewLotFromSelection() {
    if (!lot || !token || selectedPhotoIds.size === 0) return;
    const n = selectedPhotoIds.size;
    const remaining = catalogPhotos.length - n;
    if (catalogPhotos.length < 2) {
      setError("Need at least two photos on this lot to split.");
      return;
    }
    if (remaining < 1) {
      setError("Leave at least one photo on this lot — clear some checkboxes first.");
      return;
    }
    if (
      !window.confirm(
        `Create a new listing with ${n} selected photo(s)? They will leave this lot; you can edit SKU and title on the new lot page.`
      )
    ) {
      return;
    }
    setOrganizeBusy(true);
    setOrganizeMsg("");
    setError("");
    try {
      const out = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}/photos/new-lot`, token, {
        method: "POST",
        body: JSON.stringify({
          photo_ids: Array.from(selectedPhotoIds),
          auction_title: splitNewLotTitle.trim() || null
        })
      })) as { new_lot: { id: string } };
      await refetchSaleLotsList();
      setSelectedPhotoIds(new Set());
      setSplitNewLotTitle("");
      setTransferTargetId("");
      setOrganizeMsg(`Created a new lot with ${n} photo(s). Opening it…`);
      router.push(`/lots/${encodeURIComponent(out.new_lot.id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create new lot");
    } finally {
      setOrganizeBusy(false);
    }
  }

  async function transferSelectedPhotos() {
    if (!lot || !token || selectedPhotoIds.size === 0 || !transferTargetId) return;
    if (transferTargetId === TRANSFER_DEST_NEW_LOT) return;
    const n = selectedPhotoIds.size;
    setOrganizeBusy(true);
    setOrganizeMsg("");
    setError("");
    try {
      await apiFetch(`/lots/${encodeURIComponent(transferTargetId)}/photos/transfer`, token, {
        method: "POST",
        body: JSON.stringify({ photo_ids: Array.from(selectedPhotoIds) })
      });
      await refetchLotAndPhotos();
      await refetchSaleLotsList();
      setSelectedPhotoIds(new Set());
      setTransferTargetId("");
      setOrganizeMsg(`Moved ${n} photo(s) to the selected lot.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transfer failed");
    } finally {
      setOrganizeBusy(false);
    }
  }

  async function applyPhotoDestination() {
    if (!lot || !token || selectedPhotoIds.size === 0 || !transferTargetId) return;
    if (transferTargetId === TRANSFER_DEST_NEW_LOT) {
      await createNewLotFromSelection();
      return;
    }
    await transferSelectedPhotos();
  }

  async function mergeSourceIntoThisLot() {
    if (!lot || !token || !mergeSourceId) return;
    if (
      !window.confirm(
        "Merge the selected lot into this one? All of its photos will move here and the other lot will be deleted. This cannot be undone."
      )
    ) {
      return;
    }
    setOrganizeBusy(true);
    setOrganizeMsg("");
    setError("");
    try {
      await apiFetch(`/lots/${encodeURIComponent(lot.id)}/merge`, token, {
        method: "POST",
        body: JSON.stringify({
          source_lot_id: mergeSourceId,
          prefer_source_sku: mergePreferSourceSku
        })
      });
      setMergeSourceId("");
      setMergePreferSourceSku(false);
      await refetchLotAndPhotos();
      await refetchSaleLotsList();
      setOrganizeMsg("Lots merged. The other listing was removed; its photos are now on this lot.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Merge failed");
    } finally {
      setOrganizeBusy(false);
    }
  }

  async function deleteSelectedPhotos() {
    if (!lot || !token || selectedPhotoIds.size === 0) return;
    const n = selectedPhotoIds.size;
    if (
      !window.confirm(
        `Permanently delete ${n} selected photo file(s) from this lot? This cannot be undone.`
      )
    ) {
      return;
    }
    setOrganizeBusy(true);
    setOrganizeMsg("");
    setError("");
    try {
      await apiFetch(`/lots/${encodeURIComponent(lot.id)}/photos/delete`, token, {
        method: "POST",
        body: JSON.stringify({ photo_ids: Array.from(selectedPhotoIds) })
      });
      closePhotoViewer();
      setSelectedPhotoIds(new Set());
      lastPhotoSelectAnchorRef.current = null;
      await refetchLotAndPhotos();
      await refetchSaleLotsList();
      setOrganizeMsg(`Deleted ${n} photo(s).`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete photos");
    } finally {
      setOrganizeBusy(false);
    }
  }

  const shellActions = (
    <>
      <Link href="/account" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
        Account
      </Link>
      <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
        Log out
      </button>
    </>
  );

  const lotTopBar = (
    <nav className="ct-lot-detail-crumb" aria-label="Breadcrumb">
      <Link href="/dashboard" onClick={rememberDashboardScrollBeforeLotDetails}>
        Dashboard
      </Link>
      <span aria-hidden>›</span>
      {saleName ? (
        <>
          <span className="ct-muted">{saleName}</span>
          <span aria-hidden>›</span>
        </>
      ) : null}
      <span aria-current="page">Lot {lot?.sku?.trim() || "(no SKU)"}</span>
    </nav>
  );

  const lotWarnings = lot ? (
    <>
      {lot.sku_needs_review ? (
        <div className="ct-banner ct-banner--warning">
          <strong>Double-check the lot number.</strong> We could not read the colored estate tape clearly — make sure
          the SKU below matches your photos before you publish.
        </div>
      ) : null}
      {lot.many_photos_review ? (
        <div className="ct-banner ct-banner--warning">
          <strong>Unusual number of photos.</strong> This listing has many more pictures than your other lots. Check
          that another item&apos;s photos were not combined here by mistake.
        </div>
      ) : null}
      {lot.sku_duplicate_in_sale ? (
        <div className="ct-banner ct-banner--warning">
          <strong>Same lot number as another row.</strong> Two listings share this number — confirm each item&apos;s
          photos are on the right row (use <strong>Organize</strong> below).
        </div>
      ) : null}
      {lot.sku_text_mismatch ? (
        <div className="ct-banner ct-banner--warning">
          <strong>Text does not match the lot number.</strong> The title or description mentions a different number than
          this row&apos;s SKU — you may need to split or move photos.
        </div>
      ) : null}
      {photoFileHealth.someMissing ? (
        <div className="ct-banner ct-banner--error">
          <strong>Photo files missing on the server.</strong>{" "}
          {photoFileHealth.allMissing ? (
            <>All {photoFileHealth.total} catalog photo{photoFileHealth.total === 1 ? "" : "s"} for this lot are gone</>
          ) : (
            <>
              {photoFileHealth.missing} of {photoFileHealth.total} catalog photo
              {photoFileHealth.total === 1 ? "" : "s"} cannot be read from disk
            </>
          )}
          {" "}
          (often after clearing completed upload jobs, an API redeploy without persistent storage, or multiple API
          servers without a shared upload volume). Use <strong>Add photos</strong> to re-upload; slots marked{" "}
          <strong>Missing on server</strong> have no file left to download or export.
        </div>
      ) : null}
      {showAiRegenProgress ? (
        <div
          className="ct-banner"
          role="status"
          aria-live="polite"
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "0.5rem",
            borderColor: "rgba(96, 165, 250, 0.45)",
            background: "rgba(59, 130, 246, 0.12)"
          }}
        >
          <Loader2 size={18} style={{ flexShrink: 0, marginTop: "0.1rem" }} className="ct-spin" />
          <div>
            <strong style={{ display: "block", marginBottom: "0.2rem" }}>AI regenerate in progress</strong>
            <span className="ct-muted" style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
              Title, description, and specs refresh when finished. This page updates automatically — large photo sets
              can take several minutes.
            </span>
          </div>
        </div>
      ) : null}
      {aiRegenError ? (
        <div className="ct-banner ct-banner--error" role="alert">
          <strong style={{ display: "block", marginBottom: "0.2rem" }}>AI regenerate failed</strong>
          {aiRegenError}
        </div>
      ) : null}
      {!showAiRegenProgress && aiRegenNotice ? (
        <div
          className="ct-banner"
          style={{
            borderColor: "rgba(74, 222, 128, 0.35)",
            background: "rgba(34, 197, 94, 0.1)"
          }}
          role="status"
        >
          {aiRegenNotice}
        </div>
      ) : null}
    </>
  ) : null;

  if (!token) {
    return (
      <AppShell actions={shellActions}>
        <div className="ct-dashboard">
          <section className="ct-dash-panel">
            <p style={{ margin: 0 }}>Please sign in first.</p>
            <p style={{ margin: "0.75rem 0 0" }}>
              <Link href="/" className="ct-btn ct-btn--primary">
                Go to sign in
              </Link>
            </p>
          </section>
        </div>
      </AppShell>
    );
  }
  if (!lot) {
    return (
      <AppShell topBar={lotTopBar} actions={shellActions}>
        <div className="ct-dashboard">
          <section className="ct-dash-panel ct-dash-panel--loading" aria-busy="true">
            <p style={{ margin: 0 }}>Loading lot…</p>
          </section>
        </div>
      </AppShell>
    );
  }

  const showOrganizePanel =
    catalogPhotos.length > 0 || saleLots.filter((x) => x.id !== lot.id).length > 0;

  const showLotAlerts =
    lot.sku_needs_review ||
    lot.many_photos_review ||
    lot.sku_duplicate_in_sale ||
    lot.sku_text_mismatch ||
    photoFileHealth.someMissing ||
    showAiRegenProgress ||
    Boolean(aiRegenError) ||
    Boolean(aiRegenNotice);

  return (
    <AppShell topBar={lotTopBar} actions={shellActions}>
      <div className="ct-dashboard ct-lot-detail-stack">
        <header className="ct-lot-detail-hero">
          <div className="ct-lot-detail-hero__main">
            <h1 className="ct-lot-detail-hero__sku">Lot {lot.sku || "(no SKU)"}</h1>
            {(lot.auction_title || "").trim() ? (
              <p className="ct-lot-detail-hero__title">{(lot.auction_title || "").trim()}</p>
            ) : (
              <p className="ct-lot-detail-hero__title ct-muted">No auction title yet</p>
            )}
          </div>
          <div className="ct-lot-detail-hero__side">
            <label
              className={`ct-lot-detail-status${lot.is_completed ? " ct-lot-detail-status--done" : ""}`}
              style={{ cursor: "pointer" }}
            >
              <input
                type="checkbox"
                checked={lot.is_completed}
                onChange={(e) => setLot({ ...lot, is_completed: e.target.checked })}
                disabled={saving || showAiRegenProgress}
              />
              <span>{lot.is_completed ? "Done" : "Not done"}</span>
            </label>
            <div className="ct-lot-detail-hero__actions">
              <label className="ct-row" style={{ gap: "0.4rem", fontSize: "0.82rem" }}>
                <input
                  type="checkbox"
                  checked={preserveSkuForAi}
                  onChange={(e) => setPreserveSkuForAi(e.target.checked)}
                  disabled={showAiRegenProgress || saving}
                />
                <span className="ct-muted">Keep SKU</span>
              </label>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                onClick={() => void regenerateListingWithAi()}
                disabled={showAiRegenProgress || saving || !catalogPhotos.length}
                aria-busy={showAiRegenProgress}
              >
                {showAiRegenProgress ? (
                  <span className="ct-row" style={{ gap: "0.4rem", alignItems: "center" }}>
                    <Loader2 size={16} className="ct-spin" aria-hidden />
                    Running AI…
                  </span>
                ) : (
                  "Regenerate with AI"
                )}
              </button>
            </div>
          </div>
        </header>

        {showLotAlerts ? <div className="ct-lot-detail-alerts">{lotWarnings}</div> : null}

        <section className="ct-dash-panel">
          <h2 className="ct-dash-panel__title">
            Catalog photos
            {catalogPhotos.length ? (
              <span className="ct-muted" style={{ fontWeight: 500, marginLeft: "0.35rem" }}>
                ({catalogPhotos.length})
              </span>
            ) : null}
          </h2>
        <p className="ct-lead" style={{ margin: "0 0 0.55rem", fontSize: "0.95rem", maxWidth: "44rem" }}>
          Photos are shown in <strong>gallery order</strong> (how they will export). Drag <strong>⋮⋮</strong> on a
          picture to reorder. Use <strong>↺ ↻</strong> on each thumbnail for a quick rotate, or click the image for crop and
          fine-tune. <strong>Add photos</strong> uploads more frames
          {CTBIDS_MAX_PHOTOS_PER_LOT != null ? (
            <>
              {" "}
              (CTBids max <strong>{CTBIDS_MAX_PHOTOS_PER_LOT}</strong> per listing).
            </>
          ) : (
            "."
          )}{" "}
          <strong>Download all photos (ZIP)</strong> and the CT Seller flow use the same stored files as{" "}
          <strong>tap-to-zoom</strong> (full resolution). Grid thumbnails are only previews.
        </p>
        <input
          ref={addPhotosInputRef}
          type="file"
          multiple
          accept="image/*,.heic,.heif"
          style={{ display: "none" }}
          onChange={(e) => void uploadAdditionalCatalogPhotos(e.target.files)}
        />
        <div
          className="ct-row"
          style={{ flexWrap: "wrap", gap: "0.5rem", marginBottom: "0.85rem", alignItems: "center" }}
        >
          <button
            type="button"
            className="ct-btn ct-btn--primary"
            disabled={
              addPhotosBusy ||
              organizeBusy ||
              reorderBusy ||
              showAiRegenProgress ||
              (CTBIDS_MAX_PHOTOS_PER_LOT != null &&
                catalogPhotos.length >= CTBIDS_MAX_PHOTOS_PER_LOT)
            }
            onClick={() => addPhotosInputRef.current?.click()}
          >
            {addPhotosBusy
              ? "Uploading…"
              : CTBIDS_MAX_PHOTOS_PER_LOT != null &&
                  catalogPhotos.length >= CTBIDS_MAX_PHOTOS_PER_LOT
                ? "Photo limit reached"
                : "Add photos…"}
          </button>
          {catalogPhotos.length > 0 ? (
            <button
              type="button"
              className="ct-btn"
              disabled={zipDownloading || addPhotosBusy}
              onClick={() => void downloadLotPhotosZip()}
            >
              {zipDownloading ? "Preparing ZIP…" : "Download all photos (ZIP)"}
            </button>
          ) : null}
        </div>
        <details className="ct-help-details" style={{ marginBottom: "0.65rem" }}>
          <summary>Extension order, checkboxes, and keyboard tips</summary>
          <div className="ct-help-details__body">
            <ul>
              <li>
                Same order as <strong>Upload lot photos</strong> in the browser extension: first row left to right,
                then the next row.
              </li>
              <li>
                Order is saved for ZIP downloads, extension upload, and AI.
              </li>
              <li>
                <strong>Shift+click</strong> a checkbox to select a range; <strong>Ctrl or ⌘+click</strong> to add or
                remove one photo.
              </li>
              <li>
                <strong>↺ ↻</strong> on a thumbnail rotates and saves immediately; click the image for crop or multiple
                edits in the full viewer.
              </li>
            </ul>
          </div>
        </details>
        {photosError ? (
          <div className="ct-banner ct-banner--error" style={{ marginBottom: "0.75rem" }}>
            {photosError}
          </div>
        ) : null}
        {catalogPhotos.length === 0 && !photosError ? (
          <div className="ct-lot-hero" style={{ marginBottom: 0 }}>
            <LotThumbnail lotId={lot.id} token={token} hasThumbnail={lot.has_thumbnail} size={140} maxPx={720} />
            <p className="ct-lot-hero-note" style={{ margin: 0 }}>
              No photos on this listing yet. Use <strong>Add photos</strong> above, or run a photo job from the
              dashboard.
            </p>
          </div>
        ) : (
          <div
            className="ct-lot-photo-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
              gap: "0.75rem"
            }}
          >
            {catalogPhotos.map((p, index) => (
              <figure
                key={p.id}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const fromId = e.dataTransfer.getData("text/plain") || dragPhotoIdRef.current;
                  dragPhotoIdRef.current = null;
                  if (!fromId) return;
                  const fromIndex = catalogPhotos.findIndex((x) => x.id === fromId);
                  if (fromIndex < 0) return;
                  applyPhotoReorder(fromIndex, index);
                }}
                style={{
                  margin: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.35rem"
                }}
              >
                <div style={{ position: "relative" }}>
                  <span
                    className="ct-muted"
                    style={{
                      position: "absolute",
                      top: 6,
                      left: 6,
                      zIndex: 1,
                      fontSize: "0.72rem",
                      fontWeight: 700,
                      background: "rgba(12, 15, 18, 0.82)",
                      color: "var(--ct-primary)",
                      padding: "0.12rem 0.4rem",
                      borderRadius: 6,
                      border: "1px solid var(--ct-border)"
                    }}
                  >
                    {index + 1}
                  </span>
                  <span
                    draggable={!organizeBusy && !reorderBusy}
                    onDragStart={(e) => {
                      e.stopPropagation();
                      dragPhotoIdRef.current = p.id;
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData("text/plain", p.id);
                    }}
                    onDragEnd={() => {
                      dragPhotoIdRef.current = null;
                    }}
                    title="Drag to reorder"
                    style={{
                      position: "absolute",
                      bottom: 4,
                      left: 4,
                      zIndex: 3,
                      fontSize: "0.62rem",
                      fontWeight: 700,
                      letterSpacing: "-0.06em",
                      padding: "2px 4px",
                      cursor: organizeBusy || reorderBusy ? "default" : "grab",
                      background: "rgba(12, 15, 18, 0.9)",
                      color: "var(--ct-primary)",
                      borderRadius: 4,
                      border: "1px solid var(--ct-border)",
                      userSelect: "none",
                      lineHeight: 1
                    }}
                  >
                    ⋮⋮
                  </span>
                  <input
                    type="checkbox"
                    title="Select for move (Shift+click range, Ctrl/Cmd+click toggle)"
                    checked={selectedPhotoIds.has(p.id)}
                    onMouseDown={(e) => {
                      if (e.shiftKey || e.ctrlKey || e.metaKey) e.preventDefault();
                    }}
                    onChange={(e) => {
                      if (organizeBusy || reorderBusy) return;
                      handlePhotoCheckboxClick(index, p.id, e.nativeEvent as unknown as React.MouseEvent<HTMLInputElement>);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    disabled={organizeBusy || reorderBusy}
                    style={{
                      position: "absolute",
                      top: 6,
                      right: 6,
                      zIndex: 2,
                      width: "1rem",
                      height: "1rem",
                      cursor: "pointer"
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      bottom: 4,
                      right: 4,
                      zIndex: 4,
                      display: "flex",
                      gap: "0.2rem"
                    }}
                  >
                    <button
                      type="button"
                      className="ct-btn"
                      title="Rotate left 90°"
                      aria-label="Rotate left"
                      disabled={
                        organizeBusy ||
                        reorderBusy ||
                        quickRotatingId !== null ||
                        p.file_on_disk === false
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        void quickRotatePhoto(p, -90);
                      }}
                      style={{
                        padding: "0.15rem 0.35rem",
                        fontSize: "0.72rem",
                        lineHeight: 1,
                        minHeight: 0
                      }}
                    >
                      {quickRotatingId === p.id ? "…" : "↺"}
                    </button>
                    <button
                      type="button"
                      className="ct-btn"
                      title="Rotate right 90°"
                      aria-label="Rotate right"
                      disabled={
                        organizeBusy ||
                        reorderBusy ||
                        quickRotatingId !== null ||
                        p.file_on_disk === false
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        void quickRotatePhoto(p, 90);
                      }}
                      style={{
                        padding: "0.15rem 0.35rem",
                        fontSize: "0.72rem",
                        lineHeight: 1,
                        minHeight: 0
                      }}
                    >
                      {quickRotatingId === p.id ? "…" : "↻"}
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => openPhotoViewer(index)}
                    title={`Open ${p.original_name} — crop and fine-tune`}
                    style={{
                      all: "unset",
                      cursor: quickRotatingId === p.id ? "wait" : "zoom-in",
                      display: "block",
                      width: "100%",
                      opacity: quickRotatingId === p.id ? 0.65 : 1
                    }}
                  >
                    <LotPhotoThumb
                      key={`${p.id}-${thumbCacheBust[p.id] ?? 0}`}
                      lotId={lot.id}
                      photoId={p.id}
                      token={token}
                      label={p.original_name}
                      maxPx={960}
                      minHeight={120}
                      fileOnDisk={p.file_on_disk !== false}
                      cacheBust={thumbCacheBust[p.id] ?? 0}
                    />
                  </button>
                </div>
                <figcaption
                  className="ct-muted"
                  style={{
                    fontSize: "0.72rem",
                    lineHeight: 1.35,
                    wordBreak: "break-word",
                    margin: 0
                  }}
                  title={
                    p.file_on_disk === false
                      ? `${p.original_name} — file missing on server`
                      : p.original_name
                  }
                >
                  {p.file_on_disk === false ? (
                    <span style={{ color: "var(--ct-error)" }}>Missing on server · </span>
                  ) : null}
                  {p.original_name}
                </figcaption>
              </figure>
            ))}
          </div>
        )}
        </section>

        {showOrganizePanel ? (
          <section className="ct-dash-panel">
            <h2 className="ct-dash-panel__title">Organize photos</h2>
            {organizeMsg ? (
              <div className="ct-banner ct-banner--success" style={{ marginBottom: "0.75rem" }}>
                {organizeMsg}
              </div>
            ) : null}
            {catalogPhotos.length > 0 ? (
              <div className="ct-lot-detail-organize-block">
                <h3 className="ct-section-title" style={{ fontSize: "1rem", margin: "0 0 0.35rem" }}>
                  Move or split selected photos
                </h3>
                <p className="ct-lead" style={{ fontSize: "0.92rem", margin: "0 0 0.55rem", maxWidth: "44rem" }}>
                  Select photos in the grid above, pick a <strong>destination</strong>, then move them or start a{" "}
                  <strong>new listing</strong> with just those pictures. One photo must stay on this lot.
                </p>
                <details className="ct-help-details" style={{ marginBottom: "0.55rem" }}>
                  <summary>When to use this vs merge</summary>
                  <div className="ct-help-details__body">
                    Use this when only <em>some</em> photos belong elsewhere, or two tape numbers were merged into one row
                    by mistake. To pull in <em>every</em> photo from another row at once, use{" "}
                    <strong>Merge a whole lot</strong> below instead.
                  </div>
                </details>
                <div className="ct-row" style={{ flexWrap: "wrap", gap: "0.5rem", marginBottom: "0.65rem" }}>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    disabled={organizeBusy || reorderBusy || catalogPhotos.length === 0}
                    onClick={selectAllCatalogPhotos}
                  >
                    Select all photos
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    disabled={organizeBusy || reorderBusy || selectedPhotoIds.size === 0}
                    onClick={clearPhotoSelection}
                  >
                    Clear selection
                  </button>
                  <span className="ct-muted" style={{ fontSize: "0.82rem", alignSelf: "center" }}>
                    {selectedPhotoIds.size} selected
                  </span>
                </div>
                {transferTargetId === TRANSFER_DEST_NEW_LOT ? (
                  <label className="ct-muted" style={{ display: "block", fontSize: "0.82rem", marginBottom: "0.55rem" }}>
                    Optional title for the new listing (leave blank for a default)
                    <input
                      className="ct-input"
                      style={{ width: "100%", maxWidth: 420, marginTop: 6, display: "block" }}
                      placeholder='e.g. "SA004 — orange pair"'
                      value={splitNewLotTitle}
                      onChange={(e) => setSplitNewLotTitle(e.target.value)}
                      disabled={organizeBusy || reorderBusy}
                      autoComplete="off"
                    />
                  </label>
                ) : null}
                <div className="ct-row" style={{ flexWrap: "wrap", gap: "0.5rem", alignItems: "center" }}>
                  <select
                    className="ct-input"
                    style={{ minWidth: "min(100%, 280px)", flex: "1 1 200px" }}
                    value={transferTargetId}
                    onChange={(e) => setTransferTargetId(e.target.value)}
                    disabled={organizeBusy || reorderBusy}
                  >
                    <option value="">— Destination —</option>
                    <option value={TRANSFER_DEST_NEW_LOT}>+ New listing (selected photos only)…</option>
                    {saleLots
                      .filter((x) => x.id !== lot.id)
                      .map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {(opt.sku || "—") + " · " + (opt.auction_title || "(untitled)").slice(0, 56)}
                          {(opt.auction_title || "").length > 56 ? "…" : ""} ({opt.photo_count} photos)
                        </option>
                      ))}
                  </select>
                  <button
                    type="button"
                    className={`ct-btn${transferTargetId === TRANSFER_DEST_NEW_LOT ? " ct-btn--primary" : ""}`}
                    disabled={
                      organizeBusy ||
                      reorderBusy ||
                      selectedPhotoIds.size === 0 ||
                      !transferTargetId ||
                      (transferTargetId === TRANSFER_DEST_NEW_LOT
                        ? catalogPhotos.length < 2 || selectedPhotoIds.size >= catalogPhotos.length
                        : !saleLots.some((x) => x.id === transferTargetId))
                    }
                    onClick={() => void applyPhotoDestination()}
                  >
                    {organizeBusy || reorderBusy
                      ? "Working…"
                      : transferTargetId === TRANSFER_DEST_NEW_LOT
                        ? "Create new lot"
                        : "Move selected"}
                  </button>
                </div>
              </div>
            ) : null}
            {saleLots.filter((x) => x.id !== lot.id).length > 0 ? (
              <div className="ct-lot-detail-organize-block">
                <h3 className="ct-section-title" style={{ fontSize: "1rem", margin: "0 0 0.35rem" }}>
                  Merge a whole lot into this one
                </h3>
                <p className="ct-lead" style={{ fontSize: "0.92rem", margin: "0 0 0.55rem", maxWidth: "44rem" }}>
                  All photos from the other listing move into this one; the other row is removed. A short note is added to
                  your specs.
                </p>
                <details className="ct-help-details" style={{ marginBottom: "0.55rem" }}>
                  <summary>SKU option &amp; partial moves</summary>
                  <div className="ct-help-details__body">
                    You can optionally take the other lot&apos;s tape SKU using the checkbox below. To move only{" "}
                    <em>some</em> photos into a new row, use <strong>Move or split selected photos</strong> above and pick{" "}
                    <strong>New listing</strong> in the menu.
                  </div>
                </details>
                <div className="ct-row" style={{ flexWrap: "wrap", gap: "0.5rem", alignItems: "center" }}>
                  <select
                    className="ct-input"
                    style={{ minWidth: "min(100%, 280px)", flex: "1 1 200px" }}
                    value={mergeSourceId}
                    onChange={(e) => setMergeSourceId(e.target.value)}
                    disabled={organizeBusy || reorderBusy}
                  >
                    <option value="">— Lot to absorb —</option>
                    {saleLots
                      .filter((x) => x.id !== lot.id)
                      .map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {(opt.sku || "—") + " · " + (opt.auction_title || "(untitled)").slice(0, 56)}
                          {(opt.auction_title || "").length > 56 ? "…" : ""} ({opt.photo_count} photos)
                        </option>
                      ))}
                  </select>
                  <button
                    type="button"
                    className="ct-btn"
                    disabled={organizeBusy || reorderBusy || !mergeSourceId || !saleLots.some((x) => x.id === mergeSourceId)}
                    onClick={() => void mergeSourceIntoThisLot()}
                  >
                    {organizeBusy || reorderBusy ? "Working…" : "Merge into this lot"}
                  </button>
                </div>
                <label className="ct-row" style={{ gap: "0.45rem", marginTop: "0.55rem" }}>
                  <input
                    type="checkbox"
                    checked={mergePreferSourceSku}
                    onChange={(e) => setMergePreferSourceSku(e.target.checked)}
                    disabled={organizeBusy || reorderBusy}
                  />
                  <span className="ct-muted" style={{ fontSize: "0.88rem" }}>
                    Use the other listing&apos;s lot number as this row&apos;s SKU when it has one
                  </span>
                </label>
              </div>
            ) : null}
            {catalogPhotos.length > 0 ? (
              <div className="ct-lot-detail-organize-block">
                <h3 className="ct-section-title" style={{ fontSize: "1rem", margin: "0 0 0.35rem" }}>
                  Delete selected photos
                </h3>
                <p className="ct-lead" style={{ margin: "0 0 0.55rem", fontSize: "0.92rem", lineHeight: 1.45 }}>
                  Remove checked photos from this listing and the server for good. The listing row stays unless you delete
                  the whole lot in <strong>Listing details</strong> below.
                </p>
                <div className="ct-form-actions" style={{ marginBottom: 0 }}>
                  <button
                    type="button"
                    className="ct-btn ct-btn--danger"
                    onClick={() => void deleteSelectedPhotos()}
                    disabled={
                      organizeBusy ||
                      reorderBusy ||
                      saving ||
                      selectedPhotoIds.size === 0 ||
                      catalogPhotos.length === 0
                    }
                  >
                    {organizeBusy ? "Working…" : "Delete selected photos"}
                  </button>
                </div>
              </div>
            ) : null}
          </section>
        ) : null}

        <section id="listing-details" className="ct-dash-panel ct-stack" style={{ scrollMarginTop: "0.75rem" }}>
          <h2 className="ct-dash-panel__title">Listing details</h2>
        <div>
          <label className="ct-label" htmlFor="lot-sku">
            Estate lot SKU (handwritten on colored tape)
          </label>
          <input
            id="lot-sku"
            className="ct-input"
            inputMode="text"
            autoComplete="off"
            placeholder="e.g. 034 or SA013"
            value={lot.sku ?? ""}
            onChange={(e) => setLot({ ...lot, sku: e.target.value.trim() || null })}
          />
          <p className="ct-muted" style={{ margin: "0.35rem 0 0", fontSize: "0.82rem" }}>
            Save after you fix it — the yellow “review SKU” flag on the dashboard clears.
          </p>
        </div>
        <div>
          <label className="ct-label" htmlFor="auction-title">
            Auction title
          </label>
          <input
            id="auction-title"
            className="ct-input"
            value={lot.auction_title}
            onChange={(e) => setLot({ ...lot, auction_title: e.target.value })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="pro-desc">
            Professional description
          </label>
          <textarea
            id="pro-desc"
            className="ct-textarea"
            style={{ minHeight: 160 }}
            value={lot.professional_description}
            onChange={(e) => setLot({ ...lot, professional_description: e.target.value })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="spec-lines">
            Specification lines (one per line)
          </label>
          <textarea
            id="spec-lines"
            className="ct-textarea"
            value={lot.specification_lines.join("\n")}
            onChange={(e) => setLot({ ...lot, specification_lines: e.target.value.split("\n").filter(Boolean) })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="brands">
            Brands (comma separated)
          </label>
          <input
            id="brands"
            className="ct-input"
            value={lot.brands.join(", ")}
            onChange={(e) => setLot({ ...lot, brands: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="tags">
            Sizes / tags — clothing &amp; shoes only (comma separated)
          </label>
          <input
            id="tags"
            className="ct-input"
            value={lot.labels_and_tags.join(", ")}
            onChange={(e) =>
              setLot({ ...lot, labels_and_tags: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })
            }
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="price">
            Starting price
          </label>
          <input
            id="price"
            className="ct-input"
            type="number"
            value={lot.starting_price ?? ""}
            onChange={(e) => setLot({ ...lot, starting_price: e.target.value ? Number(e.target.value) : null })}
          />
        </div>
        <div>
          <label className="ct-label" htmlFor="condition">
            Condition
          </label>
          <input
            id="condition"
            className="ct-input"
            value={lot.condition_text}
            onChange={(e) => setLot({ ...lot, condition_text: e.target.value })}
          />
        </div>
        <div className="ct-form-actions">
          <button type="button" className="ct-btn ct-btn--primary" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="ct-btn ct-btn--danger" onClick={() => void removeLot()}>
            Delete lot
          </button>
        </div>
        {error ? <div className="ct-banner ct-banner--error" style={{ marginBottom: 0 }}>{error}</div> : null}
      </section>
      {viewerIndex !== null && lot && token ? (
        <LotPhotoViewer
          lotId={lot.id}
          token={token}
          photos={catalogPhotos}
          index={viewerIndex}
          onClose={closePhotoViewer}
          onIndexChange={setViewerIndex}
          onSaved={handlePhotoSaved}
        />
      ) : null}

      {showScrollJumpNav && viewerIndex === null ? (
        <div
          role="navigation"
          aria-label="Jump scroll"
          style={{
            position: "fixed",
            right: "max(12px, env(safe-area-inset-right, 0px))",
            bottom: "max(16px, env(safe-area-inset-bottom, 0px))",
            zIndex: 9990,
            display: "flex",
            flexDirection: "column",
            gap: 6
          }}
        >
          <button
            type="button"
            className="ct-btn"
            title="Top of page"
            aria-label="Scroll to top of page"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            style={{
              minWidth: 48,
              minHeight: 48,
              padding: 0,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 2px 10px rgba(0,0,0,0.18)"
            }}
          >
            <ChevronUp size={22} strokeWidth={2.25} aria-hidden />
          </button>
          <button
            type="button"
            className="ct-btn"
            title="Bottom of page"
            aria-label="Scroll to bottom of page"
            onClick={() =>
              window.scrollTo({
                top: document.documentElement.scrollHeight,
                behavior: "smooth"
              })
            }
            style={{
              minWidth: 48,
              minHeight: 48,
              padding: 0,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 2px 10px rgba(0,0,0,0.18)"
            }}
          >
            <ChevronDown size={22} strokeWidth={2.25} aria-hidden />
          </button>
        </div>
      ) : null}
      </div>
    </AppShell>
  );
}

