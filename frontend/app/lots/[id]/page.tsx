"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Archive, ArrowRightLeft, ChevronDown, ChevronUp, GitMerge, ImagePlus, LayoutList, Loader2, RotateCcw, RotateCw, Save, Trash2 } from "lucide-react";
import { LotPhotoThumb } from "../../../components/LotPhotoThumb";
import { LotPhotoViewer } from "../../../components/LotPhotoViewer";
import { LotThumbnail } from "../../../components/LotThumbnail";
import { MoveLotsToSale, type MoveSaleOption } from "../../../components/MoveLotsToSale";
import { AppShell } from "../../../components/AppShell";
import {
  AudienceHuntProgress,
  audienceHuntPhaseLabel,
  audienceHuntStepPercent
} from "../../../components/AudienceHuntProgress";
import { API_BASE, apiFetch, apiUploadFiles } from "../../../lib/api";
import { isOwnerRole } from "../../../lib/roles";
import { sortBySku } from "../../../lib/skuSort";

type LotVideo = {
  id: string;
  lot_id: string;
  original_name: string;
  created_at: string;
  sort_order?: number;
  content_type?: string;
  byte_size?: number;
};

type LotPhoto = {
  id: string;
  lot_id: string;
  original_name: string;
  created_at: string;
  sort_order?: number;
  file_on_disk?: boolean;
  photo_role?: string | null;
  distinct_item_id?: string | null;
  is_primary_front?: boolean;
  shows_whole_lot?: boolean;
  shows_measuring_scale?: boolean;
  label_legibility?: string | null;
};

const LISTING_ROLE_LABEL: Record<string, string> = {
  hero: "Group",
  item_front: "Item",
  angle_only: "Detail",
  tape: "SKU tape",
  unusable: "Skip"
};

function listingRoleBadge(photo: LotPhoto): string | null {
  if (photo.shows_measuring_scale) return "Measure";
  if (photo.photo_role && LISTING_ROLE_LABEL[photo.photo_role]) {
    return LISTING_ROLE_LABEL[photo.photo_role];
  }
  return null;
}

type Lot = {
  id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  professional_description: string;
  specification_lines: string[];
  brands: string[];
  labels_and_tags: string[];
  search_tags: string[];
  starting_price: number | null;
  reserve_price?: number | null;
  condition_text: string;
  is_completed: boolean;
  photo_count: number;
  has_thumbnail: boolean;
  sku_needs_review: boolean;
  many_photos_review: boolean;
  sku_duplicate_in_sale: boolean;
  sku_text_mismatch: boolean;
  listing_needs_review?: boolean;
  listing_review_reason?: string | null;
  ai_regenerating?: boolean;
  ai_regenerate_error?: string | null;
  ctseller_item_id?: number | null;
  ctseller_posted_at?: string | null;
  ctseller_sale_id?: number | null;
  ctseller_item_status?: string | null;
  ctseller_sold_price?: number | null;
  audience_hold?: boolean;
  audience_hunting?: boolean;
  audience_hunt_error?: string | null;
  audience_hunt_phase?: string | null;
  audience?: {
    worthy?: boolean;
    skip_reason?: string;
    groups?: { name: string; url?: string; why?: string }[];
    blurb_ctbids?: string;
    blurb_hold?: string;
    recommend_hold?: boolean;
    researched_at?: string | null;
  } | null;
};

type CTSellerRemoteSale = {
  id: number;
  title: string;
  status?: string | null;
  starttime?: string | null;
  endtime?: string | null;
};

type SaleLotOption = {
  id: string;
  sku: string | null;
  auction_title: string;
  photo_count: number;
};

function saleLotOptionsFromRows(rows: SaleLotOption[]): SaleLotOption[] {
  return sortBySku(
    rows.map((r) => ({
      id: r.id,
      sku: r.sku ?? null,
      auction_title: r.auction_title || "",
      photo_count: typeof r.photo_count === "number" ? r.photo_count : 0
    })),
    (r) => r.sku
  );
}

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

/** Keep in sync with ``backend/app/catalog_limits.py`` (CT Seller ~40; CT Seller hard cap). */
const CTBIDS_MAX_PHOTOS_PER_LOT: number | null = 40;

const STANDARD_CONDITIONS = ["NEW", "LIKE NEW", "VERY GOOD", "GOOD", "FAIR"] as const;

type CTSellerCondition = { condition: string; description?: string };

