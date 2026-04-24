"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Database, FileSpreadsheet, FileText } from "lucide-react";
import { LotThumbnail } from "../../components/LotThumbnail";
import { apiFetch, apiUploadFiles, API_BASE } from "../../lib/api";

const SALE_STORAGE_KEY = "ct_selected_sale_id";

function isAuthFailureMessage(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("invalid authentication") ||
    m.includes("401") ||
    m.includes("unauthorized")
  );
}

type Sale = {
  id: string;
  name: string;
  closed_at: string | null;
  created_at: string;
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

const PREVIEW_THUMB_MAX = 48;

type FolderPickPreview = {
  folderLabel: string;
  imageFiles: File[];
  thumbUrls: string[];
  skippedCount: number;
  /** When true, this upload is processed as one catalog lot (one SKU row) for every photo. */
  singleLotMode?: boolean;
};

/** Merge or replace staged files for one-lot uploads (picker + inline camera). */
function applySingleLotFilesToStaging(
  prev: FolderPickPreview | null,
  incoming: File[],
  extraSkipped: number
): FolderPickPreview {
  const appendSingleLot = Boolean(prev?.singleLotMode);
  if (appendSingleLot) {
    const merged = [...prev!.imageFiles, ...incoming];
    const mergedSkipped = prev!.skippedCount + extraSkipped;
    if (prev!.thumbUrls.length) revokeObjectUrls(prev!.thumbUrls);
    const forThumbs = merged.slice(0, PREVIEW_THUMB_MAX);
    const thumbUrls = forThumbs.map((f) => URL.createObjectURL(f));
    return {
      folderLabel:
        merged.length === 1 ? folderLabelFromFile(merged[0]!) : `One lot (${merged.length} photos)`,
      imageFiles: merged,
      thumbUrls,
      skippedCount: mergedSkipped,
      singleLotMode: true
    };
  }
  if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
  const forThumbs = incoming.slice(0, PREVIEW_THUMB_MAX);
  const thumbUrls = forThumbs.map((f) => URL.createObjectURL(f));
  return {
    folderLabel:
      incoming.length === 1 ? folderLabelFromFile(incoming[0]!) : `One lot (${incoming.length} photos)`,
    imageFiles: incoming,
    thumbUrls,
    skippedCount: extraSkipped,
    singleLotMode: true
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
  const lotCameraStripRef = useRef<HTMLDivElement | null>(null);
  const inventoryFirstLoad = useRef(true);
  const [token, setToken] = useState<string>("");
  const [sales, setSales] = useState<Sale[]>([]);
  const [selectedSaleId, setSelectedSaleId] = useState<string>("");
  const selectedSaleIdRef = useRef<string>("");
  const [newSaleName, setNewSaleName] = useState("");
  const [creatingSale, setCreatingSale] = useState(false);
  const [closingSale, setClosingSale] = useState(false);
  const [lots, setLots] = useState<Lot[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [folder, setFolder] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  /** When set, top-level subfolders named with 2–6 digits (e.g. ``035/``) set each lot's SKU on finalize. */
  const [inheritSkuFromSubfolder, setInheritSkuFromSubfolder] = useState(false);
  const [exporting, setExporting] = useState<"" | "markdown" | "json" | "csv">("");
  const [folderPick, setFolderPick] = useState<FolderPickPreview | null>(null);
  /** Optional tape/lot digits for one-lot uploads (camera + file picker). */
  const [singleLotManualSku, setSingleLotManualSku] = useState("");
  /** One loading row per catalog photo while the active job runs (SSE). */
  const [photoPendingSlots, setPhotoPendingSlots] = useState<PhotoPendingSlot[]>([]);
  const [inInventoryPhase, setInInventoryPhase] = useState(false);
  folderPickRef.current = folderPick;
  selectedSaleIdRef.current = selectedSaleId;

  const busyProcessing = jobs.some((j) => j.status === "processing");
  const processingJobId = useMemo(
    () => jobs.find((j) => j.status === "processing")?.id ?? null,
    [jobs]
  );
  const selectedSale = useMemo(() => sales.find((s) => s.id === selectedSaleId) ?? null, [sales, selectedSaleId]);
  const selectedSaleClosed = Boolean(selectedSale?.closed_at);

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
    void apiFetch("/sales", t)
      .then((sl) => {
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
  }, [router]);

  useEffect(() => {
    if (!token || !selectedSaleId) {
      return;
    }
    const t = token;
    if (inventoryFirstLoad.current) {
      setLoading(true);
    }
    localStorage.setItem(SALE_STORAGE_KEY, selectedSaleId);
    Promise.all([
      apiFetch(`/lots?sale_id=${encodeURIComponent(selectedSaleId)}`, t),
      apiFetch(`/jobs?sale_id=${encodeURIComponent(selectedSaleId)}`, t)
    ])
      .then(([lotsData, jobsData]) => {
        setLots(lotsData as Lot[]);
        setJobs(jobsData as Job[]);
      })
      .catch((e) => {
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
        if (inventoryFirstLoad.current) {
          inventoryFirstLoad.current = false;
          setLoading(false);
        }
      });
  }, [token, selectedSaleId, router]);

  useEffect(() => {
    if (!token || loading || !processingJobId) return;

    const url = `${API_BASE}/jobs/${processingJobId}/events?access_token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);

    const refreshJobsAndLots = () => {
      const sid = selectedSaleIdRef.current;
      if (!sid) return;
      void apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)
        .then((d) => setJobs(d as Job[]))
        .catch(() => {
          /* ignore */
        });
      void apiFetch(`/lots?sale_id=${encodeURIComponent(sid)}`, token)
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
          refreshJobsAndLots();
        }
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      es.close();
    };
    return () => {
      es.close();
      // Intentionally do not clear photoPendingSlots here: React Strict Mode remounts this effect
      // before the worker finishes, and the server replays recent SSE payloads on reconnect.
    };
  }, [token, loading, processingJobId]);

  useEffect(() => {
    if (!processingJobId) {
      setPhotoPendingSlots([]);
      setInInventoryPhase(false);
    }
  }, [processingJobId]);

  useEffect(() => {
    if (!token || loading || !busyProcessing) return;

    const pollMs = 15000;
    const tick = async () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      try {
        const sid = selectedSaleIdRef.current;
        if (!sid) return;
        const jlist = (await apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)) as Job[];
        setJobs((prev) => {
          const hadProcessing = prev.some((j) => j.status === "processing");
          const nowProcessing = jlist.some((j) => j.status === "processing");
          if (hadProcessing && !nowProcessing) {
            void apiFetch(`/lots?sale_id=${encodeURIComponent(sid)}`, token).then((lotsData) =>
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
    const id = setInterval(() => void tick(), pollMs);
    const onVis = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [token, loading, busyProcessing, selectedSaleId]);

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

  function clearFolderPick() {
    singleLotCaptureChainingRef.current = false;
    setSingleLotManualSku("");
    setFolderPick((prev) => {
      if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
      return null;
    });
  }

  function stopLotCameraTracks() {
    lotCameraStreamRef.current?.getTracks().forEach((t) => t.stop());
    lotCameraStreamRef.current = null;
    setLotCameraStream(null);
    setLotCameraOpen(false);
    setLotCameraPreviewReady(false);
  }

  function cancelLotCameraSession() {
    lotCameraSnapsRef.current = [];
    setLotCameraSnaps([]);
    stopLotCameraTracks();
  }

  function finishLotCameraSession() {
    const files = [...lotCameraSnapsRef.current];
    lotCameraSnapsRef.current = [];
    setLotCameraSnaps([]);
    stopLotCameraTracks();
    if (!files.length) return;
    setFolderPick((prev) => applySingleLotFilesToStaging(prev, files, 0));
  }

  function snapLotPhoto() {
    const v = lotCameraVideoRef.current;
    if (!v || v.readyState < 2 || v.videoWidth < 2) return;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(v, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        const file = new File([blob], `lot-${Date.now()}.jpg`, { type: "image/jpeg" });
        lotCameraSnapsRef.current = [...lotCameraSnapsRef.current, file];
        setLotCameraSnaps([...lotCameraSnapsRef.current]);
      },
      "image/jpeg",
      0.88
    );
  }

  async function openCreateOneLotFlow() {
    if (typeof window === "undefined") return;
    const md = navigator.mediaDevices;
    if (window.isSecureContext && md?.getUserMedia) {
      setLotCameraSnaps([]);
      lotCameraSnapsRef.current = [];
      setLotCameraPreviewReady(false);
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
      setFolderPick((prev) => applySingleLotFilesToStaging(prev, imageFiles, skippedCount));
    } else {
      setFolderPick((prev) => {
        if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
        const forThumbs = imageFiles.slice(0, PREVIEW_THUMB_MAX);
        const thumbUrls = forThumbs.map((f) => URL.createObjectURL(f));
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
    setUploading(true);
    try {
      const form = new FormData();
      form.append("sale_id", selectedSaleId);
      form.append("inherit_sku_from_subfolder", inheritSkuFromSubfolder ? "true" : "false");
      form.append("single_lot_mode", folderPick.singleLotMode ? "true" : "false");
      if (folderPick.singleLotMode && singleLotManualSku.trim()) {
        form.append("single_lot_manual_sku", singleLotManualSku.trim());
      }
      for (const file of folderPick.imageFiles) {
        const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
        form.append("files", file, rel);
      }
      const created = (await apiUploadFiles("/jobs/upload", token, form)) as Job;
      setJobs((prev) => [created, ...prev]);
      clearFolderPick();
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
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Loading dashboard…</p>
        </div>
      </main>
    );
  }

  return (
    <main className="ct-shell">
      <header className="ct-page-header">
        <h1 className="ct-page-title">Lots dashboard</h1>
        <div className="ct-row ct-row--gap-lg">
          <Link href="/account" className="ct-btn ct-btn--ghost">
            Account
          </Link>
          <button type="button" className="ct-btn ct-btn--ghost" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <section className="ct-card ct-card--padded" style={{ marginBottom: "1.25rem" }}>
        <h2 className="ct-section-title" style={{ marginTop: 0 }}>
          Sales
        </h2>
        <p className="ct-muted" style={{ marginTop: 0, marginBottom: "0.75rem", maxWidth: "42rem" }}>
          Use separate sales to organize inventory by auction, client, or project. Lots and jobs are stored under the
          sale tab you select.
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
          <strong>A job is running.</strong> The lots table shows one <strong>Processing…</strong> row per photo in
          the batch; each slot updates when Gemini finishes that frame, then new catalog rows appear above as{" "}
          <code>lot_ready</code> events arrive. Use <strong>Cancel</strong> on the job row to stop after the current
          image. Job status also refreshes periodically as a fallback (paused when the tab is hidden).
        </div>
      )}
      {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
      <div style={{ marginBottom: "1.25rem" }}>
        <h2 className="ct-section-title" style={{ marginTop: 0 }}>
          Run photo processing job
        </h2>
        <p className="ct-muted" style={{ maxWidth: "45rem", marginBottom: "0.75rem" }}>
          <strong>Choose folder</strong> opens your system folder picker (it may not show photo thumbnails there —
          that is normal). After you confirm, you will see previews and counts here, then upload to the server — the
          job <strong>starts automatically</strong> and the table updates live from the SSE feed (spinner row while
          Gemini runs). For phone workflows, use <strong>Choose photos (phone)</strong> to pick images from camera
          roll/files and process right away.{" "}
          For <strong>several different items without lot tape on every group</strong>, put each item in its own
          subfolder inside the folder you pick (e.g. <code>Matryoshka/*.jpg</code> and <code>Turtle/*.jpg</code>) so
          each becomes its own row.           Use <strong>Create one lot…</strong> when every photo is the same listing (angles / details) and you want{" "}
          <strong>one SKU and one row</strong>. On your phone (HTTPS), that opens an in-page camera so you can{" "}
          <strong>Snap</strong> many photos before <strong>Done</strong>; if the camera cannot start, the file picker
          is used instead. <strong>Server path</strong> is for folders already on the machine running the API (no
          upload).
        </p>
        <label className="ct-row" style={{ gap: "0.5rem", marginBottom: "0.75rem", alignItems: "flex-start" }}>
          <input
            type="checkbox"
            checked={inheritSkuFromSubfolder}
            onChange={(e) => setInheritSkuFromSubfolder(e.target.checked)}
          />
          <span className="ct-muted" style={{ fontSize: "0.9rem", maxWidth: "44rem" }}>
            <strong>Use folder names as SKU</strong> when each lot lives in its own subfolder named with 2–6 digits
            only (e.g. <code>035/IMG…</code>, <code>036/IMG…</code>). Overrides model guesses for tape SKU when this
            job finishes each lot.
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
          Choose photos (phone)…
        </button>
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
        {folderPick && (
          <div className="ct-card ct-card--padded" style={{ marginTop: "0.85rem", maxWidth: 900 }}>
            {folderPick.singleLotMode ? (
              <div style={{ marginBottom: "0.65rem" }}>
                <p className="ct-muted" style={{ margin: "0 0 0.5rem", fontSize: "0.88rem" }}>
                  <strong>One-lot mode:</strong> after upload, every photo is merged into a single catalog lot (tape
                  reads on later frames will not start a new row).
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
                  ? `${folderPick.imageFiles.length} image${folderPick.imageFiles.length === 1 ? "" : "s"} in “${folderPick.folderLabel}”`
                  : `No catalog images found in “${folderPick.folderLabel}”`}
              </strong>
              {folderPick.skippedCount > 0 ? (
                <span className="ct-muted" style={{ fontSize: "0.82rem" }}>
                  ({folderPick.skippedCount} non-image file{folderPick.skippedCount === 1 ? "" : "s"} skipped)
                </span>
              ) : null}
            </div>
            {folderPick.imageFiles.length > 0 && (
              <div className="ct-thumb-strip">
                {folderPick.thumbUrls.map((url, i) => (
                  <img
                    key={`${url}`}
                    src={url}
                    alt=""
                    title={folderPick.imageFiles[i]?.name ?? ""}
                    width={72}
                    height={72}
                  />
                ))}
              </div>
            )}
            {folderPick.imageFiles.length > PREVIEW_THUMB_MAX ? (
              <p className="ct-muted" style={{ margin: "0 0 0.65rem", fontSize: "0.82rem" }}>
                Showing first {PREVIEW_THUMB_MAX} previews; all {folderPick.imageFiles.length} images will upload.
              </p>
            ) : null}
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
              <button type="button" className="ct-btn" onClick={clearFolderPick} disabled={uploading}>
                Clear selection
              </button>
            </div>
          </div>
        )}
        <div className="ct-row ct-row--gap-lg" style={{ marginTop: "0.85rem" }}>
          <span className="ct-muted" style={{ fontSize: "0.85rem" }}>
            or
          </span>
          <input
            className="ct-input"
            style={{ maxWidth: 360, width: "100%" }}
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            placeholder="Server folder path, e.g. C:\...\Images"
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
            {job.status === "queued" && (
              <button type="button" className="ct-btn ct-btn--primary" onClick={() => void runJob(job.id)} disabled={busyProcessing}>
                Run now
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

      <div className="ct-row ct-row--gap-lg" style={{ marginBottom: "0.65rem", alignItems: "baseline" }}>
        <h2 className="ct-section-title" style={{ margin: 0 }}>
          Lots ({lots.length})
        </h2>
        <span className="ct-muted">
          {lots.filter((l) => l.is_completed).length} completed / {lots.filter((l) => !l.is_completed).length} open
        </span>
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
                  {slot.status !== "queued" && inInventoryPhase ? " · Assigning to catalog…" : null}
                </div>
              </div>
            </div>
          </div>
        ))}
        {lots.map((lot) => (
          <div
            key={`m-${lot.id}`}
            className={`ct-lot-mobile-card ct-card ct-card--padded${lot.many_photos_review ? " ct-lot-mobile-card--warn" : ""}`}
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
              <Link href={`/lots/${lot.id}#listing-details`} className="ct-btn ct-btn--primary">
                Details
              </Link>
              <label className="ct-row" style={{ gap: "0.45rem", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={lot.is_completed}
                  onChange={(e) => void toggleLotCompleted(lot.id, e.target.checked)}
                />
                <span className="ct-muted" style={{ fontSize: "0.88rem" }}>
                  {lot.is_completed ? "Done" : "Open"}
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
            <th>Done</th>
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
                      <span style={{ color: "var(--ct-text-subtle)" }}> · Assigning to catalog…</span>
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
            <tr key={lot.id} className={lot.many_photos_review ? "ct-table-row--photo-outlier" : undefined}>
              <td style={{ verticalAlign: "middle", width: 72 }}>
                <LotThumbnail lotId={lot.id} token={token} hasThumbnail={lot.has_thumbnail} size={56} />
              </td>
              <td
                className={lot.sku_needs_review || !lot.sku ? "ct-sku-cell ct-sku-cell--review" : "ct-sku-cell"}
                title={
                  lot.many_photos_review
                    ? "Unusually many photos for this sale — confirm images belong to this listing."
                    : lot.sku_needs_review || !lot.sku
                      ? "Verify handwritten tape SKU (or enter manually on the lot page)."
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
                    {lot.is_completed ? "Done" : "Open"}
                  </span>
                </label>
              </td>
              <td>{lot.photo_count}</td>
              <td>
                <Link href={`/lots/${lot.id}#listing-details`}>Details</Link>
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
            padding: "12px",
            paddingBottom: "max(12px, env(safe-area-inset-bottom))",
            boxSizing: "border-box"
          }}
        >
          <div style={{ flex: 1, minHeight: 0 }} aria-hidden />
          <div
            className="ct-card ct-card--padded"
            style={{ maxWidth: 520, width: "100%", margin: "0 auto", boxSizing: "border-box", flexShrink: 0 }}
          >
            <p className="ct-muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
              Snap as many photos as you need for this listing, then <strong>Done</strong> to return here and
              upload.
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
              <video
                ref={lotCameraVideoRef}
                autoPlay
                playsInline
                muted
                onLoadedData={() => setLotCameraPreviewReady(true)}
                style={{
                  width: "100%",
                  maxHeight: "min(52vh, 420px)",
                  borderRadius: 8,
                  objectFit: "cover",
                  background: "#111"
                }}
              />
            )}
            <p style={{ margin: "0.65rem 0 0", fontSize: "0.95rem" }}>
              <strong>{lotCameraSnaps.length}</strong> photo{lotCameraSnaps.length === 1 ? "" : "s"} captured
            </p>
            <div className="ct-row ct-row--gap-lg" style={{ marginTop: "0.75rem", flexWrap: "wrap" }}>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                onClick={() => snapLotPhoto()}
                disabled={!lotCameraStream || !lotCameraPreviewReady}
              >
                Snap photo
              </button>
              <button type="button" className="ct-btn" onClick={() => finishLotCameraSession()}>
                Done
              </button>
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => cancelLotCameraSession()}>
                Cancel
              </button>
            </div>
          </div>
          {lotCameraSnapThumbUrls.length > 0 ? (
            <div style={{ marginTop: 10, flexShrink: 0, width: "100%", maxWidth: 520, marginLeft: "auto", marginRight: "auto" }}>
              <p
                className="ct-muted"
                style={{ margin: "0 0 6px", fontSize: "0.78rem", color: "rgba(255,255,255,0.75)" }}
              >
                Captured (scroll sideways)
              </p>
              <div
                ref={lotCameraStripRef}
                style={{
                  display: "flex",
                  gap: 8,
                  overflowX: "auto",
                  overflowY: "hidden",
                  padding: "6px 2px 4px",
                  WebkitOverflowScrolling: "touch",
                  scrollSnapType: "x proximity"
                }}
              >
                {lotCameraSnapThumbUrls.map((url, i) => (
                  <img
                    key={url}
                    src={url}
                    alt=""
                    aria-hidden
                    width={56}
                    height={56}
                    style={{
                      flex: "0 0 auto",
                      width: 56,
                      height: 56,
                      objectFit: "cover",
                      borderRadius: 6,
                      border: "1px solid rgba(255,255,255,0.25)",
                      scrollSnapAlign: "start"
                    }}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <h2 className="ct-section-title">Exports</h2>
      <div className="ct-card ct-card--padded ct-exports">
        <p className="ct-muted" style={{ marginTop: 0 }}>
          Exports include lots for the <strong>currently selected sale</strong> only.
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
      </div>
    </main>
  );
}

