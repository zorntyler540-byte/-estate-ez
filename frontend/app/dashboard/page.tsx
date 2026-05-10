"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Database,
  FileSpreadsheet,
  FileText,
  Minus,
  Pencil,
  Plus,
  RotateCcw,
  RotateCw,
  Trash2,
  X
} from "lucide-react";
import { LotThumbnail } from "../../components/LotThumbnail";
import { SiteHeader } from "../../components/SiteHeader";
import { apiFetch, apiUploadFiles, API_BASE } from "../../lib/api";

const SALE_STORAGE_KEY = "ct_selected_sale_id";

/** Saved when opening lot Details; restored after ``← Back to dashboard`` on the lot page. */
const DASHBOARD_SCROLL_Y_KEY = "ct_dashboard_scroll_y";
const DASHBOARD_SCROLL_SAVED_AT_KEY = "ct_dashboard_scroll_saved_at";
const DASHBOARD_SCROLL_RESTORE_PENDING_KEY = "ct_dashboard_scroll_restore_pending";
const DASHBOARD_SCROLL_MAX_AGE_MS = 12 * 60 * 1000;

function rememberDashboardScrollBeforeLotDetails() {
  try {
    sessionStorage.setItem(DASHBOARD_SCROLL_Y_KEY, String(window.scrollY));
    sessionStorage.setItem(DASHBOARD_SCROLL_SAVED_AT_KEY, String(Date.now()));
  } catch {
    /* private mode */
  }
}

/** One multipart request per batch so 1000+ photos do not hit proxy/browser body limits. */
const UPLOAD_FILE_CHUNK = 200;

/** CTBids listing cap — one-lot uploads cannot exceed this many catalog photos. */
const CTBIDS_MAX_PHOTOS_PER_LOT = 40;

/** Digital zoom for one-lot camera preview + JPEG crop; 1 = no zoom. */
const LOT_CAMERA_ZOOM_MIN = 1;
const LOT_CAMERA_ZOOM_MAX = 4;
const LOT_CAMERA_ZOOM_STEP = 0.25;

function lotCameraPinchDistance(e: TouchEvent): number {
  if (e.touches.length < 2) return 0;
  const a = e.touches[0];
  const b = e.touches[1];
  if (!a || !b) return 0;
  const dx = a.clientX - b.clientX;
  const dy = a.clientY - b.clientY;
  return Math.hypot(dx, dy);
}

/** Reused for a short shutter-style beep after each create-one-lot snap. */
let lotCameraShutterAudioCtx: AudioContext | null = null;

function playLotCameraShutterSound() {
  try {
    const w = window as Window & typeof globalThis & { webkitAudioContext?: typeof AudioContext };
    const AC = window.AudioContext ?? w.webkitAudioContext;
    if (!AC) return;
    if (!lotCameraShutterAudioCtx || lotCameraShutterAudioCtx.state === "closed") {
      lotCameraShutterAudioCtx = new AC();
    }
    const ctx = lotCameraShutterAudioCtx;
    const run = () => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.connect(gain);
      gain.connect(ctx.destination);
      const t = ctx.currentTime;
      osc.frequency.setValueAtTime(920, t);
      osc.frequency.exponentialRampToValueAtTime(240, t + 0.055);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.linearRampToValueAtTime(0.065, t + 0.002);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.075);
      osc.start(t);
      osc.stop(t + 0.08);
    };
    void (ctx.state === "suspended" ? ctx.resume() : Promise.resolve()).then(run);
  } catch {
    /* autoplay policy, private mode, or no Web Audio */
  }
}

/** Light haptic (Android / some devices) plus shutter sound after a successful snap. */
function triggerLotCameraSnapFeedback() {
  try {
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate(12);
    }
  } catch {
    /* ignore */
  }
  playLotCameraShutterSound();
}

function loadImageFromFile(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const u = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(u);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(u);
      reject(new Error("Could not load image"));
    };
    img.src = u;
  });
}

/** Basic tone controls; values are CSS ``filter`` percentages (100 = unchanged). */
type RasterizeBasicAdjust = {
  brightnessPct: number;
  contrastPct: number;
  saturationPct: number;
};

const RASTER_ADJUST_DEFAULT: RasterizeBasicAdjust = {
  brightnessPct: 100,
  contrastPct: 100,
  saturationPct: 100
};

function clampRasterAdjust(a: RasterizeBasicAdjust): RasterizeBasicAdjust {
  return {
    brightnessPct: Math.max(50, Math.min(150, Math.round(a.brightnessPct))),
    contrastPct: Math.max(50, Math.min(150, Math.round(a.contrastPct))),
    saturationPct: Math.max(0, Math.min(150, Math.round(a.saturationPct)))
  };
}

function rasterAdjustCssFilter(a: RasterizeBasicAdjust): string {
  const c = clampRasterAdjust(a);
  return `brightness(${c.brightnessPct}%) contrast(${c.contrastPct}%) saturate(${c.saturationPct}%)`;
}

function isRasterAdjustDefault(a: RasterizeBasicAdjust): boolean {
  return (
    a.brightnessPct === 100 && a.contrastPct === 100 && a.saturationPct === 100
  );
}

/**
 * Uniform edge trim (0–35, same as CSS ``inset`` % on each side) then 0/90/180/270° rotation;
 * optional brightness/contrast/saturation via canvas ``filter``; re-encode as JPEG.
 */
async function rasterizeLotCameraEdit(
  file: File,
  rotationDeg: number,
  edgeTrimPct: number,
  adjust: RasterizeBasicAdjust = RASTER_ADJUST_DEFAULT
): Promise<File | null> {
  const trim = Math.max(0, Math.min(35, edgeTrimPct));
  const rot = (((rotationDeg % 360) + 360) % 360) as 0 | 90 | 180 | 270;
  if (rot !== 0 && rot !== 90 && rot !== 180 && rot !== 270) return null;

  let img: HTMLImageElement;
  try {
    img = await loadImageFromFile(file);
  } catch {
    return null;
  }
  const w0 = img.naturalWidth;
  const h0 = img.naturalHeight;
  if (w0 < 2 || h0 < 2) return null;

  const sx = Math.floor((w0 * trim) / 100);
  const sy = Math.floor((h0 * trim) / 100);
  const sw = Math.floor(w0 - (2 * w0 * trim) / 100);
  const sh = Math.floor(h0 - (2 * h0 * trim) / 100);
  if (sw < 2 || sh < 2) return null;

  const c1 = document.createElement("canvas");
  c1.width = sw;
  c1.height = sh;
  const g1 = c1.getContext("2d");
  if (!g1) return null;
  g1.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);

  const rad = (rot * Math.PI) / 180;
  const outW = rot % 180 === 0 ? sw : sh;
  const outH = rot % 180 === 0 ? sh : sw;
  const c2 = document.createElement("canvas");
  c2.width = outW;
  c2.height = outH;
  const g2 = c2.getContext("2d");
  if (!g2) return null;
  if (!isRasterAdjustDefault(adjust)) {
    g2.filter = rasterAdjustCssFilter(adjust);
  }
  g2.translate(outW / 2, outH / 2);
  g2.rotate(rad);
  g2.imageSmoothingEnabled = true;
  g2.imageSmoothingQuality = "high";
  g2.drawImage(c1, -sw / 2, -sh / 2);

  const baseName = (file.name || "lot.jpg").replace(/\.[^.]+$/i, "") || "lot";
  return new Promise((resolve) => {
    c2.toBlob(
      (blob) => {
        if (!blob) {
          resolve(null);
          return;
        }
        resolve(new File([blob], `${baseName}.jpg`, { type: "image/jpeg" }));
      },
      "image/jpeg",
      0.92
    );
  });
}

function readStoredToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

function readStoredSaleId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(SALE_STORAGE_KEY) || "";
}

function isAuthFailureMessage(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("invalid authentication") ||
    m.includes("401") ||
    m.includes("unauthorized")
  );
}

/** One retry helps flaky mobile / wake-from-sleep where the first ``fetch`` throws. */
async function withNetworkRetry<T>(fn: () => Promise<T>, attempts = 2): Promise<T> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      const msg = e instanceof Error ? e.message : "";
      const maybeTransient =
        /failed to fetch|networkerror|network request failed|load failed|aborted/i.test(msg) ||
        (e instanceof TypeError && !msg);
      if (!maybeTransient || i === attempts - 1) throw e;
      await new Promise((r) => setTimeout(r, 280 + i * 220));
    }
  }
  throw last;
}

type Sale = {
  id: string;
  name: string;
  closed_at: string | null;
  created_at: string;
};

type AdminAccount = {
  id: string;
  name: string;
  owner_email?: string | null;
  plan_code: string;
  is_active: boolean;
  created_at: string;
  user_count: number;
  sale_count: number;
  lot_count: number;
  job_count: number;
};

type AdminUsageAccount = {
  account_id: string;
  account_name: string;
  jobs_started: number;
  jobs_completed: number;
  jobs_failed: number;
  photos_uploaded: number;
  lots_created: number;
  avg_photos_per_job: number;
  avg_job_duration_seconds: number;
  est_input_tokens: number;
  est_output_tokens: number;
  est_total_cost_usd: number;
};

type AdminUsageSummary = {
  since_days: number;
  total_accounts: number;
  totals: AdminUsageAccount;
  by_account: AdminUsageAccount[];
};

type Lot = {
  id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  is_completed: boolean;
  completed_at: string | null;
  starting_price: number | null;
  condition_text: string;
  photo_count: number;
  has_thumbnail: boolean;
  created_at: string;
  sku_needs_review: boolean;
  many_photos_review: boolean;
  sku_duplicate_in_sale: boolean;
  sku_text_mismatch: boolean;
};

/** One inventory row streamed from ``GET /jobs/{id}/events`` (``lot_ready``). */
type LiveLotRowPayload = Lot;

function normalizeStreamedLot(row: LiveLotRowPayload): Lot {
  return {
    id: row.id,
    sale_id: row.sale_id ?? "",
    sku: row.sku ?? null,
    sku_needs_review: Boolean(row.sku_needs_review),
    many_photos_review: Boolean(row.many_photos_review),
    sku_duplicate_in_sale: Boolean(
      (row as { sku_duplicate_in_sale?: unknown }).sku_duplicate_in_sale
    ),
    sku_text_mismatch: Boolean((row as { sku_text_mismatch?: unknown }).sku_text_mismatch),
    auction_title: row.auction_title ?? "",
    is_completed: Boolean(row.is_completed),
    completed_at: row.completed_at ?? null,
    condition_text: row.condition_text ?? "",
    starting_price: row.starting_price ?? null,
    photo_count: typeof row.photo_count === "number" ? row.photo_count : Number(row.photo_count) || 0,
    has_thumbnail: Boolean(row.has_thumbnail),
    created_at: row.created_at ?? ""
  };
}

type Job = {
  id: string;
  user_id: string;
  sale_id: string;
  status: string;
  input_folder: string;
  created_at: string;
  error_message?: string;
  inherit_sku_from_subfolder?: boolean;
  single_lot_mode?: boolean;
  single_lot_manual_sku?: string | null;
};

type SaleZipExport = {
  id: string;
  sale_id: string;
  status: string;
  total_photos_expected: number;
  photo_files_written: number;
  error_message?: string | null;
  expires_at?: string | null;
  created_at: string;
  ready: boolean;
};

/** One placeholder row per queued photo (stable slot for “where the next result lands”). */
type PhotoPendingSlot = {
  id: string;
  jobId: string;
  index: number;
  total: number;
  status: "queued" | "analyzed";
  fileName?: string;
  previewTitle?: string;
  previewSku?: string | null;
};

const CATALOG_IMAGE_RE = /\.(jpe?g|png|webp|heic|heif)$/i;

function isCatalogImageFile(f: File): boolean {
  if (f.type.startsWith("image/")) return true;
  return CATALOG_IMAGE_RE.test(f.name);
}

function folderLabelFromFile(f: File): string {
  const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
  const parts = rel.split("/").filter(Boolean);
  if (parts.length >= 2) return parts[0] ?? "Folder";
  return "Selected photos";
}

function revokeObjectUrls(urls: string[]) {
  urls.forEach((u) => URL.revokeObjectURL(u));
}

type FolderPickPreview = {
  folderLabel: string;
  imageFiles: File[];
  thumbUrls: string[];
  skippedCount: number;
  /** When true, this upload is processed as one catalog lot (one SKU row) for every photo. */
  singleLotMode?: boolean;
};

type SingleLotStagingApply = { pick: FolderPickPreview; droppedFromCap: number };

function capIncomingSingleLotFiles(existingCount: number, incoming: File[]): { files: File[]; dropped: number } {
  const room = Math.max(0, CTBIDS_MAX_PHOTOS_PER_LOT - existingCount);
  if (incoming.length <= room) return { files: incoming, dropped: 0 };
  return { files: incoming.slice(0, room), dropped: incoming.length - room };
}

/** Merge or replace staged files for one-lot uploads (picker + inline camera). Caps at ``CTBIDS_MAX_PHOTOS_PER_LOT``. */
function applySingleLotFilesToStaging(
  prev: FolderPickPreview | null,
  incoming: File[],
  extraSkipped: number
): SingleLotStagingApply {
  const appendSingleLot = Boolean(prev?.singleLotMode);
  if (appendSingleLot) {
    const { files: cappedIncoming, dropped } = capIncomingSingleLotFiles(prev!.imageFiles.length, incoming);
    const merged = [...prev!.imageFiles, ...cappedIncoming];
    const mergedSkipped = prev!.skippedCount + extraSkipped + dropped;
    if (prev!.thumbUrls.length) revokeObjectUrls(prev!.thumbUrls);
    const thumbUrls = merged.map((f) => URL.createObjectURL(f));
    return {
      pick: {
        folderLabel:
          merged.length === 1 ? folderLabelFromFile(merged[0]!) : `One lot (${merged.length} photos)`,
        imageFiles: merged,
        thumbUrls,
        skippedCount: mergedSkipped,
        singleLotMode: true
      },
      droppedFromCap: dropped
    };
  }
  const { files: cappedIncoming, dropped } = capIncomingSingleLotFiles(0, incoming);
  if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
  const thumbUrls = cappedIncoming.map((f) => URL.createObjectURL(f));
  return {
    pick: {
      folderLabel:
        cappedIncoming.length === 1
          ? folderLabelFromFile(cappedIncoming[0]!)
          : `One lot (${cappedIncoming.length} photos)`,
      imageFiles: cappedIncoming,
      thumbUrls,
      skippedCount: extraSkipped + dropped,
      singleLotMode: true
    },
    droppedFromCap: dropped
  };
}