function conditionSelectOptions(
  current: string,
  catalog: CTSellerCondition[] = []
): CTSellerCondition[] {
  const base =
    catalog.length > 0
      ? catalog
      : STANDARD_CONDITIONS.map((condition) => ({ condition, description: "" }));
  const trimmed = current.trim();
  if (trimmed && !base.some((row) => row.condition === trimmed)) {
    return [{ condition: trimmed, description: "" }, ...base];
  }
  return base;
}

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
  const [accountRole, setAccountRole] = useState("");
  const isAccountOwner = isOwnerRole(accountRole);
  const [lot, setLot] = useState<Lot | null>(null);
  const [catalogPhotos, setCatalogPhotos] = useState<LotPhoto[]>([]);
  const [catalogVideos, setCatalogVideos] = useState<LotVideo[]>([]);
  const [videoError, setVideoError] = useState("");
  const [videoBusy, setVideoBusy] = useState(false);
  const [playingVideoUrl, setPlayingVideoUrl] = useState<string | null>(null);
  const addVideosInputRef = useRef<HTMLInputElement>(null);
  const [photosError, setPhotosError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [listingDirty, setListingDirty] = useState(false);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [thumbCacheBust, setThumbCacheBust] = useState<Record<string, number>>({});
  const [quickRotatingId, setQuickRotatingId] = useState<string | null>(null);
  const [batchRotating, setBatchRotating] = useState(false);
  const [zipDownloading, setZipDownloading] = useState(false);
  const [saleLots, setSaleLots] = useState<SaleLotOption[]>([]);
  const [saleName, setSaleName] = useState("");
  const [catalogSales, setCatalogSales] = useState<MoveSaleOption[]>([]);
  const [mappedCtSellerSaleId, setMappedCtSellerSaleId] = useState<number | null>(null);
  const [ctSellerRemoteSales, setCtSellerRemoteSales] = useState<CTSellerRemoteSale[]>([]);
  const [ctSellerConditions, setCtSellerConditions] = useState<CTSellerCondition[]>([]);
  const [ctSellerPickId, setCtSellerPickId] = useState("");
  const [ctSellerPostBusy, setCtSellerPostBusy] = useState(false);
  const [ctSellerMsg, setCtSellerMsg] = useState("");
  const [ctSellerErr, setCtSellerErr] = useState("");
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
  const [audienceHunting, setAudienceHunting] = useState(false);
  const [audienceHuntError, setAudienceHuntError] = useState("");
  const [audienceHuntNotice, setAudienceHuntNotice] = useState("");
  const [audienceCopyMsg, setAudienceCopyMsg] = useState("");
  const [audienceForce, setAudienceForce] = useState(false);
  const [audienceHoldBusy, setAudienceHoldBusy] = useState(false);
  const audienceHuntPostAckRef = useRef(false);
  const audienceHuntSawActiveRef = useRef(false);
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
  const showAudienceHuntProgress = useMemo(
    () => audienceHunting || Boolean(lot?.audience_hunting),
    [audienceHunting, lot?.audience_hunting]
  );
  const audienceHeld = Boolean(lot?.audience_hold);
  const lastPhotoSelectAnchorRef = useRef<number | null>(null);
  const dragPhotoIdRef = useRef<string | null>(null);
  const addPhotosInputRef = useRef<HTMLInputElement>(null);
  const [addPhotosBusy, setAddPhotosBusy] = useState(false);
  const [searchTagsBusy, setSearchTagsBusy] = useState(false);

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
    setListingDirty(false);
  }, [routeLotId]);

  function markListingDirty() {
    setListingDirty(true);
  }

  function updateListingFields(patch: Partial<Lot>) {
    markListingDirty();
    setLot((prev) => (prev ? { ...prev, ...patch } : prev));
  }

  useEffect(() => {
    if (!token) {
      setAccountRole("");
      return;
    }
    let cancelled = false;
    void apiFetch("/auth/me", token)
      .then((data) => {
        if (!cancelled) setAccountRole(String((data as { role?: string }).role || ""));
      })
      .catch(() => {
        if (!cancelled) setAccountRole("");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

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
        setLot({ ...row, search_tags: row.search_tags || [] });
        setListingDirty(false);
        setAiRegenerating(Boolean(row.ai_regenerating));
        if (row.ai_regenerating) {
          aiRegenPostAckRef.current = true;
          aiRegenSawActiveRef.current = true;
        }
        setAudienceHunting(Boolean(row.audience_hunting));
        if (row.audience_hunting) {
          audienceHuntPostAckRef.current = true;
          audienceHuntSawActiveRef.current = true;
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
        try {
          const videos = (await apiFetch(`/lots/${encodeURIComponent(routeLotId)}/videos`, t)) as LotVideo[];
          if (cancelled) return;
          setCatalogVideos(Array.isArray(videos) ? videos : []);
        } catch {
          if (!cancelled) setCatalogVideos([]);
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
    if (!token || !routeLotId || !showAudienceHuntProgress) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const row = (await apiFetch(`/lots/${encodeURIComponent(routeLotId)}`, token)) as Lot;
        if (cancelled) return;
        setLot(row);
        if (row.audience_hunting) {
          audienceHuntSawActiveRef.current = true;
          setAudienceHunting(true);
        }
        if (!row.audience_hunting && audienceHuntPostAckRef.current && audienceHuntSawActiveRef.current) {
          setAudienceHunting(false);
          if (row.audience_hunt_error) {
            setAudienceHuntNotice("");
            setAudienceHuntError(parseFastApiErrorMessage(row.audience_hunt_error));
          } else {
            setAudienceHuntError("");
            const skipped = Boolean(row.audience?.skip_reason) && !(row.audience?.groups?.length);
            setAudienceHuntNotice(
              skipped
                ? row.audience?.skip_reason || "Skipped — everyday household lot."
                : row.audience?.groups?.length
                  ? `Found ${row.audience.groups.length} public group${row.audience.groups.length === 1 ? "" : "s"}.`
                  : "Hunt finished — no public groups found."
            );
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
  }, [token, routeLotId, showAudienceHuntProgress]);

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
          setSaleLots(saleLotOptionsFromRows(rows));
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
      setCatalogSales([]);
      setMappedCtSellerSaleId(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const rows = (await apiFetch("/sales", token)) as {
          id: string;
          name?: string;
          closed_at?: string | null;
          ctseller_sale_id?: number | null;
        }[];
        if (cancelled || !Array.isArray(rows)) return;
        setCatalogSales(
          rows.map((s) => ({
            id: s.id,
            name: (s.name || "").trim() || "Untitled sale",
            closed_at: s.closed_at ?? null,
          }))
        );
        const hit = rows.find((s) => s.id === lot.sale_id);
        setSaleName((hit?.name || "").trim());
        const mapped =
          typeof hit?.ctseller_sale_id === "number" && Number.isFinite(hit.ctseller_sale_id)
            ? hit.ctseller_sale_id
            : null;
        setMappedCtSellerSaleId(mapped);
        if (mapped != null) setCtSellerPickId(String(mapped));
      } catch {
        if (!cancelled) {
          setSaleName("");
          setCatalogSales([]);
          setMappedCtSellerSaleId(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, lot?.sale_id]);

  useEffect(() => {
    if (!token) {
      setCtSellerRemoteSales([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const rows = (await apiFetch("/integrations/ctseller/sales", token)) as CTSellerRemoteSale[];
        if (cancelled) return;
        setCtSellerRemoteSales(Array.isArray(rows) ? rows : []);
      } catch {
        if (!cancelled) setCtSellerRemoteSales([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (!token) {
      setCtSellerConditions([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const rows = (await apiFetch("/integrations/ctseller/conditions", token)) as CTSellerCondition[];
        if (cancelled) return;
        setCtSellerConditions(Array.isArray(rows) ? rows : []);
      } catch {
        if (!cancelled) setCtSellerConditions([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

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
    await rotateLotPhotos(degrees, [photo.id], { confirmAll: false });
  }

  async function rotateLotPhotos(
    degrees: 90 | -90,
    photoIds: string[] | null,
    opts?: { confirmAll?: boolean }
  ) {
    if (!lot || !token) return;
    const confirmAll = opts?.confirmAll !== false;
    const rotatable = catalogPhotos.filter((p) => p.file_on_disk !== false);
    const ids =
      photoIds && photoIds.length > 0
        ? photoIds.filter((id) => rotatable.some((p) => p.id === id))
        : rotatable.map((p) => p.id);
    if (ids.length === 0) {
      setError("No photos on disk to rotate.");
      return;
    }
    const rotatingAllOnLot = ids.length === rotatable.length && rotatable.length > 1;
    if (confirmAll && rotatingAllOnLot && selectedPhotoIds.size === 0) {
      const dir = degrees === 90 ? "clockwise" : "counter-clockwise";
      if (
        !window.confirm(
          `Rotate all ${ids.length} photos on this lot 90° ${dir}?\n\nGroup shots that are already upright will turn sideways. Check only the sideways photos first if you do not want that.`
        )
      ) {
        return;
      }
    }
    const single = ids.length === 1 ? ids[0] : null;
    if (single) setQuickRotatingId(single);
    else setBatchRotating(true);
    setError("");
    try {
      await apiFetch(`/lots/${encodeURIComponent(lot.id)}/photos/rotate`, token, {
        method: "POST",
        body: JSON.stringify({ degrees, photo_ids: ids })
      });
      const bustAt = Date.now();
      setThumbCacheBust((prev) => {
        const next = { ...prev };
        for (const id of ids) next[id] = bustAt;
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not rotate photos");
    } finally {
      setQuickRotatingId(null);
      setBatchRotating(false);
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

  async function findAudience() {
    if (!lot || !token) return;
    audienceHuntPostAckRef.current = false;
    audienceHuntSawActiveRef.current = false;
    setAudienceHunting(true);
    setAudienceHuntError("");
    setAudienceHuntNotice("");
    setAudienceCopyMsg("");
    try {
      const updated = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}/audience-hunt`, token, {
        method: "POST",
        body: JSON.stringify({ force: audienceForce })
      })) as Lot;
      setLot(updated);
      audienceHuntPostAckRef.current = true;
      if (updated.audience_hunting) {
        audienceHuntSawActiveRef.current = true;
        setAudienceHunting(true);
        return;
      }
      setAudienceHunting(false);
      if (updated.audience_hunt_error) {
        setAudienceHuntError(parseFastApiErrorMessage(updated.audience_hunt_error));
        return;
      }
      const skipped = Boolean(updated.audience?.skip_reason) && !(updated.audience?.groups?.length);
      setAudienceHuntNotice(
        skipped
          ? updated.audience?.skip_reason || "Skipped — everyday household lot."
          : updated.audience?.groups?.length
            ? `Found ${updated.audience.groups.length} public group${updated.audience.groups.length === 1 ? "" : "s"}.`
            : "Hunt finished — no public groups found."
      );
    } catch (e) {
      audienceHuntPostAckRef.current = false;
      audienceHuntSawActiveRef.current = false;
      setAudienceHuntNotice("");
      setAudienceHuntError(
        e instanceof Error ? parseFastApiErrorMessage(e.message) || "Audience hunt failed" : "Audience hunt failed"
      );
      setAudienceHunting(false);
    }
  }

  async function setAudienceHold(next: boolean) {
    if (!lot || !token) return;
    setAudienceHoldBusy(true);
    setAudienceHuntError("");
    try {
      const updated = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}`, token, {
        method: "PATCH",
        body: JSON.stringify({ audience_hold: next })
      })) as Lot;
      setLot((prev) => (prev ? { ...prev, ...updated } : updated));
    } catch (e) {
      setAudienceHuntError(e instanceof Error ? e.message : "Could not update hold");
    } finally {
      setAudienceHoldBusy(false);
    }
  }

  async function fillSearchTagsFromListing() {
    if (!lot || !token) return;
    setSearchTagsBusy(true);
    setError("");
    try {
      const res = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}/search-tags/suggest`, token, {
        method: "POST",
        body: JSON.stringify({})
      })) as { search_tags?: string[] };
      updateListingFields({ search_tags: res.search_tags || [] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not fill search tags");
    } finally {
      setSearchTagsBusy(false);
    }
  }

  async function copyAudienceText(label: string, text: string) {
    const t = text.trim();
    if (!t) return;
    try {
      await navigator.clipboard.writeText(t);
      setAudienceCopyMsg(`Copied ${label}`);
    } catch {
      setAudienceCopyMsg("Could not copy — select the text instead.");
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
          search_tags: lot.search_tags || [],
          starting_price: lot.starting_price,
          reserve_price: lot.reserve_price ?? null,
          condition_text: lot.condition_text,
          is_completed: lot.is_completed
        })
      });
      setLot(updated);
      setListingDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function postToCtSeller(force = false) {
    if (!lot) return;
    const pick = Number(ctSellerPickId || mappedCtSellerSaleId || "");
    if (!Number.isFinite(pick) || pick <= 0) {
      setCtSellerErr("Pick a CT Seller auction for this catalog sale first.");
      return;
    }
    if (listingDirty) {
      setCtSellerErr("Save listing changes before posting to CT Seller.");
      return;
    }
    setCtSellerPostBusy(true);
    setCtSellerErr("");
    setCtSellerMsg("");
    try {
      const res = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}/post-to-ctseller`, token, {
        method: "POST",
        body: JSON.stringify({
          ctseller_sale_id: pick,
          save_sale_mapping: true,
          force
        })
      })) as {
        ctseller_item_id: number;
        photos_uploaded: number;
        ctseller_sale_id: number;
      };
      setMappedCtSellerSaleId(res.ctseller_sale_id);
      setCtSellerPickId(String(res.ctseller_sale_id));
      setLot((prev) =>
        prev
          ? {
              ...prev,
              ctseller_item_id: res.ctseller_item_id,
              ctseller_posted_at: new Date().toISOString(),
              ctseller_sale_id: res.ctseller_sale_id,
              ctseller_item_status: "Pending",
              ctseller_sold_price: null
            }
          : prev
      );
      setCtSellerMsg(
        `Posted to CT Seller as item #${res.ctseller_item_id}` +
          (res.photos_uploaded ? ` (${res.photos_uploaded} photo${res.photos_uploaded === 1 ? "" : "s"})` : "")
      );
    } catch (e) {
      setCtSellerErr(e instanceof Error ? e.message : "Post to CT Seller failed");
    } finally {
      setCtSellerPostBusy(false);
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

  async function sortCatalogPhotosForListing() {
    if (!lot || !token) return;
    if (
      !window.confirm(
        "Sort this lot’s photos for a listing?\n\n" +
          "Order becomes: group shots of the whole lot, then each item with its angles, then labels/stickers, then measurements, then SKU tape.\n" +
          "You can still drag photos afterward."
      )
    ) {
      return;
    }
    setReorderBusy(true);
    setError("");
    setOrganizeMsg("");
    try {
      const photos = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}/photos/sort-for-listing`, token, {
        method: "POST"
      })) as LotPhoto[];
      setCatalogPhotos(Array.isArray(photos) ? photos : []);
      setOrganizeMsg("Sorted photos for listing (group → each item → labels → measurements).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sort photos for listing");
    } finally {
      setReorderBusy(false);
    }
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

  async function refetchLotVideos() {
    if (!token || !lot?.id) return;
    try {
      const videos = (await apiFetch(`/lots/${encodeURIComponent(lot.id)}/videos`, token)) as LotVideo[];
      setCatalogVideos(Array.isArray(videos) ? videos : []);
      setVideoError("");
    } catch {
      setVideoError("Could not load videos for this lot.");
    }
  }

  async function uploadLotVideos(fileList: FileList | null) {
    if (!lot || !token || !fileList || fileList.length === 0) return;
    setVideoBusy(true);
    setVideoError("");
    try {
      const fd = new FormData();
      for (const f of Array.from(fileList)) {
        fd.append("files", f);
      }
      const raw = await apiUploadFiles(`/lots/${encodeURIComponent(lot.id)}/videos/upload`, token, fd);
      if (!Array.isArray(raw)) {
        throw new Error("Upload response was not a video list — check API version.");
      }
      setCatalogVideos(raw as LotVideo[]);
    } catch (e) {
      setVideoError(e instanceof Error ? e.message : "Could not upload videos");
    } finally {
      setVideoBusy(false);
      if (addVideosInputRef.current) addVideosInputRef.current.value = "";
    }
  }

  async function playLotVideo(videoId: string) {
    if (!lot || !token) return;
    setVideoError("");
    try {
      const res = (await apiFetch(
        `/lots/${encodeURIComponent(lot.id)}/videos/${encodeURIComponent(videoId)}/media-url`,
        token
      )) as { url?: string };
      if (!res.url) throw new Error("No playable URL");
      setPlayingVideoUrl(res.url);
    } catch (e) {
      setVideoError(e instanceof Error ? e.message : "Could not play video");
    }
  }

  async function deleteLotVideo(videoId: string) {
    if (!lot || !token) return;
    if (!window.confirm("Remove this video from the lot?")) return;
    setVideoBusy(true);
    setVideoError("");
    try {
      await apiFetch(`/lots/${encodeURIComponent(lot.id)}/videos/delete`, token, {
        method: "POST",
        body: JSON.stringify({ video_ids: [videoId] })
      });
      if (playingVideoUrl) setPlayingVideoUrl(null);
      await refetchLotVideos();
    } catch (e) {
      setVideoError(e instanceof Error ? e.message : "Could not delete video");
    } finally {
      setVideoBusy(false);
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
        setSaleLots(saleLotOptionsFromRows(rows));
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
      {lot.listing_needs_review ? (
        <div className="ct-banner ct-banner--warning">
          <strong>This listing needs a rewrite before publishing.</strong>{" "}
          {lot.listing_review_reason === "garbled_title"
            ? "The title looks like raw text copied off a tag rather than a real auction title."
            : lot.listing_review_reason === "placeholder_description"
              ? "The description is a generic placeholder — add a real description."
              : "The title is a generic placeholder — give this lot a real title."}{" "}
          Edit the title/description below (or use Regenerate) before you export or post.
        </div>
      ) : null}
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
      {showAudienceHuntProgress ? (
        <AudienceHuntProgress
          title="Audience hunt"
          phaseLabel={audienceHuntPhaseLabel(lot?.audience_hunt_phase)}
          done={lot?.audience_hunt_phase === "structuring" ? 1 : 0}
          total={2}
          percent={audienceHuntStepPercent(lot?.audience_hunt_phase)}
        />
      ) : null}
      {audienceHuntError ? (
        <div className="ct-banner ct-banner--error" role="alert">
          <strong style={{ display: "block", marginBottom: "0.2rem" }}>Audience hunt failed</strong>
          {audienceHuntError}
        </div>
      ) : null}
      {!showAudienceHuntProgress && audienceHuntNotice ? (
        <div
          className="ct-banner"
          style={{
            borderColor: "rgba(74, 222, 128, 0.35)",
            background: "rgba(34, 197, 94, 0.1)"
          }}
          role="status"
        >
          {audienceHuntNotice}
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
    Boolean(aiRegenNotice) ||
    showAudienceHuntProgress ||
    Boolean(audienceHuntError) ||
    Boolean(audienceHuntNotice);

  const lotMobileSaveBar = (
    <>
      {listingDirty ? (
        <span className="ct-app-mobile-bar__hint" aria-live="polite">
          Unsaved changes
        </span>
      ) : null}
      <button
        type="button"
        className="ct-app-mobile-bar__btn ct-app-mobile-bar__btn--primary"
        onClick={() => void save()}
        disabled={saving || !listingDirty}
        aria-busy={saving}
      >
        <Save size={20} aria-hidden />
        {saving ? "Saving…" : "Save listing"}
      </button>
    </>
  );

  return (
    <AppShell topBar={lotTopBar} actions={shellActions} mobileBottomBar={lotMobileSaveBar}>
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
            <span
              className={`ct-lot-detail-status${
                lot.ctseller_item_id || lot.ctseller_posted_at ? " ct-lot-detail-status--done" : ""
              }`}
              title={
                lot.ctseller_item_id || lot.ctseller_posted_at
                  ? [
                      lot.ctseller_item_status || "Posted to CT Seller",
                      lot.ctseller_sale_id ? `sale #${lot.ctseller_sale_id}` : "",
                      lot.ctseller_sold_price != null && lot.ctseller_sold_price > 0
                        ? `$${lot.ctseller_sold_price}`
                        : ""
                    ]
                      .filter(Boolean)
                      .join(" · ")
                  : "Not posted to CT Seller yet"
              }
            >
              {lot.ctseller_item_id || lot.ctseller_posted_at
                ? lot.ctseller_item_status
                  ? lot.ctseller_item_status
                  : lot.ctseller_sale_id
                    ? `Posted · #${lot.ctseller_sale_id}`
                    : "Posted"
                : "Not posted"}
            </span>
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
              <button
                type="button"
                className="ct-btn"
                onClick={() => void postToCtSeller(Boolean(lot.ctseller_item_id))}
                disabled={
                  ctSellerPostBusy ||
                  saving ||
                  listingDirty ||
                  showAiRegenProgress ||
                  audienceHeld ||
                  !(lot.sku || "").trim() ||
                  !(lot.auction_title || "").trim()
                }
                aria-busy={ctSellerPostBusy}
                title={
                  audienceHeld
                    ? "Held for collector groups — turn off Hold for groups to post"
                    : listingDirty
                      ? "Save listing first"
                      : lot.ctseller_item_id
                        ? "Already posted — click to post again"
                        : "Create this lot on seller.ctbids.com"
                }
              >
                {ctSellerPostBusy
                  ? "Posting…"
                  : lot.ctseller_item_id
                    ? "Re-post to CT Seller"
                    : "Post to CT Seller"}
              </button>
            </div>
          </div>
        </header>

        {showLotAlerts ? <div className="ct-lot-detail-alerts">{lotWarnings}</div> : null}

        <section className="ct-dash-panel" style={{ marginTop: "0.75rem" }}>
          <h2 className="ct-dash-panel__title">Sale</h2>
          <p className="ct-upload-intro" style={{ marginTop: 0 }}>
            This lot is in <strong>{saleName || "an unnamed sale"}</strong>. Move it if it was
            cataloged under the wrong sale.
          </p>
          <MoveLotsToSale
            token={token}
            lotIds={[lot.id]}
            currentSaleId={lot.sale_id}
            currentSaleName={saleName}
            sales={catalogSales}
            postedCount={lot.ctseller_item_id || lot.ctseller_posted_at ? 1 : 0}
            disabled={saving || organizeBusy}
            onMoved={(result) => {
              setLot((prev) =>
                prev ? { ...prev, sale_id: result.destination_sale_id } : prev
              );
              setSaleName(result.destination_sale_name);
              setSaleLots([]);
            }}
          />
        </section>

        <section className="ct-dash-panel" style={{ marginTop: "0.75rem" }}>
          <h2 className="ct-dash-panel__title">Audience</h2>
          <p className="ct-upload-intro" style={{ marginTop: 0 }}>
            Find public **United States** collector groups for nicer or niche lots. Staff still paste
            into the groups — this only hunts names and URLs. Everyday household lots are skipped unless
            you force a hunt. International / worldwide clubs are omitted (small items can ship inside
            the US). Does not scrape members or auto-post.
          </p>
          {audienceCopyMsg ? (
            <p className="ct-muted" style={{ marginTop: 0, fontSize: "0.85rem" }} role="status">
              {audienceCopyMsg}
            </p>
          ) : null}
          <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "center" }}>
            {isAccountOwner ? (
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                onClick={() => void findAudience()}
                disabled={showAudienceHuntProgress || saving}
                aria-busy={showAudienceHuntProgress}
              >
                {showAudienceHuntProgress ? (
                  <span className="ct-row" style={{ gap: "0.4rem", alignItems: "center" }}>
                    <Loader2 size={16} className="ct-spin" aria-hidden />
                    Hunting…
                  </span>
                ) : (
                  "Find audience"
                )}
              </button>
            ) : (
              <p className="ct-muted" style={{ margin: 0, fontSize: "0.85rem" }}>
                Ask an owner to run a collector-group hunt. Existing results stay visible.
              </p>
            )}
            {isAccountOwner ? (
            <label className="ct-row" style={{ gap: "0.4rem", fontSize: "0.85rem" }}>
              <input
                type="checkbox"
                checked={audienceForce}
                onChange={(e) => setAudienceForce(e.target.checked)}
                disabled={showAudienceHuntProgress}
              />
              <span className="ct-muted">Force hunt (even everyday lots)</span>
            </label>
            ) : null}
            <label className="ct-row" style={{ gap: "0.4rem", fontSize: "0.85rem" }}>
              <input
                type="checkbox"
                checked={audienceHeld}
                onChange={(e) => void setAudienceHold(e.target.checked)}
                disabled={audienceHoldBusy || saving}
              />
              <span>Hold for groups (skip CT Bids post)</span>
            </label>
          </div>
          {lot.audience?.skip_reason && !(lot.audience.groups?.length) ? (
            <p className="ct-field-hint" style={{ marginTop: "0.75rem" }}>
              {lot.audience.skip_reason}
            </p>
          ) : null}
          {lot.audience?.groups && lot.audience.groups.length > 0 ? (
            <ul style={{ margin: "0.85rem 0 0", paddingLeft: "1.15rem" }}>
              {lot.audience.groups.map((g, i) => (
                <li key={`${g.name}-${i}`} style={{ marginBottom: "0.45rem" }}>
                  {g.url ? (
                    <a href={g.url} target="_blank" rel="noopener noreferrer">
                      {g.name}
                    </a>
                  ) : (
                    <strong>{g.name}</strong>
                  )}
                  {g.why ? <span className="ct-muted"> — {g.why}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
          {lot.audience?.blurb_ctbids || lot.audience?.blurb_hold ? (
            <div
              className="ct-row ct-row--gap-lg"
              style={{ flexWrap: "wrap", alignItems: "stretch", marginTop: "0.9rem" }}
            >
              {lot.audience?.blurb_ctbids ? (
                <div style={{ flex: "1 1 240px", minWidth: 220 }}>
                  <label className="ct-label" htmlFor="audience-blurb-ctbids">
                    Invite to CT Bids
                  </label>
                  <textarea
                    id="audience-blurb-ctbids"
                    className="ct-input"
                    rows={5}
                    readOnly
                    value={lot.audience?.blurb_ctbids || ""}
                    style={{ resize: "vertical" }}
                  />
                  <button
                    type="button"
                    className="ct-btn"
                    style={{ marginTop: "0.4rem" }}
                    onClick={() => void copyAudienceText("CT Bids blurb", lot.audience?.blurb_ctbids || "")}
                  >
                    Copy
                  </button>
                </div>
              ) : null}
              {lot.audience?.blurb_hold ? (
                <div style={{ flex: "1 1 240px", minWidth: 220 }}>
                  <label className="ct-label" htmlFor="audience-blurb-hold">
                    Hold / talk to groups first
                  </label>
                  <textarea
                    id="audience-blurb-hold"
                    className="ct-input"
                    rows={5}
                    readOnly
                    value={lot.audience?.blurb_hold || ""}
                    style={{ resize: "vertical" }}
                  />
                  <button
                    type="button"
                    className="ct-btn"
                    style={{ marginTop: "0.4rem" }}
                    onClick={() => void copyAudienceText("hold blurb", lot.audience?.blurb_hold || "")}
                  >
                    Copy
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="ct-dash-panel" style={{ marginTop: "0.75rem" }}>
          <h2 className="ct-dash-panel__title">CT Seller</h2>
          <p className="ct-upload-intro" style={{ marginTop: 0 }}>
            Post this lot straight to seller.ctbids.com. Connect your Seller login on{" "}
            <Link href="/account">Account</Link>, pick the matching CTBids auction, then post. SKU-tape
            photos stay in this gallery and are <strong>not</strong> uploaded to CT Bids.
          </p>
          {audienceHeld ? (
            <div className="ct-banner ct-banner--warning">
              Held for collector groups — bulk and single CT Seller post will skip this lot until you uncheck{" "}
              <strong>Hold for groups</strong> above.
            </div>
          ) : null}
          {ctSellerMsg ? <div className="ct-banner ct-banner--success">{ctSellerMsg}</div> : null}
          {ctSellerErr ? <div className="ct-banner ct-banner--error">{ctSellerErr}</div> : null}
          {lot.ctseller_item_id ? (
            <p className="ct-muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
              Last posted as CT Seller item <strong>#{lot.ctseller_item_id}</strong>
              {lot.ctseller_sale_id ? ` · CT Bids sale #${lot.ctseller_sale_id}` : ""}
              {lot.ctseller_item_status ? ` · ${lot.ctseller_item_status}` : ""}
              {lot.ctseller_sold_price != null && lot.ctseller_sold_price > 0
                ? ` · $${lot.ctseller_sold_price}`
                : ""}
              {lot.ctseller_posted_at
                ? ` · ${new Date(lot.ctseller_posted_at).toLocaleString()}`
                : ""}
            </p>
          ) : null}
          <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
            <div style={{ minWidth: 220, flex: "1 1 240px" }}>
              <label className="ct-label" htmlFor="ctseller-sale-pick">
                CT Seller auction
              </label>
              <select
                id="ctseller-sale-pick"
                className="ct-input"
                value={ctSellerPickId}
                onChange={(e) => setCtSellerPickId(e.target.value)}
                disabled={ctSellerPostBusy}
              >
                <option value="">Select auction…</option>
                {ctSellerRemoteSales.map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.title}
                    {s.status ? ` (${s.status})` : ""}
                    {s.starttime ? ` · starts ${String(s.starttime).slice(0, 10)}` : ""} · #{s.id}
                  </option>
                ))}
              </select>
              {!ctSellerRemoteSales.length ? (
                <p className="ct-field-hint">
                  No auctions loaded — if CT Seller is already connected on Account, refresh this
                  page. Only reconnect there if your CT Seller password changed.
                </p>
              ) : null}
            </div>
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              onClick={() => void postToCtSeller(Boolean(lot.ctseller_item_id))}
              disabled={
                ctSellerPostBusy ||
                saving ||
                listingDirty ||
                audienceHeld ||
                !(lot.sku || "").trim() ||
                !(lot.auction_title || "").trim() ||
                !ctSellerPickId
              }
              title={audienceHeld ? "Held for collector groups — turn off Hold for groups to post" : undefined}
            >
              {ctSellerPostBusy
                ? "Posting…"
                : lot.ctseller_item_id
                  ? "Re-post lot"
                  : "Post lot"}
            </button>
          </div>
        </section>

        <section className="ct-dash-panel">
          <h2 className="ct-dash-panel__title">
            Catalog photos
            {catalogPhotos.length ? (
              <span className="ct-muted" style={{ fontWeight: 500, marginLeft: "0.35rem" }}>
                ({catalogPhotos.length})
              </span>
            ) : null}
          </h2>
          <p className="ct-upload-intro">
            Gallery order is export order. New lots are auto-sorted for listing (group shots, then
            each item with its angles, then labels, then measurements). Drag to fine-tune; tap a photo to crop.
          </p>
          <input
            ref={addPhotosInputRef}
            type="file"
            multiple
            accept="image/*,.heic,.heif"
            style={{ display: "none" }}
            onChange={(e) => void uploadAdditionalCatalogPhotos(e.target.files)}
          />
          <div className="ct-upload-options" role="group" aria-label="Photo actions">
            <div className="ct-upload-option ct-upload-option--featured">
              <span className="ct-upload-option__icon" aria-hidden>
                <ImagePlus size={20} strokeWidth={1.75} />
              </span>
              <div className="ct-upload-option__text">
                <span className="ct-upload-option__title">Add photos</span>
                <span className="ct-upload-option__desc">
                  Upload more frames to this listing
                  {CTBIDS_MAX_PHOTOS_PER_LOT != null ? ` (max ${CTBIDS_MAX_PHOTOS_PER_LOT})` : ""}.
                </span>
              </div>
              <button
                type="button"
                className="ct-btn ct-btn--primary ct-upload-option__btn"
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
                    ? "Limit reached"
                    : "Add photos…"}
              </button>
            </div>
            {catalogPhotos.length > 1 ? (
              <div className="ct-upload-option">
                <span className="ct-upload-option__icon" aria-hidden>
                  <LayoutList size={20} strokeWidth={1.75} />
                </span>
                <div className="ct-upload-option__text">
                  <span className="ct-upload-option__title">Sort for listing</span>
                  <span className="ct-upload-option__desc">
                    Group shots first, then each item with its angles, then labels, then measurements. SKU tape last.
                  </span>
                </div>
                <button
                  type="button"
                  className="ct-btn ct-upload-option__btn"
                  disabled={organizeBusy || reorderBusy || addPhotosBusy}
                  onClick={() => void sortCatalogPhotosForListing()}
                >
                  {reorderBusy ? "Sorting…" : "Sort for listing"}
                </button>
              </div>
            ) : null}
            {catalogPhotos.length > 0 ? (
              <div className="ct-upload-option">
                <span className="ct-upload-option__icon" aria-hidden>
                  <Archive size={20} strokeWidth={1.75} />
                </span>
                <div className="ct-upload-option__text">
                  <span className="ct-upload-option__title">Download ZIP</span>
                  <span className="ct-upload-option__desc">
                    Full-resolution files for this lot — same images used for export and CT Seller.
                  </span>
                </div>
                <button
                  type="button"
                  className="ct-btn ct-upload-option__btn"
                  disabled={zipDownloading || addPhotosBusy}
                  onClick={() => void downloadLotPhotosZip()}
                >
                  {zipDownloading ? "Preparing…" : "Download ZIP"}
                </button>
              </div>
            ) : null}
            {catalogPhotos.length > 0 ? (
              <div className="ct-upload-option">
                <span className="ct-upload-option__icon" aria-hidden>
                  <RotateCw size={20} strokeWidth={1.75} />
                </span>
                <div className="ct-upload-option__text">
                  <span className="ct-upload-option__title">
                    {selectedPhotoIds.size > 0
                      ? `Rotate ${selectedPhotoIds.size} selected`
                      : "Rotate all photos"}
                  </span>
                  <span className="ct-upload-option__desc">
                    {selectedPhotoIds.size > 0
                      ? "Turns checked photos 90°. Uncheck to rotate every photo on this lot instead."
                      : "Turns every photo on this lot 90°. Check only the sideways ones if group shots are already upright."}
                  </span>
                </div>
                <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="ct-btn ct-upload-option__btn"
                    disabled={
                      batchRotating ||
                      quickRotatingId !== null ||
                      organizeBusy ||
                      reorderBusy ||
                      addPhotosBusy ||
                      catalogPhotos.every((p) => p.file_on_disk === false)
                    }
                    title="Rotate 90° counter-clockwise"
                    onClick={() =>
                      void rotateLotPhotos(
                        -90,
                        selectedPhotoIds.size > 0 ? Array.from(selectedPhotoIds) : null
                      )
                    }
                  >
                    {batchRotating ? "Rotating…" : (
                      <>
                        <RotateCcw size={16} strokeWidth={2} aria-hidden /> ↺
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--primary ct-upload-option__btn"
                    disabled={
                      batchRotating ||
                      quickRotatingId !== null ||
                      organizeBusy ||
                      reorderBusy ||
                      addPhotosBusy ||
                      catalogPhotos.every((p) => p.file_on_disk === false)
                    }
                    title="Rotate 90° clockwise"
                    onClick={() =>
                      void rotateLotPhotos(
                        90,
                        selectedPhotoIds.size > 0 ? Array.from(selectedPhotoIds) : null
                      )
                    }
                  >
                    {batchRotating ? "Rotating…" : (
                      <>
                        <RotateCw size={16} strokeWidth={2} aria-hidden /> ↻
                      </>
                    )}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
          <details className="ct-help-details">
            <summary>Photo guide</summary>
            <div className="ct-help-details__body">
              <ul>
                <li>
                  Gallery order is left to right, then the next row — the same order CT Bids and ZIP
                  downloads use.
                </li>
                <li>
                  <strong>Sort for listing</strong> groups photos the way shoppers expect (wide group
                  shot, then each item as a chapter, then labels, then measuring-tape shots). Drag a
                  thumbnail to tweak the order after that.
                </li>
                <li>Order is saved for ZIP downloads, CT Bids posting, and AI.</li>
                <li>
                  Use <strong>↺ ↻</strong> on a thumbnail for a quick rotate, or{" "}
                  <strong>Rotate all photos</strong> (or rotate checked ones) to flip many at once.
                  Click the image for crop and other edits in the full viewer.
                </li>
                <li>
                  <strong>Shift+click</strong> a checkbox to select a range; <strong>Ctrl or ⌘+click</strong> to toggle
                  one photo (for organize actions below).
                </li>
                <li>
                  Grid images are high-res previews (not tiny dashboard thumbs). Click a photo for the full
                  original (crop/rotate).
                </li>
              </ul>
            </div>
          </details>
          {photosError ? (
            <div className="ct-banner ct-banner--error" style={{ marginBottom: "0.75rem", marginTop: "0.65rem" }}>
              {photosError}
            </div>
          ) : null}
          <div
            className="ct-lot-detail-photo-grid-wrap"
            style={catalogPhotos.length > 0 ? { marginTop: "0.65rem" } : undefined}
          >
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
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: "0.85rem"
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
                    {(() => {
                      const badge = listingRoleBadge(p);
                      return badge ? (
                      <span style={{ fontWeight: 600, marginLeft: "0.28rem", opacity: 0.9 }}>
                        {badge}
                      </span>
                      ) : null;
                    })()}
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
                        batchRotating ||
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
                        batchRotating ||
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
                      maxPx={1600}
                      maxHeight={420}
                      minHeight={200}
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
          </div>
        </section>

        <section className="ct-dash-panel">
          <h2 className="ct-dash-panel__title">
            Videos
            {catalogVideos.length ? (
              <span className="ct-muted" style={{ fontWeight: 500, marginLeft: "0.35rem" }}>
                ({catalogVideos.length})
              </span>
            ) : null}
          </h2>
          <p className="ct-upload-intro">
            Short walk-around clips from the phone app. They stay on Estate-EZ and are not posted
            to CT Bids or sent through AI catalog.
          </p>
          <input
            ref={addVideosInputRef}
            type="file"
            multiple
            accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm"
            style={{ display: "none" }}
            onChange={(e) => void uploadLotVideos(e.target.files)}
          />
          <div className="ct-upload-options" role="group" aria-label="Video actions">
            <div className="ct-upload-option">
              <div className="ct-upload-option__text">
                <span className="ct-upload-option__title">Add video</span>
                <span className="ct-upload-option__desc">MP4 or MOV, up to 80 MB, max 8 per lot.</span>
              </div>
              <button
                type="button"
                className="ct-btn ct-btn--primary ct-upload-option__btn"
                disabled={videoBusy}
                onClick={() => addVideosInputRef.current?.click()}
              >
                {videoBusy ? "Uploading…" : "Add video…"}
              </button>
            </div>
          </div>
          {videoError ? (
            <div className="ct-banner ct-banner--error" style={{ marginTop: "0.75rem" }}>
              {videoError}
            </div>
          ) : null}
          {playingVideoUrl ? (
            <video
              key={playingVideoUrl}
              src={playingVideoUrl}
              controls
              playsInline
              style={{ width: "100%", maxHeight: 420, marginTop: "0.85rem", background: "#111", borderRadius: 10 }}
            />
          ) : null}
          {catalogVideos.length ? (
            <ul style={{ listStyle: "none", padding: 0, margin: "0.85rem 0 0" }}>
              {catalogVideos.map((video, index) => (
                <li
                  key={video.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "0.75rem",
                    padding: "0.45rem 0",
                    borderTop: "1px solid var(--ct-border, #2a2a2e)"
                  }}
                >
                  <span className="ct-muted" style={{ minWidth: "1.5rem" }}>
                    {index + 1}
                  </span>
                  <span style={{ flex: 1, wordBreak: "break-word" }}>{video.original_name}</span>
                  <button type="button" className="ct-btn" disabled={videoBusy} onClick={() => void playLotVideo(video.id)}>
                    Play
                  </button>
                  <button type="button" className="ct-btn" disabled={videoBusy} onClick={() => void deleteLotVideo(video.id)}>
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ct-muted" style={{ marginTop: "0.75rem" }}>
              No videos on this lot yet. Record one in the phone app (Photo / Video toggle).
            </p>
          )}
        </section>

        {showOrganizePanel ? (
          <section className="ct-dash-panel">
            <h2 className="ct-dash-panel__title">Organize photos</h2>
            <p className="ct-upload-intro">Select photos in the grid above, then choose an action below.</p>
            {organizeMsg ? (
              <div className="ct-banner ct-banner--success" style={{ marginBottom: "0.75rem" }}>
                {organizeMsg}
              </div>
            ) : null}
            <div className="ct-upload-options ct-upload-options--stacked">
              {catalogPhotos.length > 0 ? (
                <div className="ct-upload-option ct-upload-option--stacked-card">
                  <div className="ct-upload-option__head">
                    <span className="ct-upload-option__icon" aria-hidden>
                      <RotateCw size={20} strokeWidth={1.75} />
                    </span>
                    <div className="ct-upload-option__text">
                      <span className="ct-upload-option__title">
                        {selectedPhotoIds.size > 0
                          ? `Rotate ${selectedPhotoIds.size} selected`
                          : "Rotate all photos"}
                      </span>
                      <span className="ct-upload-option__desc">
                        Rewrites the files on the server (same 90° as the thumbnail arrows). Confirm if nothing is
                        checked — upright group shots would turn sideways too.
                      </span>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                    <button
                      type="button"
                      className="ct-btn ct-upload-option__field-btn"
                      disabled={
                        organizeBusy ||
                        reorderBusy ||
                        saving ||
                        batchRotating ||
                        quickRotatingId !== null ||
                        catalogPhotos.length === 0
                      }
                      onClick={() =>
                        void rotateLotPhotos(
                          -90,
                          selectedPhotoIds.size > 0 ? Array.from(selectedPhotoIds) : null
                        )
                      }
                    >
                      {batchRotating ? "Rotating…" : "↺ Left"}
                    </button>
                    <button
                      type="button"
                      className="ct-btn ct-btn--primary ct-upload-option__field-btn"
                      disabled={
                        organizeBusy ||
                        reorderBusy ||
                        saving ||
                        batchRotating ||
                        quickRotatingId !== null ||
                        catalogPhotos.length === 0
                      }
                      onClick={() =>
                        void rotateLotPhotos(
                          90,
                          selectedPhotoIds.size > 0 ? Array.from(selectedPhotoIds) : null
                        )
                      }
                    >
                      {batchRotating ? "Rotating…" : "↻ Right"}
                    </button>
                  </div>
                </div>
              ) : null}
              {catalogPhotos.length > 0 ? (
                <div className="ct-upload-option ct-upload-option--stacked-card">
                  <div className="ct-upload-option__head">
                    <span className="ct-upload-option__icon" aria-hidden>
                      <Trash2 size={20} strokeWidth={1.75} />
                    </span>
                    <div className="ct-upload-option__text">
                      <span className="ct-upload-option__title">Delete selected</span>
                      <span className="ct-upload-option__desc">
                        Permanently remove checked photos from this lot and the server.
                      </span>
                      <span className="ct-upload-option__meta ct-muted">{selectedPhotoIds.size} selected</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="ct-btn ct-btn--danger ct-upload-option__field-btn"
                    onClick={() => void deleteSelectedPhotos()}
                    disabled={
                      organizeBusy ||
                      reorderBusy ||
                      saving ||
                      selectedPhotoIds.size === 0 ||
                      catalogPhotos.length === 0
                    }
                  >
                    {organizeBusy ? "Working…" : "Delete photos"}
                  </button>
                </div>
              ) : null}
              {catalogPhotos.length > 0 ? (
                <div className="ct-upload-option ct-upload-option--expand">
                  <div className="ct-upload-option__head">
                    <span className="ct-upload-option__icon" aria-hidden>
                      <ArrowRightLeft size={20} strokeWidth={1.75} />
                    </span>
                    <div className="ct-upload-option__text">
                      <span className="ct-upload-option__title">Move or split</span>
                      <span className="ct-upload-option__desc">
                        Send selected photos to another lot or start a new listing.
                      </span>
                    </div>
                  </div>
                  <div className="ct-upload-option__body">
                    <div className="ct-upload-option__body-actions">
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        disabled={organizeBusy || reorderBusy || catalogPhotos.length === 0}
                        onClick={selectAllCatalogPhotos}
                      >
                        Select all
                      </button>
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        disabled={organizeBusy || reorderBusy || selectedPhotoIds.size === 0}
                        onClick={clearPhotoSelection}
                      >
                        Clear selection
                      </button>
                    </div>
                    {transferTargetId === TRANSFER_DEST_NEW_LOT ? (
                      <label className="ct-upload-option__field ct-muted">
                        Optional title for the new listing
                        <input
                          className="ct-input"
                          placeholder='e.g. "SA004 — orange pair"'
                          value={splitNewLotTitle}
                          onChange={(e) => setSplitNewLotTitle(e.target.value)}
                          disabled={organizeBusy || reorderBusy}
                          autoComplete="off"
                        />
                      </label>
                    ) : null}
                    <select
                      className="ct-input ct-upload-option__field-input"
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
                      className={`ct-btn ct-upload-option__field-btn${transferTargetId === TRANSFER_DEST_NEW_LOT ? " ct-btn--primary" : ""}`}
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
                <div className="ct-upload-option ct-upload-option--expand">
                  <div className="ct-upload-option__head">
                    <span className="ct-upload-option__icon" aria-hidden>
                      <GitMerge size={20} strokeWidth={1.75} />
                    </span>
                    <div className="ct-upload-option__text">
                      <span className="ct-upload-option__title">Merge lot</span>
                      <span className="ct-upload-option__desc">
                        Pull every photo from another listing into this one; the other row is removed.
                      </span>
                    </div>
                  </div>
                  <div className="ct-upload-option__body">
                    <select
                      className="ct-input ct-upload-option__field-input"
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
                      className="ct-btn ct-upload-option__field-btn"
                      disabled={
                        organizeBusy || reorderBusy || !mergeSourceId || !saleLots.some((x) => x.id === mergeSourceId)
                      }
                      onClick={() => void mergeSourceIntoThisLot()}
                    >
                      {organizeBusy || reorderBusy ? "Working…" : "Merge into this lot"}
                    </button>
                    <label className="ct-row" style={{ gap: "0.45rem" }}>
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
                </div>
              ) : null}
            </div>
            <details className="ct-help-details">
              <summary>Organize guide</summary>
              <div className="ct-help-details__body">
                <ul>
                  <li>
                    <strong>Move or split</strong> when only some photos belong elsewhere, or two tape numbers were
                    merged into one row by mistake. One photo must stay on this lot.
                  </li>
                  <li>
                    <strong>Merge lot</strong> when every photo from another row should join this listing at once.
                  </li>
                  <li>
                    To move only some photos into a new row, use <strong>Move or split</strong> and pick{" "}
                    <strong>New listing</strong> in the destination menu.
                  </li>
                  <li>
                    Deleting photos removes files only — use <strong>Delete lot</strong> in Listing details to remove
                    the entire row.
                  </li>
                </ul>
              </div>
            </details>
          </section>
        ) : null}

        <section id="listing-details" className="ct-dash-panel ct-listing-form" style={{ scrollMarginTop: "0.75rem" }}>
          <h2 className="ct-dash-panel__title">Listing details</h2>
          <p className="ct-upload-intro">
            Edit auction copy and metadata for this lot, then save. Changes apply to exports after you save.
          </p>

          <div className="ct-listing-form__sections">
            <section className="ct-listing-form__section" aria-labelledby="listing-identity-heading">
              <h3 id="listing-identity-heading" className="ct-listing-form__section-title">
                Identity
              </h3>
              <div>
                <label className="ct-label" htmlFor="lot-sku">
                  Estate lot SKU
                </label>
                <input
                  id="lot-sku"
                  className="ct-input"
                  inputMode="text"
                  autoComplete="off"
                  placeholder="e.g. 034 or SA013"
                  value={lot.sku ?? ""}
                  onChange={(e) => updateListingFields({ sku: e.target.value.trim() || null })}
                />
                <p className="ct-field-hint">
                  Handwritten code on colored estate tape. Save after you fix it — the yellow “review SKU” flag on the
                  dashboard clears.
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
                  onChange={(e) => updateListingFields({ auction_title: e.target.value })}
                />
              </div>
            </section>

            <section className="ct-listing-form__section" aria-labelledby="listing-copy-heading">
              <h3 id="listing-copy-heading" className="ct-listing-form__section-title">
                Listing copy
              </h3>
              <div>
                <label className="ct-label" htmlFor="pro-desc">
                  Professional description
                </label>
                <textarea
                  id="pro-desc"
                  className="ct-textarea ct-textarea--listing-pro"
                  value={lot.professional_description}
                  onChange={(e) => updateListingFields({ professional_description: e.target.value })}
                />
              </div>
              <div>
                <label className="ct-label" htmlFor="spec-lines">
                  Specification lines
                </label>
                <textarea
                  id="spec-lines"
                  className="ct-textarea ct-textarea--compact"
                  placeholder="One label per line, e.g. Brand: …"
                  value={lot.specification_lines.join("\n")}
                  onChange={(e) =>
                    updateListingFields({ specification_lines: e.target.value.split("\n").filter(Boolean) })
                  }
                />
                <details className="ct-help-details ct-listing-form__help">
                  <summary>What goes in specification lines?</summary>
                  <div className="ct-help-details__body">
                    <p style={{ margin: "0 0 0.5rem" }}>One line per fact — use labels like:</p>
                    <ul>
                      <li>
                        <strong>Brand:</strong> confirmed maker when visible
                      </li>
                      <li>
                        <strong>Dimensions:</strong> / <strong>Measurement:</strong> when a ruler or tape is in the
                        photos
                      </li>
                      <li>
                        <strong>Condition note:</strong> wear or damage called out in the listing
                      </li>
                      <li>
                        <strong>Size / tags:</strong> for apparel only (otherwise use Sizes / tags below)
                      </li>
                    </ul>
                  </div>
                </details>
              </div>
            </section>

            <section className="ct-listing-form__section" aria-labelledby="listing-details-heading">
              <h3 id="listing-details-heading" className="ct-listing-form__section-title">
                Details
              </h3>
              <div className="ct-listing-form__details-grid">
                <div className="ct-listing-form__details-span">
                  <label className="ct-label" htmlFor="brands">
                    Brands
                  </label>
                  <input
                    id="brands"
                    className="ct-input"
                    placeholder="Comma separated"
                    value={lot.brands.join(", ")}
                    onChange={(e) =>
                      updateListingFields({
                        brands: e.target.value
                          .split(",")
                          .map((x) => x.trim())
                          .filter(Boolean)
                      })
                    }
                  />
                </div>
                <div className="ct-listing-form__details-span">
                  <label className="ct-label" htmlFor="tags">
                    Sizes / tags
                  </label>
                  <input
                    id="tags"
                    className="ct-input"
                    placeholder="Clothing &amp; shoes only — comma separated"
                    value={lot.labels_and_tags.join(", ")}
                    onChange={(e) =>
                      updateListingFields({
                        labels_and_tags: e.target.value
                          .split(",")
                          .map((x) => x.trim())
                          .filter(Boolean)
                      })
                    }
                  />
                </div>
                <div className="ct-listing-form__details-span">
                  <label className="ct-label" htmlFor="search-tags">
                    CT Bids search tags
                  </label>
                  <input
                    id="search-tags"
                    className="ct-input"
                    placeholder="Brand, model, material, era — comma separated"
                    value={(lot.search_tags || []).join(", ")}
                    onChange={(e) =>
                      updateListingFields({
                        search_tags: e.target.value
                          .split(",")
                          .map((x) => x.trim())
                          .filter(Boolean)
                      })
                    }
                  />
                  <p className="ct-muted" style={{ margin: "0.4rem 0 0", fontSize: "0.82rem", lineHeight: 1.4 }}>
                    Posted to CT Seller tags so shoppers can find this lot. Use words they would type for this
                    piece — not Rolex or Porsche unless that is actually on the item.
                  </p>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    style={{ marginTop: "0.45rem" }}
                    disabled={searchTagsBusy}
                    onClick={() => void fillSearchTagsFromListing()}
                  >
                    {searchTagsBusy ? "Filling…" : "Fill from listing"}
                  </button>
                </div>
                <div>
                  <label className="ct-label" htmlFor="price">
                    Starting price
                  </label>
                  <div className="ct-input-prefix">
                    <span className="ct-input-prefix__symbol" aria-hidden>
                      $
                    </span>
                    <input
                      id="price"
                      className="ct-input"
                      type="number"
                      inputMode="decimal"
                      step="0.01"
                      min="0"
                      placeholder="0.00"
                      value={lot.starting_price ?? ""}
                      onChange={(e) =>
                        updateListingFields({ starting_price: e.target.value ? Number(e.target.value) : null })
                      }
                    />
                  </div>
                </div>
                <div>
                  <label className="ct-label" htmlFor="reserve">
                    Reserve price
                  </label>
                  <div className="ct-input-prefix">
                    <span className="ct-input-prefix__symbol" aria-hidden>
                      $
                    </span>
                    <input
                      id="reserve"
                      className="ct-input"
                      type="number"
                      inputMode="decimal"
                      step="0.01"
                      min="0"
                      placeholder="none"
                      value={lot.reserve_price ?? ""}
                      onChange={(e) =>
                        updateListingFields({ reserve_price: e.target.value ? Number(e.target.value) : null })
                      }
                    />
                  </div>
                  <p className="ct-muted" style={{ margin: "0.4rem 0 0", fontSize: "0.82rem", lineHeight: 1.4 }}>
                    Loaded from CT Seller when you Check CT Bids sales / Load from CT Seller. Saving
                    here also writes the lot, and bulk apply can push to CT Seller.
                  </p>
                </div>
                <div>
                  <label className="ct-label" htmlFor="condition">
                    Condition
                  </label>
                  <select
                    id="condition"
                    className="ct-input"
                    value={lot.condition_text}
                    onChange={(e) => updateListingFields({ condition_text: e.target.value })}
                    title="CT Seller item condition tiers"
                  >
                    <option value="">Select condition…</option>
                    {conditionSelectOptions(lot.condition_text, ctSellerConditions).map((opt) => (
                      <option key={opt.condition} value={opt.condition} title={opt.description || undefined}>
                        {opt.condition}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </section>
          </div>

          <div className="ct-listing-form__actions">
            <button
              type="button"
              className="ct-btn ct-btn--primary ct-listing-form__save-desktop"
              onClick={() => void save()}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save"}
            </button>
            {isAccountOwner ? (
              <button type="button" className="ct-btn ct-btn--danger-outline" onClick={() => void removeLot()}>
                Delete lot
              </button>
            ) : null}
          </div>
          {error ? (
            <div className="ct-banner ct-banner--error" style={{ marginBottom: 0 }}>
              {error}
            </div>
          ) : null}
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