export default function DashboardPage() {
  const router = useRouter();
  const folderInputRef = useRef<HTMLInputElement>(null);
  const photosInputRef = useRef<HTMLInputElement>(null);
  /** Next file-picker result: normal batch vs one combined lot. */
  const uploadPickModeRef = useRef<"batch" | "single_lot">("batch");
  /** True after "Create one lot" until multi-file pick, upload, clear, or picker cancel. Used to chain camera opens on mobile. */
  const singleLotCaptureChainingRef = useRef(false);
  const folderPickRef = useRef<FolderPickPreview | null>(null);
  /** Inline multi-snap camera (getUserMedia); avoids iOS blocking repeated file-picker opens. */
  const lotCameraVideoRef = useRef<HTMLVideoElement | null>(null);
  const lotCameraStreamRef = useRef<MediaStream | null>(null);
  const lotCameraSnapsRef = useRef<File[]>([]);
  const [lotCameraOpen, setLotCameraOpen] = useState(false);
  const [lotCameraStream, setLotCameraStream] = useState<MediaStream | null>(null);
  const [lotCameraSnaps, setLotCameraSnaps] = useState<File[]>([]);
  const [lotCameraSnapThumbUrls, setLotCameraSnapThumbUrls] = useState<string[]>([]);
  const [lotCameraPreviewReady, setLotCameraPreviewReady] = useState(false);
  /** Index into ``lotCameraSnapThumbUrls`` for full-screen preview, or null. */
  const [lotCameraViewerIndex, setLotCameraViewerIndex] = useState<number | null>(null);
  /** Optional post-capture edit (rotate + trim edges); null when editor closed. */
  const [lotCameraEditIndex, setLotCameraEditIndex] = useState<number | null>(null);
  const [lotCameraEditRotation, setLotCameraEditRotation] = useState(0);
  const [lotCameraEditTrim, setLotCameraEditTrim] = useState(0);
  const [lotCameraEditBrightness, setLotCameraEditBrightness] = useState(100);
  const [lotCameraEditContrast, setLotCameraEditContrast] = useState(100);
  const [lotCameraEditSaturation, setLotCameraEditSaturation] = useState(100);
  const [lotCameraEditBusy, setLotCameraEditBusy] = useState(false);
  const [lotCameraZoom, setLotCameraZoom] = useState(LOT_CAMERA_ZOOM_MIN);
  const lotCameraZoomRef = useRef(LOT_CAMERA_ZOOM_MIN);
  const lotCameraZoomWrapRef = useRef<HTMLDivElement | null>(null);
  const lotCameraPinchRef = useRef<{ startDist: number; startZoom: number } | null>(null);
  const lotCameraStripRef = useRef<HTMLDivElement | null>(null);
  /** Scrollable column inside the camera modal — kept at bottom so Snap/Done stay in view on mobile. */
  const lotCameraScrollRef = useRef<HTMLDivElement | null>(null);
  const inventoryFirstLoad = useRef(true);
  const salesLoadId = useRef(0);
  const inventoryLoadId = useRef(0);
  const [token, setToken] = useState<string>(readStoredToken);
  const [sales, setSales] = useState<Sale[]>([]);
  const [selectedSaleId, setSelectedSaleId] = useState<string>(readStoredSaleId);
  /** Bumps after bfcache restore or return from lot detail so sales/lots/jobs refetch. */
  const [dataReloadNonce, setDataReloadNonce] = useState(0);
  const selectedSaleIdRef = useRef<string>("");
  const [newSaleName, setNewSaleName] = useState("");
  const [creatingSale, setCreatingSale] = useState(false);
  const [closingSale, setClosingSale] = useState(false);
  const [isMasterAdmin, setIsMasterAdmin] = useState(false);
  const [adminLoadAttempted, setAdminLoadAttempted] = useState(false);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminSavingAccountId, setAdminSavingAccountId] = useState("");
  const [adminError, setAdminError] = useState("");
  const [adminAccounts, setAdminAccounts] = useState<AdminAccount[]>([]);
  const [adminSearch, setAdminSearch] = useState("");
  const [adminNameDraftById, setAdminNameDraftById] = useState<Record<string, string>>({});
  const [adminPlanDraftById, setAdminPlanDraftById] = useState<Record<string, string>>({});
  const [adminUsage, setAdminUsage] = useState<AdminUsageSummary | null>(null);
  const [adminUsageLoading, setAdminUsageLoading] = useState(false);
  const [lots, setLots] = useState<Lot[]>([]);
  /** Filter ``GET /lots`` to rows with SKU sanity flags (duplicate digit-SKU in sale, or mixed lot numbers in text). */
  const [inventorySkuFlagFilter, setInventorySkuFlagFilter] = useState<
    "" | "duplicate" | "text_mismatch" | "any"
  >("");
  const inventorySkuFlagFilterRef = useRef<"" | "duplicate" | "text_mismatch" | "any">("");
  inventorySkuFlagFilterRef.current = inventorySkuFlagFilter;
  const [jobs, setJobs] = useState<Job[]>([]);
  const [folder, setFolder] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  /** Shown during multi-request folder uploads (large batches). */
  const [uploadProgress, setUploadProgress] = useState("");
  /** When set, top-level subfolders named with 2–6 digits (e.g. ``035/``) set each lot's SKU on finalize. */
  const [inheritSkuFromSubfolder, setInheritSkuFromSubfolder] = useState(false);
  const [exporting, setExporting] = useState<"" | "markdown" | "json" | "csv">("");
  const [saleZipExport, setSaleZipExport] = useState<SaleZipExport | null>(null);
  const [saleZipBusy, setSaleZipBusy] = useState(false);
  const [folderPick, setFolderPick] = useState<FolderPickPreview | null>(null);
  /** Full-screen preview index for staged upload thumbnails, or null. */
  const [stagingViewerIndex, setStagingViewerIndex] = useState<number | null>(null);
  /** Post-stage edit (rotate + trim) for one staged file. */
  const [stagingEditIndex, setStagingEditIndex] = useState<number | null>(null);
  const [stagingEditRotation, setStagingEditRotation] = useState(0);
  const [stagingEditTrim, setStagingEditTrim] = useState(0);
  const [stagingEditBrightness, setStagingEditBrightness] = useState(100);
  const [stagingEditContrast, setStagingEditContrast] = useState(100);
  const [stagingEditSaturation, setStagingEditSaturation] = useState(100);
  const [stagingEditBusy, setStagingEditBusy] = useState(false);
  /** Fixed jump scroll buttons when the dashboard is taller than the viewport. */
  const [showDashboardScrollJumpNav, setShowDashboardScrollJumpNav] = useState(false);
  /** Optional lot/tape marking for one-lot uploads (camera + file picker). */
  const [singleLotManualSku, setSingleLotManualSku] = useState("");
  /** One loading row per catalog photo while the active job runs (SSE). */
  const [photoPendingSlots, setPhotoPendingSlots] = useState<PhotoPendingSlot[]>([]);
  const [inInventoryPhase, setInInventoryPhase] = useState(false);
  /** Server-reported position in the post-Gemini catalog merge loop (SSE ``inventory_step``). */
  const [catalogStepProgress, setCatalogStepProgress] = useState<{
    index: number;
    total: number;
    fileName: string;
  } | null>(null);
  const [showCreateOneLot, setShowCreateOneLot] = useState(false);
  /** Bumps when returning to the tab so ``EventSource`` reconnects and replays recent SSE (mobile sleep / flaky LTE). */
  const [jobSseSession, setJobSseSession] = useState(0);
  const sseRetryAfterErrorRef = useRef(0);
  folderPickRef.current = folderPick;
  selectedSaleIdRef.current = selectedSaleId;

  const busyProcessing = jobs.some((j) => j.status === "processing");
  const busyProcessingRef = useRef(false);
  busyProcessingRef.current = busyProcessing;
  const processingJobId = useMemo(
    () => jobs.find((j) => j.status === "processing")?.id ?? null,
    [jobs]
  );
  const selectedSale = useMemo(() => sales.find((s) => s.id === selectedSaleId) ?? null, [sales, selectedSaleId]);
  const selectedSaleClosed = Boolean(selectedSale?.closed_at);
  const filteredAdminAccounts = useMemo(() => {
    const q = adminSearch.trim().toLowerCase();
    if (!q) return adminAccounts;
    return adminAccounts.filter((a) => {
      const name = (a.name || "").toLowerCase();
      const email = (a.owner_email || "").toLowerCase();
      const id = (a.id || "").toLowerCase();
      return name.includes(q) || email.includes(q) || id.includes(q);
    });
  }, [adminAccounts, adminSearch]);

  const geminiAnalyzedCount = useMemo(() => {
    if (!photoPendingSlots.length) return { done: 0, total: 0 };
    const total = photoPendingSlots.length;
    const done = photoPendingSlots.filter((s) => s.status === "analyzed").length;
    return { done, total };
  }, [photoPendingSlots]);

  useEffect(() => {
    try {
      if (sessionStorage.getItem("ct_from_lot_detail") === "1") {
        sessionStorage.removeItem("ct_from_lot_detail");
        setToken(localStorage.getItem("token") || "");
        setDataReloadNonce((n) => n + 1);
      }
    } catch {
      /* private mode */
    }
  }, []);

  useEffect(() => {
    const onPageShow = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      try {
        setToken(localStorage.getItem("token") || "");
        setDataReloadNonce((n) => n + 1);
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  useEffect(() => {
    setSaleZipExport(null);
  }, [selectedSaleId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const update = () => {
      const narrow = window.matchMedia("(max-width: 900px)").matches;
      const coarse = window.matchMedia("(pointer: coarse)").matches;
      setShowCreateOneLot(narrow || coarse);
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const update = () => {
      const doc = document.documentElement;
      setShowDashboardScrollJumpNav(doc.scrollHeight > window.innerHeight + 120);
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
  }, [
    lots.length,
    jobs.length,
    folderPick,
    lotCameraOpen,
    lotCameraSnaps.length,
    photoPendingSlots.length,
    uploadProgress,
    inInventoryPhase,
    catalogStepProgress,
    saleZipExport,
    dataReloadNonce,
    selectedSaleId,
    saleZipBusy,
    loading
  ]);

  /** After returning from lot detail via ``Back to dashboard``, scroll to where Details was opened. */
  useEffect(() => {
    if (typeof window === "undefined" || !token || loading) return;
    let cancelled = false;
    let yToRestore: number | null = null;
    try {
      if (sessionStorage.getItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY) !== "1") return;
      const raw = sessionStorage.getItem(DASHBOARD_SCROLL_Y_KEY);
      const rawAt = sessionStorage.getItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
      const savedAt = rawAt !== null ? Number.parseInt(rawAt, 10) : NaN;
      if (
        !Number.isFinite(savedAt) ||
        Date.now() - savedAt > DASHBOARD_SCROLL_MAX_AGE_MS
      ) {
        sessionStorage.removeItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY);
        if (raw !== null) sessionStorage.removeItem(DASHBOARD_SCROLL_Y_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
        return;
      }
      const y = raw !== null ? Number.parseFloat(raw) : NaN;
      if (!Number.isFinite(y) || y < 0) {
        sessionStorage.removeItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY);
        if (raw !== null) sessionStorage.removeItem(DASHBOARD_SCROLL_Y_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
        return;
      }
      yToRestore = y;
    } catch {
      return;
    }

    const apply = () => {
      if (cancelled || yToRestore === null) return;
      window.scrollTo({ top: yToRestore, behavior: "auto" });
    };
    apply();
    const raf = window.requestAnimationFrame(apply);
    const t1 = window.setTimeout(apply, 80);
    const t2 = window.setTimeout(apply, 280);
    const t3 = window.setTimeout(apply, 700);
    const tClear = window.setTimeout(() => {
      if (cancelled) return;
      try {
        sessionStorage.removeItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_Y_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
      } catch {
        /* private mode */
      }
    }, 900);
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.clearTimeout(t3);
      window.clearTimeout(tClear);
    };
  }, [loading, token, lots.length, jobs.length, dataReloadNonce]);

  useEffect(() => {
    if (!token || !saleZipExport?.id) return;
    if (saleZipExport.status !== "queued" && saleZipExport.status !== "processing") return;
    const id = saleZipExport.id;
    let cancelled = false;
    let intervalId: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      try {
        const row = (await apiFetch(`/exports/sale-photos-zip/${encodeURIComponent(id)}`, token)) as SaleZipExport;
        if (!cancelled) setSaleZipExport(row);
        if (row.ready || row.status === "failed") {
          cancelled = true;
          if (intervalId !== undefined) window.clearInterval(intervalId);
        }
      } catch {
        /* keep last row; user can refresh */
      }
    };
    intervalId = window.setInterval(() => void tick(), 2500);
    void tick();
    return () => {
      cancelled = true;
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
  }, [token, saleZipExport?.id]);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      inventoryFirstLoad.current = true;
      setLoading(false);
      setSales([]);
      setSelectedSaleId("");
      return;
    }
    setLoading(true);
    const loadId = ++salesLoadId.current;
    void withNetworkRetry(() => apiFetch("/sales", t))
      .then((sl) => {
        if (loadId !== salesLoadId.current) return;
        setError("");
        const arr = sl as Sale[];
        setSales(arr);
        if (!arr.length) {
          setSelectedSaleId("");
          setLots([]);
          setJobs([]);
          setLoading(false);
          return;
        }
        setSelectedSaleId((prev) => {
          if (prev && arr.some((s) => s.id === prev)) return prev;
          const stored = localStorage.getItem(SALE_STORAGE_KEY);
          if (stored && arr.some((s) => s.id === stored)) return stored;
          return arr[0]!.id;
        });
      })
      .catch((e) => {
        if (loadId !== salesLoadId.current) return;
        const msg = e instanceof Error ? e.message : "Load failed";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
        setLoading(false);
      });
  }, [router, dataReloadNonce]);

  useEffect(() => {
    if (!token || !selectedSaleId) {
      return;
    }
    const t = token;
    if (inventoryFirstLoad.current) {
      setLoading(true);
    }
    localStorage.setItem(SALE_STORAGE_KEY, selectedSaleId);
    const loadId = ++inventoryLoadId.current;
    const skuQ =
      inventorySkuFlagFilter === ""
        ? ""
        : `&sku_flags=${encodeURIComponent(inventorySkuFlagFilter)}`;
    void withNetworkRetry(() =>
      Promise.all([
        apiFetch(`/lots?sale_id=${encodeURIComponent(selectedSaleId)}${skuQ}`, t),
        apiFetch(`/jobs?sale_id=${encodeURIComponent(selectedSaleId)}`, t)
      ])
    )
      .then(([lotsData, jobsData]) => {
        if (loadId !== inventoryLoadId.current) return;
        setError("");
        setLots(lotsData as Lot[]);
        setJobs(jobsData as Job[]);
      })
      .catch((e) => {
        if (loadId !== inventoryLoadId.current) return;
        const msg = e instanceof Error ? e.message : "Load failed";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
      })
      .finally(() => {
        if (loadId !== inventoryLoadId.current) return;
        if (inventoryFirstLoad.current) {
          inventoryFirstLoad.current = false;
          setLoading(false);
        }
      });
  }, [token, selectedSaleId, router, dataReloadNonce, inventorySkuFlagFilter]);

  useEffect(() => {
    if (!token) {
      setIsMasterAdmin(false);
      setAdminLoadAttempted(false);
      setAdminAccounts([]);
      setAdminPlanDraftById({});
      setAdminError("");
      return;
    }
    let cancelled = false;
    setAdminLoading(true);
    setAdminLoadAttempted(true);
    setAdminError("");
    void apiFetch("/admin/accounts", token)
      .then((rows) => {
        if (cancelled) return;
        const arr = rows as AdminAccount[];
        setAdminAccounts(arr);
        setIsMasterAdmin(true);
        const drafts: Record<string, string> = {};
        const nameDrafts: Record<string, string> = {};
        arr.forEach((a) => {
          drafts[a.id] = a.plan_code || "beta_free";
          nameDrafts[a.id] = a.name || "";
        });
        setAdminNameDraftById(nameDrafts);
        setAdminPlanDraftById(drafts);
        setAdminUsageLoading(true);
        void apiFetch("/admin/analytics?since_days=30", token)
          .then((u) => {
            if (!cancelled) setAdminUsage(u as AdminUsageSummary);
          })
          .catch(() => {
            if (!cancelled) setAdminUsage(null);
          })
          .finally(() => {
            if (!cancelled) setAdminUsageLoading(false);
          });
      })
      .catch((e) => {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : "Could not load admin accounts";
        if (msg.includes("403") || msg.toLowerCase().includes("master admin")) {
          setIsMasterAdmin(false);
          setAdminAccounts([]);
          setAdminNameDraftById({});
          setAdminPlanDraftById({});
          setAdminUsage(null);
          return;
        }
        setIsMasterAdmin(false);
        setAdminError(msg);
      })
      .finally(() => {
        if (!cancelled) setAdminLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, dataReloadNonce]);

  useEffect(() => {
    const bumpSse = () => {
      if (document.visibilityState !== "visible" || !busyProcessingRef.current) return;
      setJobSseSession((n) => n + 1);
    };
    document.addEventListener("visibilitychange", bumpSse);
    return () => document.removeEventListener("visibilitychange", bumpSse);
  }, []);

  useEffect(() => {
    if (!token || loading || !processingJobId) return;

    const url = `${API_BASE}/jobs/${processingJobId}/events?access_token=${encodeURIComponent(token)}`;
    sseRetryAfterErrorRef.current = 0;
    const es = new EventSource(url);
    es.onopen = () => {
      sseRetryAfterErrorRef.current = 0;
    };

    const refreshJobsAndLots = () => {
      const sid = selectedSaleIdRef.current;
      if (!sid) return;
      const fq =
        inventorySkuFlagFilterRef.current === ""
          ? ""
          : `&sku_flags=${encodeURIComponent(inventorySkuFlagFilterRef.current)}`;
      void apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)
        .then((d) => setJobs(d as Job[]))
        .catch(() => {
          /* ignore */
        });
      void apiFetch(`/lots?sale_id=${encodeURIComponent(sid)}${fq}`, token)
        .then((d) => setLots(d as Lot[]))
        .catch(() => {
          /* ignore */
        });
    };

    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as {
          type?: string;
          status?: string;
          job_id?: string;
          error_message?: string | null;
          lot?: LiveLotRowPayload;
          total_photos?: number;
          phase?: string;
          index?: number;
          total?: number;
          file_name?: string;
          preview?: { auction_title: string; sku: string | null };
        };
        if (
          msg.type === "batch_start" &&
          typeof msg.total_photos === "number" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          const n = msg.total_photos;
          const jid = processingJobId;
          setInInventoryPhase(false);
          setCatalogStepProgress(null);
          setPhotoPendingSlots(() =>
            Array.from({ length: n }, (_, i) => ({
              id: `${jid}-slot-${i}`,
              jobId: jid,
              index: i,
              total: n,
              status: "queued" as const
            }))
          );
        }
        if (
          msg.type === "photo_analyzed" &&
          typeof msg.index === "number" &&
          typeof msg.total === "number" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          setPhotoPendingSlots((prev) => {
            if (prev.length === 0) {
              const jid = processingJobId;
              const n = msg.total!;
              const base: PhotoPendingSlot[] = Array.from({ length: n }, (_, i) => ({
                id: `${jid}-slot-${i}`,
                jobId: jid,
                index: i,
                total: n,
                status: "queued"
              }));
              return base.map((s) =>
                s.index === msg.index
                  ? {
                      ...s,
                      status: "analyzed",
                      fileName: msg.file_name ?? "",
                      previewTitle: msg.preview?.auction_title,
                      previewSku: msg.preview?.sku ?? null
                    }
                  : s
              );
            }
            return prev.map((s) =>
              s.index === msg.index
                ? {
                    ...s,
                    status: "analyzed",
                    fileName: msg.file_name ?? s.fileName,
                    previewTitle: msg.preview?.auction_title,
                    previewSku: msg.preview?.sku ?? null
                  }
                : s
            );
          });
        }
        if (msg.type === "batch_phase" && msg.phase === "inventory" && (!msg.job_id || msg.job_id === processingJobId)) {
          setInInventoryPhase(true);
        }
        if (
          msg.type === "inventory_step" &&
          typeof msg.index === "number" &&
          typeof msg.total === "number" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          setCatalogStepProgress({
            index: msg.index,
            total: msg.total,
            fileName: typeof msg.file_name === "string" ? msg.file_name : ""
          });
        }
        if (
          msg.type === "job_log" &&
          typeof (msg as { message?: unknown }).message === "string" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          console.info("[job]", (msg as { message: string }).message);
        }
        if (msg.type === "lot_ready" && msg.lot?.id && (!msg.job_id || msg.job_id === processingJobId)) {
          const row = normalizeStreamedLot(msg.lot);
          if (row.sale_id && row.sale_id !== selectedSaleIdRef.current) {
            return;
          }
          setLots((prev) => {
            const exists = prev.some((l) => l.id === row.id);
            if (exists) {
              return prev.map((l) => (l.id === row.id ? { ...l, ...row } : l));
            }
            return [row, ...prev];
          });
        }
        if (msg.type === "status" && (msg.status === "completed" || msg.status === "failed")) {
          setPhotoPendingSlots([]);
          setInInventoryPhase(false);
          setCatalogStepProgress(null);
          refreshJobsAndLots();
        }
      } catch {
        /* ignore */
      }
    };
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    es.onerror = () => {
      es.close();
      if (sseRetryAfterErrorRef.current >= 12) return;
      sseRetryAfterErrorRef.current += 1;
      reconnectTimer = setTimeout(() => setJobSseSession((n) => n + 1), 1800);
    };
    return () => {
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      es.close();
      // Intentionally do not clear photoPendingSlots here: React Strict Mode remounts this effect
      // before the worker finishes, and the server replays recent SSE payloads on reconnect.
    };
  }, [token, loading, processingJobId, jobSseSession]);

  useEffect(() => {
    if (!processingJobId) {
      setPhotoPendingSlots([]);
      setInInventoryPhase(false);
      setCatalogStepProgress(null);
    }
  }, [processingJobId]);

  useEffect(() => {
    if (!token || loading || !busyProcessing) return;

    /** Keep syncing while the phone is locked or Safari is in the background (field use). Slightly slower when hidden to save radio. */
    const pollMs = () =>
      typeof document !== "undefined" && document.visibilityState === "hidden" ? 45000 : 12000;

    const tick = async () => {
      try {
        const sid = selectedSaleIdRef.current;
        if (!sid) return;
        const jlist = (await apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)) as Job[];
        setJobs((prev) => {
          const hadProcessing = prev.some((j) => j.status === "processing");
          const nowProcessing = jlist.some((j) => j.status === "processing");
          if (hadProcessing && !nowProcessing) {
            const fq =
              inventorySkuFlagFilterRef.current === ""
                ? ""
                : `&sku_flags=${encodeURIComponent(inventorySkuFlagFilterRef.current)}`;
            void apiFetch(`/lots?sale_id=${encodeURIComponent(sid)}${fq}`, token).then((lotsData) =>
              setLots(lotsData as Lot[])
            );
          }
          return jlist;
        });
      } catch {
        /* ignore */
      }
    };

    void tick();
    let id = setInterval(() => void tick(), pollMs());
    const onVis = () => {
      void tick();
      clearInterval(id);
      id = setInterval(() => void tick(), pollMs());
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [token, loading, busyProcessing, selectedSaleId, inventorySkuFlagFilter]);

  useEffect(() => {
    return () => {
      const p = folderPickRef.current;
      if (p?.thumbUrls.length) revokeObjectUrls(p.thumbUrls);
      lotCameraStreamRef.current?.getTracks().forEach((t) => t.stop());
      lotCameraStreamRef.current = null;
    };
  }, []);

  useEffect(() => {
    const el = lotCameraVideoRef.current;
    const s = lotCameraStream;
    if (!el || !s) return;
    el.srcObject = s;
    void el.play().catch(() => {});
    return () => {
      el.srcObject = null;
    };
  }, [lotCameraStream]);

  useEffect(() => {
    const urls = lotCameraSnaps.map((f) => URL.createObjectURL(f));
    setLotCameraSnapThumbUrls(urls);
    return () => {
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [lotCameraSnaps]);

  useEffect(() => {
    const strip = lotCameraStripRef.current;
    if (!strip || !lotCameraSnapThumbUrls.length) return;
    strip.scrollLeft = strip.scrollWidth;
  }, [lotCameraSnapThumbUrls.length]);

  /** Center the live photo preview in the scroll panel so it stays fully in view (actions sit in a fixed bottom bar). */
  useEffect(() => {
    if (!lotCameraOpen || !lotCameraStream || !lotCameraPreviewReady) return;
    const wrap = lotCameraZoomWrapRef.current;
    if (!wrap) return;

    const centerPreview = () => {
      wrap.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });
    };

    centerPreview();
    requestAnimationFrame(centerPreview);
    const t1 = window.setTimeout(centerPreview, 80);
    const t2 = window.setTimeout(centerPreview, 320);

    let resizeRaf = 0;
    const onResizeOrOrientation = () => {
      cancelAnimationFrame(resizeRaf);
      resizeRaf = window.requestAnimationFrame(() => {
        lotCameraZoomWrapRef.current?.scrollIntoView({
          block: "center",
          inline: "nearest",
          behavior: "auto"
        });
      });
    };
    window.addEventListener("orientationchange", onResizeOrOrientation);
    window.addEventListener("resize", onResizeOrOrientation);

    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      cancelAnimationFrame(resizeRaf);
      window.removeEventListener("orientationchange", onResizeOrOrientation);
      window.removeEventListener("resize", onResizeOrOrientation);
    };
  }, [lotCameraOpen, lotCameraStream, lotCameraPreviewReady]);

  /** Pinch-to-zoom on the live preview (non-passive ``touchmove`` so Safari does not scroll the page). */
  useEffect(() => {
    const el = lotCameraZoomWrapRef.current;
    if (!el || !lotCameraOpen || !lotCameraStream) return;

    const clampZoom = (z: number) =>
      Math.round(Math.min(LOT_CAMERA_ZOOM_MAX, Math.max(LOT_CAMERA_ZOOM_MIN, z)) * 100) / 100;

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length === 2) {
        const d = lotCameraPinchDistance(e);
        if (d > 0) {
          lotCameraPinchRef.current = { startDist: d, startZoom: lotCameraZoomRef.current };
        }
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 2 || !lotCameraPinchRef.current) return;
      const d = lotCameraPinchDistance(e);
      if (d < 1) return;
      e.preventDefault();
      const { startDist, startZoom } = lotCameraPinchRef.current;
      if (startDist < 1) return;
      const z = clampZoom(startZoom * (d / startDist));
      lotCameraZoomRef.current = z;
      setLotCameraZoom(z);
    };

    const onTouchEnd = () => {
      lotCameraPinchRef.current = null;
    };

    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", onTouchEnd);
    el.addEventListener("touchcancel", onTouchEnd);
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [lotCameraOpen, lotCameraStream]);

  function setLotCameraZoomLevel(next: number) {
    const z =
      Math.round(Math.min(LOT_CAMERA_ZOOM_MAX, Math.max(LOT_CAMERA_ZOOM_MIN, next)) * 100) / 100;
    lotCameraZoomRef.current = z;
    setLotCameraZoom(z);
  }

  function clearFolderPick() {
    singleLotCaptureChainingRef.current = false;
    setSingleLotManualSku("");
    setStagingViewerIndex(null);
    setStagingEditIndex(null);
    setStagingEditBusy(false);
    setStagingEditRotation(0);
    setStagingEditTrim(0);
    setStagingEditBrightness(100);
    setStagingEditContrast(100);
    setStagingEditSaturation(100);
    setFolderPick((prev) => {
      if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
      return null;
    });
  }

  function removeStagingImage(index: number) {
    setFolderPick((prev) => {
      if (!prev || index < 0 || index >= prev.imageFiles.length) return prev;
      if (prev.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
      const nextFiles = prev.imageFiles.filter((_, j) => j !== index);
      const thumbUrls = nextFiles.map((f) => URL.createObjectURL(f));
      const folderLabel =
        prev.singleLotMode && nextFiles.length > 0
          ? nextFiles.length === 1
            ? folderLabelFromFile(nextFiles[0]!)
            : `One lot (${nextFiles.length} photos)`
          : prev.folderLabel;
      return { ...prev, imageFiles: nextFiles, thumbUrls, folderLabel };
    });
    setStagingViewerIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
    setStagingEditIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
  }

  function replaceStagingImageFile(index: number, file: File) {
    setFolderPick((prev) => {
      if (!prev || index < 0 || index >= prev.imageFiles.length) return prev;
      if (prev.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
      const nextFiles = [...prev.imageFiles];
      nextFiles[index] = file;
      const thumbUrls = nextFiles.map((f) => URL.createObjectURL(f));
      return { ...prev, imageFiles: nextFiles, thumbUrls };
    });
  }

  function openStagingEdit(i: number) {
    const p = folderPickRef.current;
    if (!p || i < 0 || i >= p.imageFiles.length) return;
    setStagingViewerIndex(null);
    setStagingEditIndex(i);
    setStagingEditRotation(0);
    setStagingEditTrim(0);
    setStagingEditBrightness(100);
    setStagingEditContrast(100);
    setStagingEditSaturation(100);
  }

  function closeStagingEdit() {
    if (stagingEditBusy) return;
    setStagingEditIndex(null);
  }

  async function applyStagingEdit() {
    const i = stagingEditIndex;
    if (i === null) return;
    const p = folderPickRef.current;
    const file = p?.imageFiles[i];
    if (!file) return;
    const adjust: RasterizeBasicAdjust = {
      brightnessPct: stagingEditBrightness,
      contrastPct: stagingEditContrast,
      saturationPct: stagingEditSaturation
    };
    if (
      stagingEditRotation === 0 &&
      stagingEditTrim === 0 &&
      isRasterAdjustDefault(adjust)
    ) {
      setStagingEditIndex(null);
      return;
    }
    setStagingEditBusy(true);
    try {
      const out = await rasterizeLotCameraEdit(
        file,
        stagingEditRotation,
        stagingEditTrim,
        adjust
      );
      if (!out) return;
      replaceStagingImageFile(i, out);
      setStagingEditIndex(null);
      setStagingEditRotation(0);
      setStagingEditTrim(0);
      setStagingEditBrightness(100);
      setStagingEditContrast(100);
      setStagingEditSaturation(100);
    } finally {
      setStagingEditBusy(false);
    }
  }

  function stopLotCameraTracks() {
    lotCameraStreamRef.current?.getTracks().forEach((t) => t.stop());
    lotCameraStreamRef.current = null;
    setLotCameraStream(null);
    setLotCameraOpen(false);
    setLotCameraPreviewReady(false);
    setLotCameraViewerIndex(null);
    setLotCameraEditIndex(null);
    setLotCameraEditBusy(false);
    setLotCameraEditBrightness(100);
    setLotCameraEditContrast(100);
    setLotCameraEditSaturation(100);
    lotCameraPinchRef.current = null;
    lotCameraZoomRef.current = LOT_CAMERA_ZOOM_MIN;
    setLotCameraZoom(LOT_CAMERA_ZOOM_MIN);
  }

  function cancelLotCameraSession() {
    lotCameraSnapsRef.current = [];
    setLotCameraSnaps([]);
    stopLotCameraTracks();
  }

  /** Cancel away from Snap/Done; confirm only when snaps would be lost (not uploaded until Done). */
  function tryCancelLotCameraSession() {
    const n = lotCameraSnapsRef.current.length;
    if (n > 0) {
      const msg =
        n === 1
          ? "Discard 1 captured photo and close the camera? Photos are not uploaded until you tap Done."
          : `Discard ${n} captured photos and close the camera? They are not uploaded until you tap Done.`;
      if (!window.confirm(msg)) return;
    }
    cancelLotCameraSession();
  }

  function finishLotCameraSession() {
    const files = [...lotCameraSnapsRef.current];
    lotCameraSnapsRef.current = [];
    setLotCameraSnaps([]);
    stopLotCameraTracks();
    if (!files.length) return;
    const { pick, droppedFromCap } = applySingleLotFilesToStaging(folderPickRef.current, files, 0);
    setFolderPick(pick);
    if (droppedFromCap > 0) {
      setError(
        `CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. ${droppedFromCap} photo(s) from the camera were not added because the staging area was already full.`
      );
    }
  }

  function snapLotPhoto() {
    if (lotCameraSnapsRef.current.length >= CTBIDS_MAX_PHOTOS_PER_LOT) {
      setError(`CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing.`);
      return;
    }
    const v = lotCameraVideoRef.current;
    if (!v || v.readyState < 2 || v.videoWidth < 2) return;
    const z = Math.max(LOT_CAMERA_ZOOM_MIN, lotCameraZoomRef.current);
    const w = v.videoWidth;
    const h = v.videoHeight;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (z <= LOT_CAMERA_ZOOM_MIN + 0.001) {
      ctx.drawImage(v, 0, 0, w, h, 0, 0, w, h);
    } else {
      const sw = w / z;
      const sh = h / z;
      const sx = (w - sw) / 2;
      const sy = (h - sh) / 2;
      ctx.drawImage(v, sx, sy, sw, sh, 0, 0, w, h);
    }
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        if (lotCameraSnapsRef.current.length >= CTBIDS_MAX_PHOTOS_PER_LOT) return;
        const file = new File([blob], `lot-${Date.now()}.jpg`, { type: "image/jpeg" });
        lotCameraSnapsRef.current = [...lotCameraSnapsRef.current, file];
        setLotCameraSnaps([...lotCameraSnapsRef.current]);
        triggerLotCameraSnapFeedback();
      },
      "image/jpeg",
      0.88
    );
  }

  function removeLotCameraSnap(index: number) {
    setLotCameraSnaps((prev) => {
      if (index < 0 || index >= prev.length) return prev;
      const next = prev.filter((_, j) => j !== index);
      lotCameraSnapsRef.current = next;
      return next;
    });
    setLotCameraViewerIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
    setLotCameraEditIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
  }

  function openLotCameraEdit(i: number) {
    if (i < 0 || i >= lotCameraSnapsRef.current.length) return;
    setLotCameraViewerIndex(null);
    setLotCameraEditIndex(i);
    setLotCameraEditRotation(0);
    setLotCameraEditTrim(0);
    setLotCameraEditBrightness(100);
    setLotCameraEditContrast(100);
    setLotCameraEditSaturation(100);
  }

  function closeLotCameraEdit() {
    if (lotCameraEditBusy) return;
    setLotCameraEditIndex(null);
  }

  async function applyLotCameraEdit() {
    const i = lotCameraEditIndex;
    if (i === null) return;
    const file = lotCameraSnapsRef.current[i];
    if (!file) return;
    const adjust: RasterizeBasicAdjust = {
      brightnessPct: lotCameraEditBrightness,
      contrastPct: lotCameraEditContrast,
      saturationPct: lotCameraEditSaturation
    };
    if (
      lotCameraEditRotation === 0 &&
      lotCameraEditTrim === 0 &&
      isRasterAdjustDefault(adjust)
    ) {
      setLotCameraEditIndex(null);
      return;
    }
    setLotCameraEditBusy(true);
    try {
      const out = await rasterizeLotCameraEdit(
        file,
        lotCameraEditRotation,
        lotCameraEditTrim,
        adjust
      );
      if (!out) return;
      setLotCameraSnaps((prev) => {
        if (i < 0 || i >= prev.length) return prev;
        const next = [...prev];
        next[i] = out;
        lotCameraSnapsRef.current = next;
        return next;
      });
      setLotCameraEditIndex(null);
      setLotCameraEditRotation(0);
      setLotCameraEditTrim(0);
      setLotCameraEditBrightness(100);
      setLotCameraEditContrast(100);
      setLotCameraEditSaturation(100);
    } finally {
      setLotCameraEditBusy(false);
    }
  }

  async function openCreateOneLotFlow() {
    if (typeof window === "undefined") return;
    const md = navigator.mediaDevices;
    if (window.isSecureContext && md?.getUserMedia) {
      setLotCameraSnaps([]);
      lotCameraSnapsRef.current = [];
      setLotCameraPreviewReady(false);
      setLotCameraViewerIndex(null);
      setLotCameraEditIndex(null);
      setLotCameraEditBusy(false);
      setLotCameraEditBrightness(100);
      setLotCameraEditContrast(100);
      setLotCameraEditSaturation(100);
      lotCameraZoomRef.current = LOT_CAMERA_ZOOM_MIN;
      setLotCameraZoom(LOT_CAMERA_ZOOM_MIN);
      setLotCameraOpen(true);
      try {
        const stream = await md.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
          audio: false
        });
        lotCameraStreamRef.current = stream;
        setLotCameraStream(stream);
      } catch {
        lotCameraStreamRef.current = null;
        setLotCameraStream(null);
        setLotCameraOpen(false);
        singleLotCaptureChainingRef.current = true;
        uploadPickModeRef.current = "single_lot";
        photosInputRef.current?.click();
      }
      return;
    }
    singleLotCaptureChainingRef.current = true;
    uploadPickModeRef.current = "single_lot";
    photosInputRef.current?.click();
  }

  function onFolderInputChange(list: FileList | null) {
    if (!list?.length) {
      if (singleLotCaptureChainingRef.current) {
        singleLotCaptureChainingRef.current = false;
      }
      return;
    }
    const pickMode = uploadPickModeRef.current;
    uploadPickModeRef.current = "batch";
    const all = Array.from(list);
    const imageFiles = all.filter(isCatalogImageFile);
    if (!imageFiles.length) {
      if (folderInputRef.current) folderInputRef.current.value = "";
      if (photosInputRef.current) photosInputRef.current.value = "";
      return;
    }
    const skippedCount = all.length - imageFiles.length;
    const label = folderLabelFromFile(all[0]!);
    const singleLotMode = pickMode === "single_lot";
    if (singleLotMode && imageFiles.length > 1) {
      singleLotCaptureChainingRef.current = false;
    }
    const chainAnotherCamera =
      singleLotMode &&
      singleLotCaptureChainingRef.current &&
      imageFiles.length === 1;

    if (singleLotMode) {
      const { pick, droppedFromCap } = applySingleLotFilesToStaging(
        folderPickRef.current,
        imageFiles,
        skippedCount
      );
      setFolderPick(pick);
      if (droppedFromCap > 0) {
        setError(
          `CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. ${droppedFromCap} photo(s) were not added.`
        );
      }
    } else {
      setFolderPick((prev) => {
        if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
        const thumbUrls = imageFiles.map((f) => URL.createObjectURL(f));
        return {
          folderLabel: label,
          imageFiles,
          thumbUrls,
          skippedCount,
          singleLotMode
        };
      });
    }
    if (folderInputRef.current) folderInputRef.current.value = "";
    if (photosInputRef.current) photosInputRef.current.value = "";

    if (chainAnotherCamera) {
      window.setTimeout(() => {
        if (!singleLotCaptureChainingRef.current) return;
        uploadPickModeRef.current = "single_lot";
        photosInputRef.current?.click();
      }, 280);
    }
  }

  async function enqueueJob() {
    if (!selectedSaleId) {
      setError("Select or create a sale first.");
      return;
    }
    setError("");
    try {
      const created = await apiFetch("/jobs", token, {
        method: "POST",
        body: JSON.stringify({
          input_folder: folder,
          sale_id: selectedSaleId,
          inherit_sku_from_subfolder: inheritSkuFromSubfolder,
          single_lot_mode: false
        })
      });
      setJobs((prev) => [created, ...prev]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to create job";
      if (isAuthFailureMessage(msg)) {
        localStorage.removeItem("token");
        router.replace("/");
        return;
      }
      setError(msg);
    }
  }

  async function uploadStagedFolder() {
    if (!folderPick?.imageFiles.length) return;
    if (!selectedSaleId) {
      setError("Select or create a sale first.");
      return;
    }
    setError("");
    setUploadProgress("");
    setStagingViewerIndex(null);
    setStagingEditIndex(null);
    setStagingEditBusy(false);
    setStagingEditBrightness(100);
    setStagingEditContrast(100);
    setStagingEditSaturation(100);
    const files = folderPick.imageFiles;
    const total = files.length;
    if (folderPick.singleLotMode && total > CTBIDS_MAX_PHOTOS_PER_LOT) {
      setError(
        `CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. Remove ${total - CTBIDS_MAX_PHOTOS_PER_LOT} photo(s) before uploading.`
      );
      return;
    }
    setUploading(true);
    try {
      const buildFormForSlice = (slice: File[]) => {
        const form = new FormData();
        form.append("sale_id", selectedSaleId);
        form.append("inherit_sku_from_subfolder", inheritSkuFromSubfolder ? "true" : "false");
        form.append("single_lot_mode", folderPick.singleLotMode ? "true" : "false");
        if (folderPick.singleLotMode && singleLotManualSku.trim()) {
          form.append("single_lot_manual_sku", singleLotManualSku.trim());
        }
        for (const file of slice) {
          const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
          form.append("files", file, rel);
        }
        return form;
      };

      let created: Job;
      if (total <= UPLOAD_FILE_CHUNK) {
        created = (await apiUploadFiles("/jobs/upload", token, buildFormForSlice(files))) as Job;
      } else {
        const batchCount = Math.ceil(total / UPLOAD_FILE_CHUNK);
        setUploadProgress(`Uploading batch 1/${batchCount} (${Math.min(UPLOAD_FILE_CHUNK, total)} of ${total} files)…`);
        const first = files.slice(0, UPLOAD_FILE_CHUNK);
        created = (await apiUploadFiles("/jobs/upload", token, buildFormForSlice(first))) as Job;
        let uploaded = first.length;
        let batchIndex = 2;
        while (uploaded < total) {
          const slice = files.slice(uploaded, uploaded + UPLOAD_FILE_CHUNK);
          setUploadProgress(
            `Uploading batch ${batchIndex}/${batchCount} (${slice.length} files, ${uploaded + slice.length}/${total} total)…`
          );
          const form = new FormData();
          for (const file of slice) {
            const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
            form.append("files", file, rel);
          }
          created = (await apiUploadFiles(`/jobs/${created.id}/upload-files`, token, form)) as Job;
          uploaded += slice.length;
          batchIndex += 1;
        }
        setUploadProgress(`All ${total} files uploaded — starting job…`);
      }
      setJobs((prev) => [created, ...prev]);
      clearFolderPick();
      setUploadProgress("");
      try {
        const running = (await apiFetch(`/jobs/${created.id}/run`, token, { method: "POST" })) as Job;
        setJobs((prev) => prev.map((j) => (j.id === running.id ? running : j)));
      } catch (runErr) {
        setError(
          runErr instanceof Error
            ? `${runErr.message} (upload saved; use Run now on the job row.)`
            : "Upload saved but failed to start processing — use Run now on the job row."
        );
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Upload failed";
      if (isAuthFailureMessage(msg)) {
        localStorage.removeItem("token");
        router.replace("/");
        return;
      }
      setError(msg);
    } finally {
      setUploading(false);
      setUploadProgress("");
    }
  }

  async function runJob(jobId: string) {
    setError("");
    try {
      const updated = (await apiFetch(`/jobs/${jobId}/run`, token, { method: "POST" })) as Job;
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Run failed";
      if (isAuthFailureMessage(msg)) {
        localStorage.removeItem("token");
        router.replace("/");
        return;
      }
      setError(msg);
      try {
        const sid = selectedSaleIdRef.current;
        if (!sid) return;
        const refreshed = await apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token);
        setJobs(refreshed as Job[]);
      } catch {
        /* ignore */
      }
    }
  }

  async function cancelRun(jobId: string) {
    setError("");
    try {
      await apiFetch(`/jobs/${jobId}/cancel`, token, { method: "POST" });
      const sid = selectedSaleIdRef.current;
      if (!sid) return;
      const jlist = (await apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)) as Job[];
      setJobs(jlist);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancel failed");
    }
  }

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function deleteLotRow(lotId: string) {
    if (!window.confirm("Delete this lot from the database?")) return;
    setError("");
    try {
      await apiFetch(`/lots/${lotId}`, token, { method: "DELETE" });
      setLots((prev) => prev.filter((l) => l.id !== lotId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  async function toggleLotCompleted(lotId: string, nextValue: boolean) {
    setError("");
    try {
      const updated = (await apiFetch(`/lots/${lotId}`, token, {
        method: "PATCH",
        body: JSON.stringify({ is_completed: nextValue })
      })) as Lot;
      setLots((prev) => prev.map((l) => (l.id === lotId ? updated : l)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update completion");
    }
  }

  async function deleteJobRow(jobId: string) {
    if (!window.confirm("Remove this job record (and uploaded folder if any)?")) return;
    setError("");
    try {
      await apiFetch(`/jobs/${jobId}`, token, { method: "DELETE" });
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Delete failed";
      if (msg.includes("409")) {
        setError("Wait until the job finishes processing before deleting.");
        return;
      }
      setError(msg);
    }
  }

  async function createSale() {
    const name = newSaleName.trim();
    if (!name || !token) return;
    setCreatingSale(true);
    setError("");
    try {
      const created = (await apiFetch("/sales", token, {
        method: "POST",
        body: JSON.stringify({ name })
      })) as Sale;
      setSales((prev) => [...prev, created]);
      setNewSaleName("");
      setSelectedSaleId(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create sale");
    } finally {
      setCreatingSale(false);
    }
  }

  async function endSelectedSale() {
    if (!token || !selectedSaleId || selectedSaleClosed) return;
    const target = selectedSale?.name || "this sale";
    if (!window.confirm(`Close "${target}"? Closed sales remain visible but should no longer be used for new work.`)) {
      return;
    }
    setClosingSale(true);
    setError("");
    try {
      const updated = (await apiFetch(`/sales/${selectedSaleId}`, token, {
        method: "PATCH",
        body: JSON.stringify({ is_closed: true })
      })) as Sale;
      setSales((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not close sale");
    } finally {
      setClosingSale(false);
    }
  }

  async function refreshAdminAccounts() {
    if (!token || !adminLoadAttempted) return;
    setAdminError("");
    setAdminLoading(true);
    try {
      const rows = (await apiFetch("/admin/accounts", token)) as AdminAccount[];
      setAdminAccounts(rows);
      const drafts: Record<string, string> = {};
      const nameDrafts: Record<string, string> = {};
      rows.forEach((a) => {
        drafts[a.id] = a.plan_code || "beta_free";
        nameDrafts[a.id] = a.name || "";
      });
      setAdminNameDraftById(nameDrafts);
      setAdminPlanDraftById(drafts);
      setIsMasterAdmin(true);
      setAdminUsageLoading(true);
      try {
        const usage = (await apiFetch("/admin/analytics?since_days=30", token)) as AdminUsageSummary;
        setAdminUsage(usage);
      } catch {
        setAdminUsage(null);
      } finally {
        setAdminUsageLoading(false);
      }
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not refresh admin accounts");
    } finally {
      setAdminLoading(false);
    }
  }

  async function patchAdminAccount(
    accountId: string,
    body: { is_active?: boolean; plan_code?: string; name?: string }
  ) {
    if (!token) return;
    setAdminSavingAccountId(accountId);
    setAdminError("");
    try {
      const updated = (await apiFetch(`/admin/accounts/${encodeURIComponent(accountId)}`, token, {
        method: "PATCH",
        body: JSON.stringify(body)
      })) as AdminAccount;
      setAdminAccounts((prev) => prev.map((a) => (a.id === accountId ? updated : a)));
      setAdminPlanDraftById((prev) => ({
        ...prev,
        [accountId]: updated.plan_code || prev[accountId] || "beta_free"
      }));
      setAdminNameDraftById((prev) => ({
        ...prev,
        [accountId]: updated.name || prev[accountId] || ""
      }));
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not update account");
    } finally {
      setAdminSavingAccountId("");
    }
  }

  async function toggleArchiveAdminAccount(accountId: string, nextActive: boolean) {
    if (!token) return;
    setAdminSavingAccountId(accountId);
    setAdminError("");
    try {
      const endpoint = nextActive
        ? `/admin/accounts/${encodeURIComponent(accountId)}/restore`
        : `/admin/accounts/${encodeURIComponent(accountId)}/archive`;
      const updated = (await apiFetch(endpoint, token, { method: "POST" })) as AdminAccount;
      setAdminAccounts((prev) => prev.map((a) => (a.id === accountId ? updated : a)));
      setAdminPlanDraftById((prev) => ({
        ...prev,
        [accountId]: updated.plan_code || prev[accountId] || "beta_free"
      }));
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not update archive status");
    } finally {
      setAdminSavingAccountId("");
    }
  }

  async function deleteAdminAccount(accountId: string, label: string) {
    if (!token) return;
    const typed = window.prompt(
      `Delete account "${label}"?\n\nThis permanently removes users, sales, lots, photos, jobs, and exports.\nType DELETE to confirm.`
    );
    if (typed !== "DELETE") return;
    setAdminSavingAccountId(accountId);
    setAdminError("");
    try {
      await apiFetch(`/admin/accounts/${encodeURIComponent(accountId)}`, token, { method: "DELETE" });
      setAdminAccounts((prev) => prev.filter((a) => a.id !== accountId));
      setAdminNameDraftById((prev) => {
        const next = { ...prev };
        delete next[accountId];
        return next;
      });
      setAdminPlanDraftById((prev) => {
        const next = { ...prev };
        delete next[accountId];
        return next;
      });
      setAdminUsage((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          total_accounts: Math.max(0, prev.total_accounts - 1),
          by_account: prev.by_account.filter((u) => u.account_id !== accountId)
        };
      });
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not delete account");
    } finally {
      setAdminSavingAccountId("");
    }
  }

  async function purgeAll() {
    if (
      !window.confirm(
        "Delete ALL lots, photos, jobs, and your browser-uploaded image folders for this login? This cannot be undone."
      )
    ) {
      return;
    }
    setError("");
    try {
      await apiFetch("/lots/purge-all", token, { method: "POST" });
      setLots([]);
      setJobs([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Purge failed");
    }
  }

  const exportLinks = useMemo(() => {
    const q = selectedSaleId ? `?sale_id=${encodeURIComponent(selectedSaleId)}` : "";
    return {
      markdown: `${API_BASE}/exports/markdown${q}`,
      json: `${API_BASE}/exports/json${q}`,
      csv: `${API_BASE}/exports/csv${q}`
    };
  }, [selectedSaleId]);

  async function downloadExport(kind: "markdown" | "json" | "csv") {
    if (!token) return;
    setError("");
    setExporting(kind);
    try {
      const res = await fetch(exportLinks[kind], {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store"
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Export failed (${res.status})`);
      }

      const blob = await res.blob();
      const ext = kind === "markdown" ? "md" : kind;
      const saleName = sales.find((s) => s.id === selectedSaleId)?.name?.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
      const baseName = saleName || selectedSaleId || "all-sales";
      const fileName = `ct-export-${baseName}.${ext}`;
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not export");
    } finally {
      setExporting("");
    }
  }

  async function startSalePhotosZipExport() {
    if (!token || !selectedSaleId) return;
    setError("");
    setSaleZipBusy(true);
    try {
      const row = (await apiFetch(
        `/exports/sale-photos-zip?sale_id=${encodeURIComponent(selectedSaleId)}`,
        token,
        { method: "POST" }
      )) as SaleZipExport;
      setSaleZipExport(row);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start photo ZIP export");
    } finally {
      setSaleZipBusy(false);
    }
  }

  async function downloadSaleZipExport() {
    if (!token || !saleZipExport?.id || !saleZipExport.ready) return;
    setError("");
    try {
      const res = await fetch(
        `${API_BASE}/exports/sale-photos-zip/${encodeURIComponent(saleZipExport.id)}/download`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store"
        }
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Download failed (${res.status})`);
      }
      const blob = await res.blob();
      const saleName = sales.find((s) => s.id === selectedSaleId)?.name?.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
      const base = saleName || selectedSaleId || "sale";
      const fileName = `sale-${base}-photos.zip`;
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
    }
  }

  if (!token) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Please sign in first.</p>
        </div>
      </main>
    );
  }
  if (loading) {
    return (
      <main className="ct-shell">
        <div className="ct-panel ct-panel--pulse">
          <p style={{ margin: 0 }}>Loading dashboard…</p>
        </div>
      </main>
    );
  }

  const saleContext =
    selectedSale != null
      ? `Working in “${selectedSale.name}”${selectedSale.closed_at ? " (closed)" : ""}. Switch sale below.`
      : "Select or create a sale below to organize lots and jobs.";

  return (
    <main id="dashboard-top" className="ct-shell">
      <SiteHeader
        actions={
          <>
            <Link href="/account" className="ct-btn ct-btn--ghost">
              Account
            </Link>
            <button type="button" className="ct-btn ct-btn--ghost" onClick={logout}>
              Log out
            </button>
          </>
        }
      />
      <header className="ct-page-header">
        <div>
          <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
            Lots dashboard
          </h1>
          <p className="ct-subtitle" style={{ margin: 0, maxWidth: "36rem" }}>
            {saleContext}
          </p>
        </div>
      </header>
      <section className="ct-card ct-card--padded ct-card--interactive" style={{ marginBottom: "1.25rem" }}>
        <h2 className="ct-section-title" style={{ marginTop: 0 }}>
          Sales
        </h2>
        <p className="ct-lead" style={{ marginTop: 0, marginBottom: "0.75rem", maxWidth: "42rem" }}>
          Each <strong>sale</strong> is its own workspace — pick the tab for the auction or project you are working on.
          Lots and uploads always go into the <strong>selected</strong> sale.
        </p>
        {sales.length > 0 ? (
          <nav className="ct-tabs" aria-label="Sales">
            {sales.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`ct-tab${selectedSaleId === s.id ? " ct-tab--active" : ""}`}
                onClick={() => {
                  setSelectedSaleId(s.id);
                  setError("");
                }}
              >
                {s.name}
                {s.closed_at ? " (Closed)" : ""}
              </button>
            ))}
          </nav>
        ) : (
          <p className="ct-muted" style={{ marginBottom: "0.65rem" }}>
            No sales yet — add one below to start cataloging.
          </p>
        )}
        <div className="ct-row ct-row--gap-lg" style={{ marginTop: "0.75rem", flexWrap: "wrap" }}>
          <input
            className="ct-input"
            style={{ maxWidth: 320, width: "100%" }}
            placeholder="e.g. Oak Street estate — April 2026"
            value={newSaleName}
            onChange={(e) => setNewSaleName(e.target.value)}
            disabled={creatingSale}
          />
          <button
            type="button"
            className="ct-btn ct-btn--primary"
            disabled={creatingSale || !newSaleName.trim() || !token}
            onClick={() => void createSale()}
          >
            {creatingSale ? "Creating…" : "Add sale"}
          </button>
          <button
            type="button"
            className="ct-btn"
            disabled={!selectedSaleId || selectedSaleClosed || closingSale || busyProcessing}
            onClick={() => void endSelectedSale()}
          >
            {closingSale ? "Closing…" : "End sale"}
          </button>
          {selectedSaleClosed ? (
            <span className="ct-muted" style={{ fontSize: "0.85rem" }}>
              This sale is closed.
            </span>
          ) : null}
        </div>
      </section>
      {busyProcessing && (
        <div className="ct-banner ct-banner--warning">
          <p style={{ margin: 0 }}>
            <strong>Working on your photos.</strong> New lots will appear in the table below as they are ready. Use{" "}
            <strong>Cancel</strong> on the job row if you need to stop.
          </p>
          {geminiAnalyzedCount.total > 0 && !inInventoryPhase ? (
            <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.92rem" }}>
              <strong>Step 1 — Reading photos:</strong> {geminiAnalyzedCount.done} / {geminiAnalyzedCount.total} done
              {geminiAnalyzedCount.done < geminiAnalyzedCount.total
                ? " (this usually moves every few seconds)."
                : " — finishing up, then lots are saved."}
            </p>
          ) : null}
          {inInventoryPhase ? (
            <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.92rem" }}>
              <strong>Step 2 — Saving lots:</strong>{" "}
              {catalogStepProgress ? (
                <>
                  photo {catalogStepProgress.index + 1} of {catalogStepProgress.total}
                  {catalogStepProgress.fileName ? (
                    <>
                      {" "}
                      — <code style={{ fontSize: "0.88em" }}>{catalogStepProgress.fileName}</code>
                    </>
                  ) : null}
                  . If this stays stuck for several minutes, try Cancel and run the job again, or ask your tech contact
                  to check the server.
                </>
              ) : (
                <>starting…</>
              )}
            </p>
          ) : null}
          <details className="ct-help-details" style={{ marginTop: "0.65rem", marginBottom: 0 }}>
            <summary>For staff: how the live table works</summary>
            <div className="ct-help-details__body">
              You may see one <strong>Processing…</strong> row per photo until each frame is analyzed; rows then turn
              into catalog entries. Updates stream from the server in real time. If you switch away from this tab,
              progress may refresh more slowly — come back to this page to see the latest.
            </div>
          </details>
        </div>
      )}
      {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
      {uploadProgress ? (
        <div className="ct-banner ct-banner--warning" style={{ marginBottom: "0.5rem" }}>
          {uploadProgress}
        </div>
      ) : null}
      {isMasterAdmin || adminLoading || adminError ? (
        <section className="ct-card ct-card--padded" style={{ marginBottom: "1.25rem" }}>
          <h2 className="ct-section-title" style={{ marginTop: 0, marginBottom: "0.35rem" }}>
            Master admin
          </h2>
          <p className="ct-muted" style={{ marginTop: 0, marginBottom: "0.75rem", maxWidth: "54rem" }}>
            Manage pilot accounts from one place: toggle access and set a plan label for internal tracking.
          </p>
          <div className="ct-row ct-row--gap-lg" style={{ marginBottom: "0.75rem" }}>
            <button
              type="button"
              className="ct-btn"
              onClick={() => void refreshAdminAccounts()}
              disabled={adminLoading || !isMasterAdmin}
            >
              {adminLoading ? "Refreshing…" : "Refresh accounts"}
            </button>
            <input
              className="ct-input"
              style={{ maxWidth: 360, width: "100%" }}
              placeholder="Search account name, owner email, or ID"
              value={adminSearch}
              onChange={(e) => setAdminSearch(e.target.value)}
              disabled={!isMasterAdmin}
            />
            <button
              type="button"
              className="ct-btn"
              onClick={() => setAdminSearch("")}
              disabled={!isMasterAdmin || !adminSearch.trim()}
            >
              Clear
            </button>
          </div>
          {adminError ? <div className="ct-banner ct-banner--error">{adminError}</div> : null}
          {!isMasterAdmin && !adminLoading ? (
            <p className="ct-muted" style={{ margin: 0 }}>
              Your account is not in <code>MASTER_ADMIN_EMAILS</code>, so this panel is read-only hidden.
            </p>
          ) : null}
          {isMasterAdmin ? (
            <div className="ct-card ct-card--padded" style={{ marginBottom: "0.75rem" }}>
              <h3 className="ct-section-title" style={{ marginTop: 0, marginBottom: "0.4rem", fontSize: "1rem" }}>
                Analytics (last 30 days)
              </h3>
              {adminUsageLoading ? (
                <p className="ct-muted" style={{ margin: 0 }}>
                  Loading analytics…
                </p>
              ) : adminUsage ? (
                <p className="ct-muted" style={{ margin: 0, fontSize: "0.9rem" }}>
                  {adminUsage.totals.jobs_started} jobs started ({adminUsage.totals.jobs_completed} completed,{" "}
                  {adminUsage.totals.jobs_failed} failed), {adminUsage.totals.photos_uploaded} photos uploaded,{" "}
                  avg {adminUsage.totals.avg_photos_per_job.toFixed(2)} photos/job, est tokens{" "}
                  {(adminUsage.totals.est_input_tokens + adminUsage.totals.est_output_tokens).toLocaleString()} (
                  est ${adminUsage.totals.est_total_cost_usd.toFixed(2)}).
                </p>
              ) : (
                <p className="ct-muted" style={{ margin: 0 }}>
                  Analytics data unavailable yet.
                </p>
              )}
            </div>
          ) : null}
          {isMasterAdmin ? (
            <div className="ct-dashboard-table-desktop ct-table-wrap" style={{ marginBottom: 0 }}>
              <table className="ct-table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Plan</th>
                    <th>Status</th>
                    <th>Usage</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAdminAccounts.map((a) => {
                    const busy = adminSavingAccountId === a.id;
                    const draft = adminPlanDraftById[a.id] ?? a.plan_code ?? "beta_free";
                    const nameDraft = adminNameDraftById[a.id] ?? a.name ?? "";
                    const usage = adminUsage?.by_account.find((u) => u.account_id === a.id) || null;
                    return (
                      <tr key={a.id}>
                        <td>
                          <input
                            className="ct-input"
                            style={{ width: 220, fontWeight: 600 }}
                            value={nameDraft}
                            onChange={(e) =>
                              setAdminNameDraftById((prev) => ({ ...prev, [a.id]: e.target.value }))
                            }
                            disabled={busy}
                          />
                          {a.owner_email ? (
                            <div className="ct-muted" style={{ fontSize: "0.8rem" }}>
                              {a.owner_email}
                            </div>
                          ) : null}
                          <div className="ct-muted" style={{ fontSize: "0.8rem" }}>
                            {a.id}
                          </div>
                        </td>
                        <td>
                          <input
                            className="ct-input"
                            style={{ width: 150 }}
                            value={draft}
                            onChange={(e) =>
                              setAdminPlanDraftById((prev) => ({ ...prev, [a.id]: e.target.value }))
                            }
                            disabled={busy}
                          />
                        </td>
                        <td>{a.is_active ? "Active" : "Inactive"}</td>
                        <td className="ct-muted" style={{ fontSize: "0.85rem" }}>
                          {a.user_count} users · {a.sale_count} sales · {a.lot_count} lots · {a.job_count} jobs
                          {usage ? (
                            <>
                              <br />
                              30d: {usage.photos_uploaded} photos · avg {usage.avg_photos_per_job.toFixed(2)} photos/job
                              · est tokens {(usage.est_input_tokens + usage.est_output_tokens).toLocaleString()} · est $
                              {usage.est_total_cost_usd.toFixed(2)}
                            </>
                          ) : null}
                        </td>
                        <td>
                          <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap" }}>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() => void toggleArchiveAdminAccount(a.id, !a.is_active)}
                              disabled={busy}
                            >
                              {busy ? "Saving…" : a.is_active ? "Archive" : "Restore"}
                            </button>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() =>
                                void patchAdminAccount(a.id, {
                                  name: (nameDraft || "").trim() || a.name
                                })
                              }
                              disabled={busy || (nameDraft || "").trim().length < 2}
                            >
                              Save name
                            </button>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() => void patchAdminAccount(a.id, { plan_code: draft.trim() || "beta_free" })}
                              disabled={busy || draft.trim().length < 2}
                            >
                              Save plan
                            </button>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() => void deleteAdminAccount(a.id, a.name || a.id)}
                              disabled={busy}
                              title="Permanently delete this account and all related data"
                            >
                              Delete account
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {filteredAdminAccounts.length === 0 && !adminLoading ? (
                    <tr>
                      <td colSpan={5} className="ct-muted">
                        No accounts match that search.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}
      <div style={{ marginBottom: "1.25rem" }}>
        <h2 className="ct-section-title" style={{ marginTop: 0 }}>
          Run photo processing job
        </h2>
        <p className="ct-lead">
          Add photos for the <strong>sale you selected above</strong>. Choose a whole folder, or pick individual
          photos — then review thumbnails and tap <strong>Upload</strong>. Processing runs automatically; new lots
          appear in the table when each group is ready.
        </p>
        <details className="ct-help-details">
          <summary>Advanced tips &amp; technical notes</summary>
          <div className="ct-help-details__body">
            <ul>
              <li>
                <strong>Choose photo folder</strong> uses your computer&apos;s folder dialog (thumbnails may not show
                there — that is normal). After you confirm, previews and counts appear here before upload.
              </li>
              <li>
                <strong>Choose photos</strong> is better when you want thumbnails in the picker (common on phones and
                desktops).
              </li>
              <li>
                Large folders (<strong>200+ images</strong>) upload in chunks into the <strong>same</strong> job so the
                browser does not time out.
              </li>
              <li>
                <strong>Separate items:</strong> put each item in its own subfolder (e.g. <code>Matryoshka/</code>,{" "}
                <code>Turtle/</code>) if you are not using colored estate tape on every group.
              </li>
              <li>
                <strong>One flat folder:</strong> colored estate tape (purple, green, yellow, etc.) marks new lots; a{" "}
                <strong>blank white divider</strong>{" "}
                photo between groups can also split lots (that frame is not kept as a product photo).
              </li>
              <li>
                <strong>Create one lot…</strong> (when shown): all photos become <strong>one</strong> catalog row — good
                for many angles of a single item. On phones this can open the camera; if the camera fails, the regular
                file picker opens.
              </li>
              <li>
                <strong>Server path + Queue job</strong> is for staff only: photos already on the server running the
                API — nothing is uploaded from your browser.
              </li>
              <li>
                While a job runs, the table shows live progress; you can <strong>Cancel</strong> from the job row.
              </li>
            </ul>
          </div>
        </details>
        <label className="ct-row" style={{ gap: "0.5rem", marginBottom: "0.75rem", alignItems: "flex-start" }}>
          <input
            type="checkbox"
            checked={inheritSkuFromSubfolder}
            onChange={(e) => setInheritSkuFromSubfolder(e.target.checked)}
          />
          <span className="ct-muted" style={{ fontSize: "0.9rem", maxWidth: "44rem" }}>
            <strong>Use folder names as lot numbers</strong> when each lot is in its own subfolder named with digits
            only (for example <code>035</code>, <code>036</code>). The app uses that number as the SKU instead of
            reading it from colored estate tape in the photos.
          </span>
        </label>
        <input
          ref={folderInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          accept="image/*,.heic,.heif"
          {...({ webkitdirectory: "" } as Record<string, unknown>)}
          onChange={(e) => onFolderInputChange(e.target.files)}
        />
        <input
          ref={photosInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          accept="image/*,.heic,.heif"
          onChange={(e) => onFolderInputChange(e.target.files)}
        />
        <button
          type="button"
          className="ct-btn ct-btn--primary"
          onClick={() => {
            singleLotCaptureChainingRef.current = false;
            uploadPickModeRef.current = "batch";
            folderInputRef.current?.click();
          }}
          disabled={uploading || !selectedSaleId || selectedSaleClosed}
        >
          Choose photo folder…
        </button>
        <span className="ct-muted" style={{ marginLeft: "0.6rem", fontSize: "0.8rem" }}>
          Tip: folder pickers often hide thumbnails — use <strong>Choose photos</strong> if you need previews.
        </span>
        <button
          type="button"
          className="ct-btn"
          style={{ marginLeft: "0.6rem" }}
          onClick={() => {
            singleLotCaptureChainingRef.current = false;
            uploadPickModeRef.current = "batch";
            photosInputRef.current?.click();
          }}
          disabled={uploading || !selectedSaleId || selectedSaleClosed}
        >
          Choose photos…
        </button>
        {showCreateOneLot ? (
          <button
            type="button"
            className="ct-btn"
            style={{ marginLeft: "0.6rem" }}
            title="All selected images become one catalog row with one SKU"
            onClick={() => void openCreateOneLotFlow()}
            disabled={uploading || !selectedSaleId || selectedSaleClosed}
          >
            Create one lot…
          </button>
        ) : null}
        {folderPick && (
          <div className="ct-card ct-card--padded" style={{ marginTop: "0.85rem", maxWidth: 900 }}>
            {folderPick.singleLotMode ? (
              <div style={{ marginBottom: "0.65rem" }}>
                <p className="ct-muted" style={{ margin: "0 0 0.5rem", fontSize: "0.88rem" }}>
                  <strong>One lot only:</strong> all photos below become <strong>one</strong> listing (one row in the
                  table). CTBids allows at most <strong>{CTBIDS_MAX_PHOTOS_PER_LOT}</strong> photos per listing — extra
                  files are skipped.
                </p>
                <label className="ct-muted" style={{ display: "block", fontSize: "0.85rem" }}>
                  SKU for this lot <span style={{ fontWeight: 400 }}>(optional)</span>
                  <input
                    className="ct-input"
                    style={{ width: "100%", maxWidth: 360, marginTop: 6, display: "block" }}
                    value={singleLotManualSku}
                    onChange={(e) => setSingleLotManualSku(e.target.value)}
                    placeholder="e.g. 035 or Lot #042"
                    autoComplete="off"
                    enterKeyHint="done"
                  />
                </label>
              </div>
            ) : null}
            <div className="ct-row ct-row--gap-lg" style={{ marginBottom: "0.65rem" }}>
              <strong>
                {folderPick.imageFiles.length
                  ? folderPick.singleLotMode
                    ? `${folderPick.imageFiles.length} / ${CTBIDS_MAX_PHOTOS_PER_LOT} image${
                        folderPick.imageFiles.length === 1 ? "" : "s"
                      } in “${folderPick.folderLabel}”`
                    : `${folderPick.imageFiles.length} image${folderPick.imageFiles.length === 1 ? "" : "s"} in “${folderPick.folderLabel}”`
                  : `No catalog images found in “${folderPick.folderLabel}”`}
              </strong>
              {folderPick.skippedCount > 0 ? (
                <span className="ct-muted" style={{ fontSize: "0.82rem" }}>
                  ({folderPick.skippedCount} non-image file{folderPick.skippedCount === 1 ? "" : "s"} skipped)
                </span>
              ) : null}
            </div>
            {folderPick.imageFiles.length > 0 ? (
              <p className="ct-muted" style={{ margin: "0 0 0.5rem", fontSize: "0.8rem" }}>
                Tap a photo to enlarge. Pencil = edit (rotate, trim, color). Trash = remove before upload.
              </p>
            ) : null}
            {folderPick.imageFiles.length > 0 && (
              <div
                className="ct-thumb-strip"
                style={{ flexWrap: "nowrap", overflowX: "auto", WebkitOverflowScrolling: "touch" }}
              >
                {folderPick.thumbUrls.map((url, i) => (
                  <div
                    key={url}
                    style={{
                      position: "relative",
                      flex: "0 0 auto",
                      width: 72,
                      height: 72
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => !uploading && setStagingViewerIndex(i)}
                      aria-label={`Preview staged image ${i + 1}`}
                      disabled={uploading}
                      title={folderPick.imageFiles[i]?.name ?? ""}
                      style={{
                        display: "block",
                        width: 72,
                        height: 72,
                        padding: 0,
                        border: "1px solid var(--ct-border)",
                        borderRadius: 6,
                        overflow: "hidden",
                        cursor: uploading ? "not-allowed" : "pointer",
                        background: "var(--ct-surface)",
                        opacity: uploading ? 0.55 : 1
                      }}
                    >
                      <img
                        src={url}
                        alt=""
                        width={72}
                        height={72}
                        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block", pointerEvents: "none" }}
                      />
                    </button>
                    <button
                      type="button"
                      aria-label={`Edit staged image ${i + 1}`}
                      disabled={uploading}
                      onClick={(e) => {
                        e.stopPropagation();
                        openStagingEdit(i);
                      }}
                      className="ct-btn ct-btn--ghost"
                      style={{
                        position: "absolute",
                        bottom: -2,
                        left: -2,
                        minWidth: 34,
                        minHeight: 34,
                        width: 34,
                        height: 34,
                        padding: 0,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "rgba(12,15,18,0.92)",
                        border: "1px solid rgba(255,255,255,0.35)",
                        color: "#c8d4ff",
                        boxShadow: "0 1px 4px rgba(0,0,0,0.25)"
                      }}
                    >
                      <Pencil size={15} aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove staged image ${i + 1}`}
                      disabled={uploading}
                      onClick={(e) => {
                        e.stopPropagation();
                        removeStagingImage(i);
                      }}
                      className="ct-btn ct-btn--ghost"
                      style={{
                        position: "absolute",
                        top: -2,
                        right: -2,
                        minWidth: 34,
                        minHeight: 34,
                        width: 34,
                        height: 34,
                        padding: 0,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "rgba(12,15,18,0.92)",
                        border: "1px solid rgba(255,255,255,0.35)",
                        color: "#f5a5a5",
                        boxShadow: "0 1px 4px rgba(0,0,0,0.25)"
                      }}
                    >
                      <Trash2 size={15} aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="ct-row ct-row--gap-lg">
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                onClick={() => void uploadStagedFolder()}
                disabled={uploading || !folderPick.imageFiles.length || !selectedSaleId || selectedSaleClosed}
              >
                {uploading
                  ? "Uploading…"
                  : folderPick.singleLotMode
                    ? `Upload ${folderPick.imageFiles.length} photo(s) as one lot`
                    : `Upload ${folderPick.imageFiles.length} photo(s) to server`}
              </button>
              <button
                type="button"
                className="ct-btn"
                disabled={uploading}
                onClick={() => {
                  const n = folderPick.imageFiles.length;
                  const msg =
                    n === 0
                      ? "Close this selection? Nothing has been uploaded yet."
                      : n === 1
                        ? "Remove the 1 staged photo from this upload box? Nothing has been uploaded yet."
                        : `Remove all ${n} staged photos from this upload box? Nothing has been uploaded yet.`;
                  if (!window.confirm(msg)) return;
                  clearFolderPick();
                }}
              >
                Clear selection
              </button>
            </div>
          </div>
        )}
        <div className="ct-row ct-row--gap-lg" style={{ marginTop: "0.85rem", alignItems: "center" }}>
          <span className="ct-muted" style={{ fontSize: "0.85rem" }}>
            Staff / server only
          </span>
          <input
            className="ct-input"
            style={{ maxWidth: 360, width: "100%" }}
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            placeholder="Path on the server where images already live"
            aria-label="Server folder path for queued job"
          />
          <button
            type="button"
            className="ct-btn"
            onClick={() => void enqueueJob()}
            disabled={!folder || uploading || !selectedSaleId || selectedSaleClosed}
          >
            Queue job
          </button>
        </div>
      </div>

      <h2 className="ct-section-title">Jobs</h2>
      <ul className="ct-job-list">
        {jobs.map((job) => (
          <li key={job.id}>
            <span className="ct-job-status">{job.status}</span>
            <span className="ct-job-path">{job.input_folder}</span>
            {job.inherit_sku_from_subfolder ? (
              <span className="ct-muted" style={{ fontSize: "0.78rem" }} title="Lot SKUs taken from digit folder names">
                SKU←folders
              </span>
            ) : null}
            {job.single_lot_mode ? (
              <span className="ct-muted" style={{ fontSize: "0.78rem" }} title="All photos merged into one catalog lot">
                1 lot
              </span>
            ) : null}
            {job.single_lot_manual_sku ? (
              <span className="ct-muted" style={{ fontSize: "0.78rem" }} title="Manual SKU on this job">
                SKU:{job.single_lot_manual_sku}
              </span>
            ) : null}
            {(job.status === "queued" || job.status === "failed") && (
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                onClick={() => void runJob(job.id)}
                disabled={busyProcessing}
              >
                {job.status === "failed" ? "Run again" : "Run now"}
              </button>
            )}
            {job.status === "processing" && (
              <button type="button" className="ct-btn" onClick={() => void cancelRun(job.id)}>
                Cancel
              </button>
            )}
            {job.error_message ? (
              <span style={{ color: "var(--ct-error)", fontSize: "0.85rem" }}>— {job.error_message}</span>
            ) : null}
            {job.status !== "processing" && (
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void deleteJobRow(job.id)} disabled={busyProcessing}>
                Delete job
              </button>
            )}
          </li>
        ))}
      </ul>

      <div className="ct-row ct-row--gap-lg" style={{ marginBottom: "0.65rem", alignItems: "baseline", flexWrap: "wrap" }}>
        <h2 className="ct-section-title" style={{ margin: 0 }}>
          Lots ({lots.length})
        </h2>
        <span className="ct-muted">
          {lots.filter((l) => l.is_completed).length} completed / {lots.filter((l) => !l.is_completed).length} not done
        </span>
        <label className="ct-row ct-row--gap-lg" style={{ alignItems: "center", gap: "0.35rem" }}>
          <span className="ct-muted" style={{ fontSize: "0.85rem" }}>
            SKU checks
          </span>
          <select
            className="ct-input"
            style={{ maxWidth: 220, fontSize: "0.88rem" }}
            value={inventorySkuFlagFilter}
            onChange={(e) =>
              setInventorySkuFlagFilter(
                (e.target.value || "") as "" | "duplicate" | "text_mismatch" | "any"
              )
            }
            aria-label="Filter lots by SKU sanity flags"
          >
            <option value="">All lots</option>
            <option value="any">Flagged: any issue</option>
            <option value="duplicate">Duplicate SKU (same sale)</option>
            <option value="text_mismatch">Mixed lot # in text</option>
          </select>
        </label>
        <button type="button" className="ct-btn ct-btn--danger" onClick={() => void purgeAll()} disabled={busyProcessing}>
          Clear all lots & jobs
        </button>
      </div>
      <div className="ct-dashboard-lots-mobile" aria-label="Lots (mobile layout)">
        {photoPendingSlots.map((slot) => (
          <div
            key={slot.id}
            className={`ct-lot-mobile-card ct-card ct-card--padded${
              slot.status === "queued" ? " ct-lot-mobile-card--pending-queued" : " ct-lot-mobile-card--pending-done"
            }`}
          >
            <div className="ct-row ct-row--gap-lg" style={{ alignItems: "center" }}>
              <div style={{ width: 40, flexShrink: 0, textAlign: "center" }}>
                {slot.status === "queued" ? (
                  <svg width={22} height={22} viewBox="0 0 24 24" aria-hidden>
                    <circle cx="12" cy="12" r="9" fill="none" stroke="rgba(253, 230, 138, 0.35)" strokeWidth="3" />
                    <path
                      d="M12 3 A9 9 0 0 1 21 12"
                      fill="none"
                      stroke="#fbbf24"
                      strokeWidth="3"
                      strokeLinecap="round"
                    >
                      <animateTransform
                        attributeName="transform"
                        type="rotate"
                        from="0 12 12"
                        to="360 12 12"
                        dur="0.75s"
                        repeatCount="indefinite"
                      />
                    </path>
                  </svg>
                ) : (
                  <span style={{ color: "var(--ct-success)", fontWeight: 700 }} aria-hidden>
                    ✓
                  </span>
                )}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 700 }}>
                  Photo {slot.index + 1} / {slot.total}
                </div>
                {slot.fileName ? (
                  <div className="ct-muted" style={{ fontSize: "0.8rem", wordBreak: "break-word", marginTop: 4 }}>
                    {slot.fileName}
                  </div>
                ) : null}
                <div className="ct-muted" style={{ fontSize: "0.82rem", marginTop: 6 }}>
                  {slot.status === "queued" ? "Waiting for Gemini…" : null}
                  {slot.previewTitle ? (
                    <span>
                      {" "}
                      — <em>{slot.previewTitle}</em>
                    </span>
                  ) : null}
                  {slot.status !== "queued" && inInventoryPhase ? (
                    <span>
                      {" · "}
                      {!catalogStepProgress
                        ? "Catalog merge: starting…"
                        : slot.index < catalogStepProgress.index
                          ? "Catalog merge: done"
                          : slot.index > catalogStepProgress.index
                            ? "Catalog merge: waiting"
                            : `Catalog merge: now ${catalogStepProgress.index + 1}/${catalogStepProgress.total}${
                                catalogStepProgress.fileName ? ` · ${catalogStepProgress.fileName}` : ""
                              }`}
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ))}
        {lots.map((lot) => (
          <div
            key={`m-${lot.id}`}
            className={`ct-lot-mobile-card ct-card ct-card--padded${
              lot.many_photos_review || lot.sku_duplicate_in_sale || lot.sku_text_mismatch
                ? " ct-lot-mobile-card--warn"
                : ""
            }`}
          >
            <div className="ct-row ct-row--gap-lg" style={{ alignItems: "flex-start" }}>
              <div style={{ flexShrink: 0 }}>
                <LotThumbnail lotId={lot.id} token={token} hasThumbnail={lot.has_thumbnail} size={56} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  className={lot.sku_needs_review || !lot.sku ? "ct-sku-cell ct-sku-cell--review" : "ct-sku-cell"}
                  style={{ fontSize: "0.95rem" }}
                >
                  <span className={lot.sku_needs_review || !lot.sku ? "ct-sku-pill" : undefined}>{lot.sku || "—"}</span>
                </div>
                <div style={{ fontWeight: 600, marginTop: 6, lineHeight: 1.35 }}>{lot.auction_title || "—"}</div>
                <div className="ct-muted" style={{ marginTop: "0.5rem", fontSize: "0.88rem", lineHeight: 1.4 }}>
                  <strong style={{ color: "var(--ct-text)" }}>{lot.photo_count}</strong>{" "}
                  photo{lot.photo_count === 1 ? "" : "s"}
                  {lot.starting_price != null ? (
                    <>
                      {" "}
                      · Starting <strong style={{ color: "var(--ct-text)" }}>${lot.starting_price}</strong>
                    </>
                  ) : null}
                  {lot.condition_text ? (
                    <>
                      {" "}
                      · <span title={lot.condition_text}>{lot.condition_text}</span>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
            <div className="ct-row ct-row--gap-lg" style={{ marginTop: "0.85rem", flexWrap: "wrap", alignItems: "center" }}>
              <Link
                href={`/lots/${lot.id}#listing-details`}
                className="ct-btn ct-btn--primary"
                onClick={rememberDashboardScrollBeforeLotDetails}
              >
                Details
              </Link>
              <label className="ct-row" style={{ gap: "0.45rem", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={lot.is_completed}
                  onChange={(e) => void toggleLotCompleted(lot.id, e.target.checked)}
                />
                <span className="ct-muted" style={{ fontSize: "0.88rem" }}>
                  {lot.is_completed ? "Done" : "Not done"}
                </span>
              </label>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                onClick={() => void deleteLotRow(lot.id)}
                disabled={busyProcessing}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="ct-dashboard-table-desktop ct-table-wrap">
        <table className="ct-table">
        <thead>
          <tr>
            <th>Photo</th>
            <th>SKU</th>
            <th>Auction Title</th>
            <th>Price</th>
            <th>Condition</th>
            <th>Status</th>
            <th>Photos</th>
            <th>Details</th>
            <th>Delete</th>
          </tr>
        </thead>
        <tbody>
          {photoPendingSlots.map((slot) => (
            <tr
              key={slot.id}
              className={slot.status === "queued" ? "ct-table-row--pending-queued" : "ct-table-row--pending-done"}
            >
              <td style={{ verticalAlign: "middle", width: 52, textAlign: "center" }}>
                {slot.status === "queued" ? (
                  <svg width={22} height={22} viewBox="0 0 24 24" aria-label="Processing" role="img">
                    <title>Processing</title>
                    <circle cx="12" cy="12" r="9" fill="none" stroke="rgba(253, 230, 138, 0.35)" strokeWidth="3" />
                    <path
                      d="M12 3 A9 9 0 0 1 21 12"
                      fill="none"
                      stroke="#fbbf24"
                      strokeWidth="3"
                      strokeLinecap="round"
                    >
                      <animateTransform
                        attributeName="transform"
                        type="rotate"
                        from="0 12 12"
                        to="360 12 12"
                        dur="0.75s"
                        repeatCount="indefinite"
                      />
                    </path>
                  </svg>
                ) : (
                  <span style={{ color: "var(--ct-success)", fontWeight: 700 }} aria-hidden>
                    ✓
                  </span>
                )}
              </td>
              <td style={{ color: "var(--ct-text-muted)" }}>{slot.previewSku ?? "—"}</td>
              <td>
                <strong>
                  Photo {slot.index + 1}/{slot.total}
                </strong>
                {slot.fileName ? (
                  <span style={{ fontFamily: "monospace", color: "var(--ct-text-subtle)" }}> · {slot.fileName}</span>
                ) : null}
                {slot.status === "queued" ? (
                  <span style={{ color: "var(--ct-text-subtle)" }}> — Waiting for Gemini…</span>
                ) : (
                  <>
                    {slot.previewTitle ? (
                      <span style={{ color: "var(--ct-text-muted)" }}>
                        {" "}
                        — <em>{slot.previewTitle}</em>
                      </span>
                    ) : null}
                    {inInventoryPhase ? (
                      <span style={{ color: "var(--ct-text-subtle)" }}>
                        {" · "}
                        {!catalogStepProgress
                          ? "Catalog merge: starting…"
                          : slot.index < catalogStepProgress.index
                            ? "Catalog merge: done"
                            : slot.index > catalogStepProgress.index
                              ? "Catalog merge: waiting"
                              : `Catalog merge: now ${catalogStepProgress.index + 1}/${catalogStepProgress.total}${
                                  catalogStepProgress.fileName ? ` · ${catalogStepProgress.fileName}` : ""
                                }`}
                      </span>
                    ) : null}
                  </>
                )}
              </td>
              <td style={{ color: "var(--ct-text-faint)" }}>—</td>
              <td style={{ color: "var(--ct-text-faint)" }}>—</td>
              <td style={{ color: "var(--ct-text-faint)" }}>—</td>
              <td style={{ color: "var(--ct-text-faint)" }}>—</td>
              <td style={{ color: "var(--ct-text-faint)" }}>—</td>
              <td style={{ color: "var(--ct-text-faint)" }}>—</td>
            </tr>
          ))}
          {lots.map((lot) => (
            <tr
              key={lot.id}
              className={
                lot.many_photos_review || lot.sku_duplicate_in_sale || lot.sku_text_mismatch
                  ? "ct-table-row--photo-outlier"
                  : undefined
              }
            >
              <td style={{ verticalAlign: "middle", width: 72 }}>
                <LotThumbnail lotId={lot.id} token={token} hasThumbnail={lot.has_thumbnail} size={56} />
              </td>
              <td
                className={lot.sku_needs_review || !lot.sku ? "ct-sku-cell ct-sku-cell--review" : "ct-sku-cell"}
                title={
                  lot.many_photos_review
                    ? "Unusually many photos for this sale — confirm images belong to this listing."
                    : lot.sku_needs_review || !lot.sku
                      ? "Verify handwritten lot code on colored estate tape (or enter manually on the lot page)."
                      : undefined
                }
              >
                <span className={lot.sku_needs_review || !lot.sku ? "ct-sku-pill" : undefined}>{lot.sku || "—"}</span>
              </td>
              <td>{lot.auction_title}</td>
              <td>{lot.starting_price ?? "-"}</td>
              <td>{lot.condition_text}</td>
              <td>
                <label className="ct-row" style={{ gap: "0.4rem" }}>
                  <input
                    type="checkbox"
                    checked={lot.is_completed}
                    onChange={(e) => void toggleLotCompleted(lot.id, e.target.checked)}
                  />
                  <span className="ct-muted" style={{ fontSize: "0.82rem" }}>
                    {lot.is_completed ? "Done" : "Not done"}
                  </span>
                </label>
              </td>
              <td>{lot.photo_count}</td>
              <td>
                <Link href={`/lots/${lot.id}#listing-details`} onClick={rememberDashboardScrollBeforeLotDetails}>
                  Details
                </Link>
              </td>
              <td>
                <button
                  type="button"
                  className="ct-btn ct-btn--ghost"
                  onClick={() => void deleteLotRow(lot.id)}
                  disabled={busyProcessing}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
        </table>
      </div>

      {lotCameraOpen ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Snap photos for one lot"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 10000,
            background: "rgba(0,0,0,0.82)",
            display: "flex",
            flexDirection: "column",
            padding: "8px 8px 0",
            boxSizing: "border-box",
            overflow: "hidden",
            height: "100dvh",
            maxHeight: "100dvh"
          }}
        >
          <div
            ref={lotCameraScrollRef}
            style={{
              // flex:1 + minHeight:0 lets this shrink so the action bar stays in view; do not set height:100% or it
              // consumes the full dialog and pins the footer below the clipped edge (bad in landscape).
              flex: "1 1 0",
              minHeight: 0,
              width: "100%",
              maxWidth: 520,
              margin: "0 auto",
              overflowY: "auto",
              overflowX: "hidden",
              WebkitOverflowScrolling: "touch",
              overscrollBehavior: "contain"
            }}
          >
            <div
              className="ct-card ct-card--padded"
              style={{ width: "100%", margin: "0 auto", boxSizing: "border-box", paddingBottom: "1rem" }}
            >
            <p className="ct-muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
              Snap as many photos as you need for this listing, then <strong>Done</strong> to return here and
              upload. Pinch or use +/− to zoom the live view; the saved photo matches what you see. Tap a thumbnail to
              preview; use the pencil to rotate, trim edges, or tweak brightness / contrast / saturation when needed;
              use the trash control to remove one shot.
            </p>
            <label className="ct-muted" style={{ display: "block", marginTop: "0.55rem", fontSize: "0.85rem" }}>
              SKU for this lot <span style={{ fontWeight: 400 }}>(optional)</span>
              <input
                className="ct-input"
                style={{ width: "100%", marginTop: 6, boxSizing: "border-box" }}
                value={singleLotManualSku}
                onChange={(e) => setSingleLotManualSku(e.target.value)}
                placeholder="e.g. 035 or Lot #042"
                autoComplete="off"
                enterKeyHint="done"
              />
            </label>
            {!lotCameraStream ? (
              <p style={{ margin: "0.75rem 0", textAlign: "center" }}>Starting camera…</p>
            ) : (
              <>
                <div
                  ref={lotCameraZoomWrapRef}
                  className="ct-lot-camera-live"
                  style={{
                    width: "100%",
                    maxHeight: "min(56dvh, 560px)",
                    height: "min(56dvh, 560px)",
                    marginTop: "0.65rem",
                    borderRadius: 8,
                    overflow: "hidden",
                    background: "#111",
                    touchAction: "none"
                  }}
                >
                  <video
                    ref={lotCameraVideoRef}
                    autoPlay
                    playsInline
                    muted
                    onLoadedData={() => setLotCameraPreviewReady(true)}
                    style={{
                      width: "100%",
                      height: "100%",
                      display: "block",
                      objectFit: "cover",
                      transform: `scale(${lotCameraZoom})`,
                      transformOrigin: "center center"
                    }}
                  />
                </div>
                <div
                  className="ct-row"
                  style={{
                    marginTop: "0.5rem",
                    flexWrap: "wrap",
                    gap: "0.45rem",
                    alignItems: "center",
                    justifyContent: "space-between"
                  }}
                >
                  <span className="ct-muted" style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.8)" }}>
                    Zoom {lotCameraZoom.toFixed(2)}×
                  </span>
                  <div className="ct-row" style={{ gap: 6, alignItems: "center" }}>
                    <button
                      type="button"
                      className="ct-btn ct-btn--ghost"
                      aria-label="Zoom out"
                      disabled={lotCameraZoom <= LOT_CAMERA_ZOOM_MIN + 0.001}
                      onClick={() => setLotCameraZoomLevel(lotCameraZoom - LOT_CAMERA_ZOOM_STEP)}
                      style={{ minWidth: 44, minHeight: 44, padding: 0, color: "#fff" }}
                    >
                      <Minus size={22} aria-hidden />
                    </button>
                    <button
                      type="button"
                      className="ct-btn ct-btn--ghost"
                      aria-label="Zoom in"
                      disabled={lotCameraZoom >= LOT_CAMERA_ZOOM_MAX - 0.001}
                      onClick={() => setLotCameraZoomLevel(lotCameraZoom + LOT_CAMERA_ZOOM_STEP)}
                      style={{ minWidth: 44, minHeight: 44, padding: 0, color: "#fff" }}
                    >
                      <Plus size={22} aria-hidden />
                    </button>
                  </div>
                </div>
              </>
            )}
            <p style={{ margin: "0.65rem 0 0", fontSize: "0.95rem" }}>
              <strong>
                {lotCameraSnaps.length} / {CTBIDS_MAX_PHOTOS_PER_LOT}
              </strong>{" "}
              photo{lotCameraSnaps.length === 1 ? "" : "s"} captured
              {lotCameraSnaps.length >= CTBIDS_MAX_PHOTOS_PER_LOT ? (
                <span className="ct-muted" style={{ display: "block", marginTop: 4, fontSize: "0.82rem" }}>
                  CTBids limit reached — tap <strong>Done</strong> to add these to the upload box, or remove shots with
                  the trash icon.
                </span>
              ) : null}
            </p>
            {lotCameraSnapThumbUrls.length > 0 ? (
              <div style={{ marginTop: "0.65rem", width: "100%" }}>
                <p
                  className="ct-muted"
                  style={{ margin: "0 0 6px", fontSize: "0.78rem", color: "rgba(255,255,255,0.75)" }}
                >
                  Captured (scroll sideways) — tap to preview, pencil to edit
                </p>
                <div
                  ref={lotCameraStripRef}
                  style={{
                    display: "flex",
                    gap: 8,
                    overflowX: "auto",
                    overflowY: "hidden",
                    padding: "10px 4px 12px",
                    WebkitOverflowScrolling: "touch",
                    scrollSnapType: "x proximity"
                  }}
                >
                  {lotCameraSnapThumbUrls.map((url, i) => (
                    <div
                      key={url}
                      style={{
                        position: "relative",
                        flex: "0 0 auto",
                        width: 56,
                        height: 56,
                        scrollSnapAlign: "start"
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => setLotCameraViewerIndex(i)}
                        aria-label={`Preview captured photo ${i + 1}`}
                        style={{
                          display: "block",
                          width: 56,
                          height: 56,
                          padding: 0,
                          border: "1px solid rgba(255,255,255,0.25)",
                          borderRadius: 6,
                          overflow: "hidden",
                          cursor: "pointer",
                          background: "#111"
                        }}
                      >
                        <img
                          src={url}
                          alt=""
                          width={56}
                          height={56}
                          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                        />
                      </button>
                      <button
                        type="button"
                        aria-label={`Edit captured photo ${i + 1}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          openLotCameraEdit(i);
                        }}
                        className="ct-btn ct-btn--ghost"
                        style={{
                          position: "absolute",
                          bottom: -4,
                          left: -4,
                          minWidth: 36,
                          minHeight: 36,
                          width: 36,
                          height: 36,
                          padding: 0,
                          borderRadius: "50%",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          background: "rgba(12,15,18,0.92)",
                          border: "1px solid rgba(255,255,255,0.35)",
                          color: "#c8d4ff",
                          boxShadow: "0 1px 4px rgba(0,0,0,0.45)"
                        }}
                      >
                        <Pencil size={16} aria-hidden />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete captured photo ${i + 1}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          removeLotCameraSnap(i);
                        }}
                        className="ct-btn ct-btn--ghost"
                        style={{
                          position: "absolute",
                          top: -4,
                          right: -4,
                          minWidth: 36,
                          minHeight: 36,
                          width: 36,
                          height: 36,
                          padding: 0,
                          borderRadius: "50%",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          background: "rgba(12,15,18,0.92)",
                          border: "1px solid rgba(255,255,255,0.35)",
                          color: "#f5a5a5",
                          boxShadow: "0 1px 4px rgba(0,0,0,0.45)"
                        }}
                      >
                        <Trash2 size={16} aria-hidden />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            </div>
          </div>
          <div
            style={{
              flexShrink: 0,
              width: "100%",
              maxWidth: 520,
              margin: "0 auto",
              paddingTop: "10px",
              paddingLeft: 8,
              paddingRight: 8,
              paddingBottom: "max(12px, calc(env(safe-area-inset-bottom, 0px) + 8px))",
              boxSizing: "border-box",
              background: "rgba(12, 12, 14, 0.96)",
              borderTop: "1px solid rgba(255,255,255,0.12)",
              backdropFilter: "blur(8px)"
            }}
          >
            <div
              className="ct-row ct-row--gap-lg"
              style={{
                flexWrap: "wrap",
                justifyContent: "space-between",
                gap: "0.5rem",
                alignItems: "center",
                width: "100%"
              }}
            >
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => tryCancelLotCameraSession()}>
                Cancel
              </button>
              <div className="ct-row" style={{ gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="ct-btn ct-btn--primary"
                  onClick={() => snapLotPhoto()}
                  disabled={
                    !lotCameraStream ||
                    !lotCameraPreviewReady ||
                    lotCameraSnaps.length >= CTBIDS_MAX_PHOTOS_PER_LOT
                  }
                >
                  Snap photo
                </button>
                <button type="button" className="ct-btn" onClick={() => finishLotCameraSession()}>
                  Done
                </button>
              </div>
            </div>
          </div>

          {lotCameraViewerIndex !== null &&
          lotCameraViewerIndex >= 0 &&
          lotCameraViewerIndex < lotCameraSnapThumbUrls.length ? (
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Photo preview"
              onClick={() => setLotCameraViewerIndex(null)}
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 10001,
                background: "rgba(0,0,0,0.94)",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                padding: "12px",
                paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
                boxSizing: "border-box"
              }}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: "0.75rem",
                  width: "100%",
                  maxWidth: "min(100vw - 24px, 720px)"
                }}
              >
                <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="ct-muted" style={{ color: "rgba(255,255,255,0.85)", fontSize: "0.9rem" }}>
                    Photo {lotCameraViewerIndex + 1} of {lotCameraSnapThumbUrls.length}
                  </span>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Close preview"
                    onClick={() => setLotCameraViewerIndex(null)}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <X size={22} aria-hidden />
                  </button>
                </div>
                <img
                  src={lotCameraSnapThumbUrls[lotCameraViewerIndex] ?? ""}
                  alt={`Captured photo ${lotCameraViewerIndex + 1}`}
                  style={{
                    maxWidth: "100%",
                    maxHeight: "min(72vh, 640px)",
                    width: "auto",
                    height: "auto",
                    objectFit: "contain",
                    borderRadius: 8,
                    border: "1px solid rgba(255,255,255,0.2)"
                  }}
                />
                <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
                  {lotCameraSnapThumbUrls.length > 1 ? (
                    <>
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        aria-label="Previous photo"
                        disabled={lotCameraViewerIndex <= 0}
                        onClick={() => setLotCameraViewerIndex((j) => (j !== null && j > 0 ? j - 1 : j))}
                        style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                      >
                        <ChevronLeft size={24} aria-hidden />
                      </button>
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        aria-label="Next photo"
                        disabled={lotCameraViewerIndex >= lotCameraSnapThumbUrls.length - 1}
                        onClick={() =>
                          setLotCameraViewerIndex((j) =>
                            j !== null && j < lotCameraSnapThumbUrls.length - 1 ? j + 1 : j
                          )
                        }
                        style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                      >
                        <ChevronRight size={24} aria-hidden />
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    onClick={() => {
                      const idx = lotCameraViewerIndex;
                      if (idx !== null) openLotCameraEdit(idx);
                    }}
                    style={{ color: "#fff" }}
                  >
                    <Pencil size={16} aria-hidden style={{ verticalAlign: "middle", marginRight: 6 }} />
                    Edit
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--danger"
                    onClick={() => {
                      const idx = lotCameraViewerIndex;
                      if (idx !== null) removeLotCameraSnap(idx);
                    }}
                  >
                    Delete this photo
                  </button>
                </div>
                <p className="ct-muted" style={{ margin: 0, fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  Tap outside the image to close
                </p>
              </div>
            </div>
          ) : null}

          {lotCameraEditIndex !== null &&
          lotCameraEditIndex >= 0 &&
          lotCameraEditIndex < lotCameraSnapThumbUrls.length ? (
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Edit captured photo"
              onClick={() => closeLotCameraEdit()}
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 10002,
                background: "rgba(0,0,0,0.94)",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                padding: "12px",
                paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
                boxSizing: "border-box"
              }}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "stretch",
                  gap: "0.75rem",
                  width: "100%",
                  maxWidth: "min(100vw - 24px, 520px)"
                }}
              >
                <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="ct-muted" style={{ color: "rgba(255,255,255,0.9)", fontSize: "0.95rem" }}>
                    Edit photo {lotCameraEditIndex + 1}
                  </span>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Close editor"
                    disabled={lotCameraEditBusy}
                    onClick={() => closeLotCameraEdit()}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <X size={22} aria-hidden />
                  </button>
                </div>
                <div
                  style={{
                    overflow: "hidden",
                    maxHeight: "min(50dvh, 480px)",
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: 8,
                    border: "1px solid rgba(255,255,255,0.2)",
                    background: "#111"
                  }}
                >
                  <div
                    style={{
                      transform: `rotate(${lotCameraEditRotation}deg)`,
                      transformOrigin: "center center"
                    }}
                  >
                    <img
                      src={lotCameraSnapThumbUrls[lotCameraEditIndex] ?? ""}
                      alt=""
                      style={{
                        maxWidth: "min(92vw, 480px)",
                        maxHeight: "min(46dvh, 420px)",
                        width: "auto",
                        height: "auto",
                        display: "block",
                        objectFit: "contain",
                        clipPath: `inset(${lotCameraEditTrim}%)`,
                        filter: rasterAdjustCssFilter({
                          brightnessPct: lotCameraEditBrightness,
                          contrastPct: lotCameraEditContrast,
                          saturationPct: lotCameraEditSaturation
                        })
                      }}
                    />
                  </div>
                </div>
                <div className="ct-row" style={{ flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Rotate counter-clockwise"
                    disabled={lotCameraEditBusy}
                    onClick={() => setLotCameraEditRotation((r) => (r + 270) % 360)}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <RotateCcw size={22} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Rotate clockwise"
                    disabled={lotCameraEditBusy}
                    onClick={() => setLotCameraEditRotation((r) => (r + 90) % 360)}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <RotateCw size={22} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    disabled={lotCameraEditBusy}
                    onClick={() => {
                      setLotCameraEditRotation(0);
                      setLotCameraEditTrim(0);
                      setLotCameraEditBrightness(100);
                      setLotCameraEditContrast(100);
                      setLotCameraEditSaturation(100);
                    }}
                    style={{ color: "#fff" }}
                  >
                    Reset
                  </button>
                </div>
                <label className="ct-muted" style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.85)" }}>
                  Trim edges (uniform %)
                  <input
                    type="range"
                    min={0}
                    max={35}
                    step={1}
                    value={lotCameraEditTrim}
                    disabled={lotCameraEditBusy}
                    onChange={(e) => setLotCameraEditTrim(Number(e.target.value))}
                    style={{ width: "100%", marginTop: 8, display: "block" }}
                  />
                  <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>{lotCameraEditTrim}%</span>
                </label>
                <div
                  className="ct-muted"
                  style={{
                    fontSize: "0.85rem",
                    color: "rgba(255,255,255,0.85)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.5rem"
                  }}
                >
                  <label>
                    Brightness (100% = unchanged)
                    <input
                      type="range"
                      min={50}
                      max={150}
                      step={1}
                      value={lotCameraEditBrightness}
                      disabled={lotCameraEditBusy}
                      onChange={(e) => setLotCameraEditBrightness(Number(e.target.value))}
                      style={{ width: "100%", marginTop: 6, display: "block" }}
                    />
                    <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                      {lotCameraEditBrightness}%
                    </span>
                  </label>
                  <label>
                    Contrast
                    <input
                      type="range"
                      min={50}
                      max={150}
                      step={1}
                      value={lotCameraEditContrast}
                      disabled={lotCameraEditBusy}
                      onChange={(e) => setLotCameraEditContrast(Number(e.target.value))}
                      style={{ width: "100%", marginTop: 6, display: "block" }}
                    />
                    <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                      {lotCameraEditContrast}%
                    </span>
                  </label>
                  <label>
                    Saturation (0% = grayscale)
                    <input
                      type="range"
                      min={0}
                      max={150}
                      step={1}
                      value={lotCameraEditSaturation}
                      disabled={lotCameraEditBusy}
                      onChange={(e) => setLotCameraEditSaturation(Number(e.target.value))}
                      style={{ width: "100%", marginTop: 6, display: "block" }}
                    />
                    <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                      {lotCameraEditSaturation}%
                    </span>
                  </label>
                </div>
                <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    disabled={lotCameraEditBusy}
                    onClick={() => closeLotCameraEdit()}
                    style={{ color: "#fff" }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--primary"
                    disabled={lotCameraEditBusy}
                    onClick={() => void applyLotCameraEdit()}
                  >
                    {lotCameraEditBusy ? "Applying…" : "Apply"}
                  </button>
                </div>
                <p className="ct-muted" style={{ margin: 0, fontSize: "0.76rem", color: "rgba(255,255,255,0.6)" }}>
                  Apply saves a new JPEG (trim, rotate, brightness, contrast, color). Tap outside to cancel.
                </p>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <h2 className="ct-section-title">Exports</h2>
      <div className="ct-card ct-card--padded ct-exports">
        <p className="ct-lead" style={{ marginTop: 0 }}>
          Download a file of everything in the <strong>sale you selected above</strong> — titles, descriptions, prices,
          and SKUs — for spreadsheets or printing.
        </p>
        <div className="ct-export-actions">
          <button
            type="button"
            className="ct-export-btn"
            onClick={() => void downloadExport("markdown")}
            disabled={exporting !== "" && exporting !== "markdown"}
          >
            <FileText size={16} aria-hidden />
            {exporting === "markdown" ? "Downloading..." : "Markdown Export"}
          </button>
          <button
            type="button"
            className="ct-export-btn"
            onClick={() => void downloadExport("json")}
            disabled={exporting !== "" && exporting !== "json"}
          >
            <Database size={16} aria-hidden />
            {exporting === "json" ? "Downloading..." : "JSON Export"}
          </button>
          <button
            type="button"
            className="ct-export-btn"
            onClick={() => void downloadExport("csv")}
            disabled={exporting !== "" && exporting !== "csv"}
          >
            <FileSpreadsheet size={16} aria-hidden />
            {exporting === "csv" ? "Downloading..." : "CTBids CSV"}
          </button>
        </div>
        <hr style={{ border: 0, borderTop: "1px solid var(--ct-border)", margin: "1.1rem 0" }} />
        <p className="ct-lead" style={{ marginBottom: "0.5rem" }}>
          <strong>Photo ZIP (full sale)</strong> — Download every catalog photo for this sale in one file. Large sales
          can take a few minutes; keep this tab open, then press <strong>Download ZIP</strong> when it appears.
        </p>
        <details className="ct-help-details" style={{ marginBottom: "0.65rem" }}>
          <summary>How the photo ZIP is organized</summary>
          <div className="ct-help-details__body">
            <ul>
              <li>
                Original images, <strong>one folder per lot</strong> (named by SKU, e.g. <code>lot-045</code>; lots
                without a SKU use <code>lot-no-sku-…</code>).
              </li>
              <li>
                Progress counts update every few seconds while the server builds the archive.
              </li>
              <li>
                To zip <strong>one listing only</strong>, open that lot&apos;s Details page and use the download there.
              </li>
            </ul>
          </div>
        </details>
        <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className="ct-export-btn"
            onClick={() => void startSalePhotosZipExport()}
            disabled={
              !selectedSaleId ||
              selectedSaleClosed ||
              saleZipBusy ||
              busyProcessing ||
              saleZipExport?.status === "queued" ||
              saleZipExport?.status === "processing"
            }
          >
            <Archive size={16} aria-hidden />
            {saleZipBusy ? "Starting…" : "Prepare sale photos (ZIP)"}
          </button>
          {saleZipExport?.ready ? (
            <button type="button" className="ct-btn ct-btn--primary" onClick={() => void downloadSaleZipExport()}>
              Download ZIP
            </button>
          ) : null}
        </div>
        {saleZipExport && (saleZipExport.status === "queued" || saleZipExport.status === "processing") ? (
          <p className="ct-muted" style={{ fontSize: "0.85rem", marginTop: "0.55rem" }}>
            Building… <strong>{saleZipExport.photo_files_written}</strong> /{" "}
            <strong>{saleZipExport.total_photos_expected}</strong> files in archive
          </p>
        ) : null}
        {saleZipExport?.status === "failed" ? (
          <p style={{ color: "var(--ct-error)", fontSize: "0.85rem", marginTop: "0.55rem" }}>
            {saleZipExport.error_message || "Export failed"}
          </p>
        ) : null}
      </div>

      {folderPick &&
      stagingViewerIndex !== null &&
      stagingViewerIndex >= 0 &&
      stagingViewerIndex < folderPick.thumbUrls.length ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Staged photo preview"
          onClick={() => !uploading && setStagingViewerIndex(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 10003,
            background: "rgba(0,0,0,0.94)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "12px",
            paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
            boxSizing: "border-box"
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "0.75rem",
              width: "100%",
              maxWidth: "min(100vw - 24px, 720px)"
            }}
          >
            <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
              <span className="ct-muted" style={{ color: "rgba(255,255,255,0.85)", fontSize: "0.9rem" }}>
                Staged photo {stagingViewerIndex + 1} of {folderPick.thumbUrls.length}
              </span>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Close preview"
                disabled={uploading}
                onClick={() => setStagingViewerIndex(null)}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <X size={22} aria-hidden />
              </button>
            </div>
            <img
              src={folderPick.thumbUrls[stagingViewerIndex] ?? ""}
              alt={`Staged photo ${stagingViewerIndex + 1}`}
              style={{
                maxWidth: "100%",
                maxHeight: "min(72vh, 640px)",
                width: "auto",
                height: "auto",
                objectFit: "contain",
                borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.2)"
              }}
            />
            <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
              {folderPick.thumbUrls.length > 1 ? (
                <>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Previous staged photo"
                    disabled={uploading || stagingViewerIndex <= 0}
                    onClick={() => setStagingViewerIndex((j) => (j !== null && j > 0 ? j - 1 : j))}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <ChevronLeft size={24} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Next staged photo"
                    disabled={uploading || stagingViewerIndex >= folderPick.thumbUrls.length - 1}
                    onClick={() =>
                      setStagingViewerIndex((j) =>
                        j !== null && j < folderPick.thumbUrls.length - 1 ? j + 1 : j
                      )
                    }
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <ChevronRight size={24} aria-hidden />
                  </button>
                </>
              ) : null}
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                disabled={uploading}
                onClick={() => {
                  const idx = stagingViewerIndex;
                  if (idx !== null) openStagingEdit(idx);
                }}
                style={{ color: "#fff" }}
              >
                <Pencil size={16} aria-hidden style={{ verticalAlign: "middle", marginRight: 6 }} />
                Edit
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--danger"
                disabled={uploading}
                onClick={() => {
                  const idx = stagingViewerIndex;
                  if (idx !== null) removeStagingImage(idx);
                }}
              >
                Delete this photo
              </button>
            </div>
            <p className="ct-muted" style={{ margin: 0, fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
              Tap outside the image to close
            </p>
          </div>
        </div>
      ) : null}

      {folderPick &&
      stagingEditIndex !== null &&
      stagingEditIndex >= 0 &&
      stagingEditIndex < folderPick.thumbUrls.length ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Edit staged photo"
          onClick={() => closeStagingEdit()}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 10004,
            background: "rgba(0,0,0,0.94)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "12px",
            paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
            boxSizing: "border-box"
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "stretch",
              gap: "0.75rem",
              width: "100%",
              maxWidth: "min(100vw - 24px, 520px)"
            }}
          >
            <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
              <span className="ct-muted" style={{ color: "rgba(255,255,255,0.9)", fontSize: "0.95rem" }}>
                Edit staged photo {stagingEditIndex + 1}
              </span>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Close editor"
                disabled={stagingEditBusy}
                onClick={() => closeStagingEdit()}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <X size={22} aria-hidden />
              </button>
            </div>
            <div
              style={{
                overflow: "hidden",
                maxHeight: "min(50dvh, 480px)",
                width: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.2)",
                background: "#111"
              }}
            >
              <div
                style={{
                  transform: `rotate(${stagingEditRotation}deg)`,
                  transformOrigin: "center center"
                }}
              >
                <img
                  src={folderPick.thumbUrls[stagingEditIndex] ?? ""}
                  alt=""
                  style={{
                    maxWidth: "min(92vw, 480px)",
                    maxHeight: "min(46dvh, 420px)",
                    width: "auto",
                    height: "auto",
                    display: "block",
                    objectFit: "contain",
                    clipPath: `inset(${stagingEditTrim}%)`,
                    filter: rasterAdjustCssFilter({
                      brightnessPct: stagingEditBrightness,
                      contrastPct: stagingEditContrast,
                      saturationPct: stagingEditSaturation
                    })
                  }}
                />
              </div>
            </div>
            <div className="ct-row" style={{ flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Rotate counter-clockwise"
                disabled={stagingEditBusy}
                onClick={() => setStagingEditRotation((r) => (r + 270) % 360)}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <RotateCcw size={22} aria-hidden />
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Rotate clockwise"
                disabled={stagingEditBusy}
                onClick={() => setStagingEditRotation((r) => (r + 90) % 360)}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <RotateCw size={22} aria-hidden />
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                disabled={stagingEditBusy}
                onClick={() => {
                  setStagingEditRotation(0);
                  setStagingEditTrim(0);
                  setStagingEditBrightness(100);
                  setStagingEditContrast(100);
                  setStagingEditSaturation(100);
                }}
                style={{ color: "#fff" }}
              >
                Reset
              </button>
            </div>
            <label className="ct-muted" style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.85)" }}>
              Trim edges (uniform %)
              <input
                type="range"
                min={0}
                max={35}
                step={1}
                value={stagingEditTrim}
                disabled={stagingEditBusy}
                onChange={(e) => setStagingEditTrim(Number(e.target.value))}
                style={{ width: "100%", marginTop: 8, display: "block" }}
              />
              <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>{stagingEditTrim}%</span>
            </label>
            <div
              className="ct-muted"
              style={{
                fontSize: "0.85rem",
                color: "rgba(255,255,255,0.85)",
                display: "flex",
                flexDirection: "column",
                gap: "0.5rem"
              }}
            >
              <label>
                Brightness (100% = unchanged)
                <input
                  type="range"
                  min={50}
                  max={150}
                  step={1}
                  value={stagingEditBrightness}
                  disabled={stagingEditBusy}
                  onChange={(e) => setStagingEditBrightness(Number(e.target.value))}
                  style={{ width: "100%", marginTop: 6, display: "block" }}
                />
                <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  {stagingEditBrightness}%
                </span>
              </label>
              <label>
                Contrast
                <input
                  type="range"
                  min={50}
                  max={150}
                  step={1}
                  value={stagingEditContrast}
                  disabled={stagingEditBusy}
                  onChange={(e) => setStagingEditContrast(Number(e.target.value))}
                  style={{ width: "100%", marginTop: 6, display: "block" }}
                />
                <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  {stagingEditContrast}%
                </span>
              </label>
              <label>
                Saturation (0% = grayscale)
                <input
                  type="range"
                  min={0}
                  max={150}
                  step={1}
                  value={stagingEditSaturation}
                  disabled={stagingEditBusy}
                  onChange={(e) => setStagingEditSaturation(Number(e.target.value))}
                  style={{ width: "100%", marginTop: 6, display: "block" }}
                />
                <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  {stagingEditSaturation}%
                </span>
              </label>
            </div>
            <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                disabled={stagingEditBusy}
                onClick={() => closeStagingEdit()}
                style={{ color: "#fff" }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                disabled={stagingEditBusy}
                onClick={() => void applyStagingEdit()}
              >
                {stagingEditBusy ? "Applying…" : "Apply"}
              </button>
            </div>
            <p className="ct-muted" style={{ margin: 0, fontSize: "0.76rem", color: "rgba(255,255,255,0.6)" }}>
              Apply replaces this file in the upload list (including tone). Tap outside to cancel.
            </p>
          </div>
        </div>
      ) : null}

      {showDashboardScrollJumpNav ? (
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
    </main>
  );
}

