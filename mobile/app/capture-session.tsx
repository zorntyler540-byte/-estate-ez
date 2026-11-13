import { CameraView, useCameraPermissions, useMicrophonePermissions, type FocusMode } from "expo-camera";
import * as FileSystem from "expo-file-system/legacy";
import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  PanResponder,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  useWindowDimensions,
  View,
  type AppStateStatus,
  type GestureResponderEvent
} from "react-native";
import { Text, TextInput } from "@/components/AppText";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { DraftPhotoStrip, PhotoPreviewModal } from "@/components/DraftPhotos";
import { useAuth } from "@/context/AuthContext";
import { useSyncActions } from "@/context/SyncContext";
import {
  CAMERA_ASPECT_PRESETS,
  aspectPresetById,
  centeredAspectRect,
  type CameraAspectId
} from "@/lib/cameraAspect";
import { MAX_DETAIL_CAPTURE_OPTIONS, pickLargestPictureSize } from "@/lib/cameraCapture";
import { librarySaveNote, photoLibraryBackupAvailable, saveCaptureToPhotoLibrary } from "@/lib/saveToPhotos";
import { CTBIDS_MAX_PHOTOS_PER_LOT } from "@/lib/catalogLimits";
import { captureModesFor } from "@/lib/clientCapture";
import {
  MAX_VIDEO_DURATION_SEC,
  MAX_VIDEOS_PER_LOT,
  isVideoUri,
  mediaKindFromName,
  splitMediaUris
} from "@/lib/mediaKind";
import { reopenForCapture } from "@/lib/queue/addPhotos";
import {
  addPhotoToDraft,
  createAppendDraft,
  createLotDraft,
  deleteLotDraft,
  deletePhotoFromDraft,
  findOpenAppendDraft,
  findOpenDraft,
  getLotDraft,
  listDraftPhotos,
  requeueDraft,
  updateDraftManualSku
} from "@/lib/queue/db";
import {
  captureQueue,
  moveCaptureIntoPlace,
  processPendingCaptures,
  queueDraftPhotoProcessing
} from "@/lib/queue/captureFiles";
import {
  encodeProcessRatio,
  processedUriForRaw,
  rawCaptureUri,
  type CaptureResult
} from "@/lib/queue/captureProcessing";
import type { LotDraft } from "@/lib/types";
import {
  MICROPHONE_OFF_NOTE,
  VIDEO_BITRATE,
  VIDEO_QUALITY,
  microphoneRecordPlan,
  videoRecordOptions,
  videoSizeProblem
} from "@/lib/videoCapture";
import { theme } from "@/constants/theme";
import { hitSlopFor } from "@/lib/a11y";

/** Pinch spread doubling ≈ this much extra zoom (0–1 native zoom range). */
const PINCH_ZOOM_SENSITIVITY = 0.6;

const ZOOM_PRESETS: { label: string; value: number }[] = [
  { label: "1×", value: 0 },
  { label: "2×", value: 0.25 },
  { label: "4×", value: 0.5 }
];

function clamp01(n: number): number {
  return Math.min(1, Math.max(0, n));
}

function AspectMask({
  layout,
  ratio
}: {
  layout: { width: number; height: number };
  ratio: number | null;
}) {
  const rect = centeredAspectRect(layout.width, layout.height, ratio);
  if (!ratio || (rect.width >= layout.width && rect.height >= layout.height)) {
    return null;
  }
  const { x, y, width: w, height: h } = rect;
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      <View style={[styles.maskBar, { left: 0, top: 0, width: layout.width, height: y }]} />
      <View
        style={[
          styles.maskBar,
          {
            left: 0,
            top: y + h,
            width: layout.width,
            height: Math.max(0, layout.height - y - h)
          }
        ]}
      />
      <View style={[styles.maskBar, { left: 0, top: y, width: x, height: h }]} />
      <View
        style={[
          styles.maskBar,
          {
            left: x + w,
            top: y,
            width: Math.max(0, layout.width - x - w),
            height: h
          }
        ]}
      />
      <View style={[styles.maskFrame, { left: x, top: y, width: w, height: h }]} />
    </View>
  );
}

function paramString(value: string | string[] | undefined): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value) && typeof value[0] === "string" && value[0].trim()) {
    return value[0].trim();
  }
  return null;
}

export default function CaptureSessionScreen() {
  const params = useLocalSearchParams<{
    channel?: string;
    draftId?: string;
    appendLotId?: string;
    appendSku?: string;
    appendTitle?: string;
    appendExistingCount?: string;
    appendSaleId?: string;
  }>();
  const resumeDraftId = paramString(params.draftId);
  const appendLotId = paramString(params.appendLotId);
  const appendSku = paramString(params.appendSku);
  const appendTitle = paramString(params.appendTitle);
  const appendExistingCount = Math.max(
    0,
    Number.parseInt(paramString(params.appendExistingCount) || "0", 10) || 0
  );
  const appendSaleId = paramString(params.appendSaleId);
  const isAppendMode = Boolean(appendLotId);
  const { selectedSaleId, selectedSaleName, signedInRole } = useAuth();
  const clientLogin = (signedInRole || "").toLowerCase() === "client";
  const captureModes = captureModesFor(signedInRole);
  const listingChannel = !clientLogin && params.channel === "facebook" ? "facebook" : "ctseller";
  const isFacebook = listingChannel === "facebook";
  const { requestUpload, setSyncPaused } = useSyncActions();
  const { width: windowWidth, height: windowHeight } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const landscape = windowWidth > windowHeight;
  const cameraRef = useRef<CameraView>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [micPermission, requestMicPermission] = useMicrophonePermissions();
  const [draftId, setDraftId] = useState<string | null>(null);
  const [manualSku, setManualSku] = useState("");
  const [thumbs, setThumbs] = useState<string[]>([]);
  /** Latest thumbs for background jobs (state in their closure would be stale). */
  const thumbsRef = useRef<string[]>([]);
  thumbsRef.current = thumbs;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [libraryNote, setLibraryNote] = useState("");
  const [photosBackup, setPhotosBackup] = useState<"unknown" | "on" | "off">("unknown");
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [cameraReady, setCameraReady] = useState(false);
  const [pictureSize, setPictureSize] = useState<string | undefined>();
  const [aspectId, setAspectId] = useState<CameraAspectId>("full");
  const [cameraLayout, setCameraLayout] = useState({ width: 0, height: 0 });
  const [sessionReady, setSessionReady] = useState(false);
  const [resumedNotice, setResumedNotice] = useState("");
  const [captureMode, setCaptureMode] = useState<"photo" | "video">("photo");
  /** Set after a recording with sound failed (e.g. no microphone access); next try is silent. */
  const [videoMuted, setVideoMuted] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordSecs, setRecordSecs] = useState(0);
  const recordTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Camera controls: zoom (0–1 = % of device max), torch, and tap-to-refocus.
  const [zoom, setZoom] = useState(0);
  const [torchOn, setTorchOn] = useState(false);
  const [autofocus, setAutofocus] = useState<FocusMode>("on");
  const [focusPoint, setFocusPoint] = useState<{ x: number; y: number } | null>(null);
  const zoomRef = useRef(0);
  const pinchStartDistRef = useRef<number | null>(null);
  const pinchStartZoomRef = useRef(0);
  const focusRingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refocusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftIdRef = useRef<string | null>(null);
  /** Queued/failed lot reopened here; it goes back in the queue when this screen closes. */
  const reopenedDraftRef = useRef<string | null>(null);
  const requestUploadRef = useRef(requestUpload);
  requestUploadRef.current = requestUpload;
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);

  const applyZoom = useCallback((next: number) => {
    const clamped = clamp01(next);
    zoomRef.current = clamped;
    setZoom(clamped);
  }, []);

  useFocusEffect(
    useCallback(() => {
      // Background sync progress used to re-render this screen (~1s) and restart CameraView.
      setSyncPaused(true);
      return () => setSyncPaused(false);
    }, [setSyncPaused])
  );

  function handleFocusTap(e: GestureResponderEvent) {
    const { locationX, locationY } = e.nativeEvent;
    setFocusPoint({ x: locationX, y: locationY });
    // expo-camera has no focus-point API; toggling autofocus off→on retriggers an AF pass.
    setAutofocus("off");
    if (refocusTimerRef.current) clearTimeout(refocusTimerRef.current);
    refocusTimerRef.current = setTimeout(() => setAutofocus("on"), 120);
    if (focusRingTimerRef.current) clearTimeout(focusRingTimerRef.current);
    focusRingTimerRef.current = setTimeout(() => setFocusPoint(null), 900);
  }

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: (e) => e.nativeEvent.touches.length >= 2,
      onPanResponderMove: (e) => {
        const touches = e.nativeEvent.touches;
        if (touches.length < 2) {
          pinchStartDistRef.current = null;
          return;
        }
        const dx = touches[0]!.pageX - touches[1]!.pageX;
        const dy = touches[0]!.pageY - touches[1]!.pageY;
        const dist = Math.hypot(dx, dy);
        if (dist <= 0) return;
        if (pinchStartDistRef.current == null) {
          pinchStartDistRef.current = dist;
          pinchStartZoomRef.current = zoomRef.current;
          return;
        }
        const scale = dist / pinchStartDistRef.current;
        applyZoom(pinchStartZoomRef.current + (scale - 1) * PINCH_ZOOM_SENSITIVITY);
      },
      onPanResponderRelease: (e, gesture) => {
        const wasPinch = pinchStartDistRef.current != null;
        pinchStartDistRef.current = null;
        if (!wasPinch && Math.abs(gesture.dx) < 8 && Math.abs(gesture.dy) < 8) {
          handleFocusTap(e);
        }
      },
      onPanResponderTerminate: () => {
        pinchStartDistRef.current = null;
      }
    })
  ).current;

  const applyDraft = useCallback(async (id: string, sku: string | null, showResume: boolean) => {
    draftIdRef.current = id;
    setDraftId(id);
    setManualSku(sku ?? "");
    const photos = await listDraftPhotos(id);
    const uris = photos.map((p) => p.file_uri);
    setThumbs(uris);
    if (showResume && uris.length) {
      setResumedNotice(
        `Resumed lot · ${uris.length} photo${uris.length === 1 ? "" : "s"} still on this phone`
      );
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setSessionReady(false);
    setResumedNotice("");

    /** Resume this exact lot. A queued/failed lot goes back to draft so new shots join it. */
    async function resumable(id: string | null): Promise<LotDraft | null> {
      const draft = id ? await getLotDraft(id) : null;
      if (!draft || draft.archived_at) return null;
      if ((draft.append_lot_id || null) !== (appendLotId || null)) return null;
      const decision = reopenForCapture(draft.status);
      if (decision === "blocked") return null;
      if (decision === "reopen") {
        await requeueDraft(draft.id, "draft");
        reopenedDraftRef.current = draft.id;
      }
      return draft;
    }

    async function hydrate() {
      try {
        let draft = await resumable(resumeDraftId);
        if (!draft && isAppendMode && appendLotId) {
          // Add photos sessions resume their own shots, never a new-lot draft.
          draft = await findOpenAppendDraft(appendLotId);
        } else if (!draft && selectedSaleId) {
          draft = await findOpenDraft(selectedSaleId, listingChannel);
        }
        if (cancelled) return;
        if (draft) {
          await applyDraft(draft.id, draft.manual_sku, true);
          const resumedId = draft.id;
          // Raw shots left by a killed session: finish them in the background.
          void processPendingCaptures(resumedId)
            .then((n) => {
              if (n > 0 && !cancelled && draftIdRef.current === resumedId) {
                return listDraftPhotos(resumedId).then((photos) =>
                  setThumbs(photos.map((p) => p.file_uri))
                );
              }
            })
            .catch(() => undefined);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not restore saved photos");
        }
      } finally {
        if (!cancelled) setSessionReady(true);
      }
    }

    void hydrate();
    return () => {
      cancelled = true;
    };
  }, [appendLotId, applyDraft, isAppendMode, listingChannel, resumeDraftId, selectedSaleId]);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      const prev = appStateRef.current;
      appStateRef.current = next;
      if (prev.match(/inactive|background/) && next === "active") {
        const id = draftIdRef.current;
        if (!id) return;
        void listDraftPhotos(id).then((photos) => {
          setThumbs(photos.map((p) => p.file_uri));
        });
      }
    });
    return () => sub.remove();
  }, []);

  useEffect(() => {
    return () => {
      const reopened = reopenedDraftRef.current;
      if (reopened) {
        // Closing the camera means "done adding": send the lot again (Finish/Discard already handled it).
        void (async () => {
          const draft = await getLotDraft(reopened);
          if (draft?.status === "draft" && draft.photo_count + (draft.video_count || 0) > 0) {
            await requeueDraft(reopened, "queued");
            requestUploadRef.current();
          }
        })().catch(() => undefined);
      }
      if (focusRingTimerRef.current) clearTimeout(focusRingTimerRef.current);
      if (refocusTimerRef.current) clearTimeout(refocusTimerRef.current);
      if (recordTimerRef.current) clearInterval(recordTimerRef.current);
      try {
        cameraRef.current?.stopRecording();
      } catch {
        /* ignore */
      }
    };
  }, []);

  async function ensureDraft(): Promise<string | null> {
    if (draftIdRef.current) return draftIdRef.current;
    if (isAppendMode && appendLotId) {
      // Shots for a website lot persist in SQLite like any lot; Sync uploads them to the lot.
      const open = await findOpenAppendDraft(appendLotId);
      if (open) {
        await applyDraft(open.id, null, open.photo_count + (open.video_count || 0) > 0);
        return open.id;
      }
      const id = await createAppendDraft({
        saleId: appendSaleId || selectedSaleId || "",
        saleName: appendSaleId && appendSaleId !== selectedSaleId ? null : selectedSaleName,
        lotId: appendLotId,
        label: appendSku || appendTitle
      });
      draftIdRef.current = id;
      setDraftId(id);
      return id;
    }
    if (!selectedSaleId) {
      setError(
        clientLogin
          ? "This login does not have a sale yet. Ask the account owner to assign one."
          : "Select a sale first."
      );
      return null;
    }
    // Prefer an existing unfinished draft so a remount / race doesn't orphan photos.
    const existing = await findOpenDraft(selectedSaleId, listingChannel);
    if (existing) {
      await applyDraft(
        existing.id,
        existing.manual_sku || manualSku,
        existing.photo_count + (existing.video_count || 0) > 0
      );
      return existing.id;
    }
    const id = await createLotDraft(selectedSaleId, selectedSaleName, manualSku, listingChannel);
    draftIdRef.current = id;
    setDraftId(id);
    return id;
  }

  useEffect(() => {
    void photoLibraryBackupAvailable().then((ok) => {
      setPhotosBackup(ok ? "on" : "off");
    });
  }, []);

  async function mirrorToPhotoLibrary(uri: string) {
    try {
      const result = await saveCaptureToPhotoLibrary(uri);
      if (result === "unavailable") {
        setPhotosBackup("off");
        setLibraryNote("");
        return;
      }
      if (result === "saved") {
        setPhotosBackup("on");
        setLibraryNote("");
        return;
      }
      setLibraryNote(librarySaveNote(result, Platform.OS === "android" ? "android" : "ios") ?? "");
    } catch {
      setLibraryNote(librarySaveNote("failed", Platform.OS === "android" ? "android" : "ios") ?? "");
    }
  }

  async function handleCameraReady() {
    setCameraReady(true);
    try {
      const sizes = await cameraRef.current?.getAvailablePictureSizesAsync();
      if (sizes?.length) {
        const largest = pickLargestPictureSize(sizes);
        if (largest) setPictureSize(largest);
      }
    } catch {
      /* device may not expose sizes; native default still used */
    }
  }

  function applyProcessedUri(rawUri: string, result: CaptureResult) {
    if (result.status !== "processed") return;
    setThumbs((prev) => prev.map((u) => (u === rawUri ? result.uri : u)));
    setPreviewUri((cur) => (cur === rawUri ? result.uri : cur));
  }

  function queueDraftProcessing(
    photoId: string,
    rawUri: string,
    ratio: number | null,
    size: { width?: number; height?: number }
  ) {
    void queueDraftPhotoProcessing(photoId, rawUri, ratio, size)
      .then(async (result) => {
        applyProcessedUri(rawUri, result);
        if (result.status !== "gone") await mirrorToPhotoLibrary(result.uri);
      })
      .catch(() => undefined);
  }

  async function snap() {
    if (!cameraRef.current || busy || !cameraReady || !sessionReady) return;
    const photoCount = thumbs.filter((u) => !isVideoUri(u)).length;
    const used = (isAppendMode ? appendExistingCount : 0) + photoCount;
    if (used >= CTBIDS_MAX_PHOTOS_PER_LOT) {
      const room = Math.max(0, CTBIDS_MAX_PHOTOS_PER_LOT - (isAppendMode ? appendExistingCount : 0));
      setError(
        room <= 0
          ? `This lot already has ${appendExistingCount} photos (CTBids limit ${CTBIDS_MAX_PHOTOS_PER_LOT}).`
          : `CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. You can add ${room} more.`
      );
      return;
    }
    setError("");
    setBusy(true);
    try {
      const photo = await cameraRef.current.takePictureAsync(MAX_DETAIL_CAPTURE_OPTIONS);
      if (!photo?.uri) {
        setError("Camera returned no image.");
        return;
      }
      const ratio = aspectPresetById(aspectId).ratio;
      const size = { width: photo.width, height: photo.height };
      // Durable first (move + SQLite row), then free the shutter. Orientation/crop
      // re-encode and the Photos copy run on the background queue.
      const id = await ensureDraft();
      if (!id) return;
      const raw = rawCaptureUri(`${FileSystem.documentDirectory}lot-captures/${id}/`, Date.now());
      await moveCaptureIntoPlace(photo.uri, raw);
      const photoId = await addPhotoToDraft(id, raw, "image", {
        processRatio: encodeProcessRatio(ratio)
      });
      setThumbs((prev) => (prev.includes(raw) ? prev : [...prev, raw]));
      setResumedNotice("");
      queueDraftProcessing(photoId, raw, ratio, size);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Capture failed");
    } finally {
      setBusy(false);
    }
  }

  function startRecordClock() {
    setRecordSecs(0);
    if (recordTimerRef.current) clearInterval(recordTimerRef.current);
    recordTimerRef.current = setInterval(() => setRecordSecs((s) => s + 1), 1000);
  }

  function stopRecordClock() {
    if (recordTimerRef.current) {
      clearInterval(recordTimerRef.current);
      recordTimerRef.current = null;
    }
  }

  async function saveCapturedVideo(uri: string) {
    const match = uri.toLowerCase().match(/\.(mp4|mov|m4v|webm)/);
    const ext = match?.[0] || (mediaKindFromName(uri) === "video" ? ".mp4" : ".mp4");
    const id = await ensureDraft();
    if (!id) return;
    const dir = `${FileSystem.documentDirectory}lot-captures/${id}/`;
    await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
    const dest = `${dir}${Date.now()}${ext}`;
    await FileSystem.copyAsync({ from: uri, to: dest });
    await addPhotoToDraft(id, dest, "video");
    setThumbs((prev) => (prev.includes(dest) ? prev : [...prev, dest]));
    setResumedNotice("");
    void captureQueue.enqueue(() => mirrorToPhotoLibrary(dest)).catch(() => undefined);
  }

  async function selectCaptureMode(mode: "photo" | "video") {
    if (recording) return;
    if (mode === "photo") {
      setCaptureMode("photo");
      setError((current) => (current === MICROPHONE_OFF_NOTE ? "" : current));
      return;
    }
    if (captureMode === "video") return;
    setCaptureMode("video");
    let granted = Boolean(micPermission?.granted);
    if (!granted) {
      try {
        granted = Boolean((await requestMicPermission()).granted);
      } catch {
        granted = false;
      }
    }
    const plan = microphoneRecordPlan(granted);
    setVideoMuted(plan.mute);
    if (plan.note) setError(plan.note);
  }

  async function recordVideo() {
    if (!cameraRef.current || busy || recording || !cameraReady || !sessionReady) return;
    if (thumbs.filter(isVideoUri).length >= MAX_VIDEOS_PER_LOT) {
      setError(`This lot can have at most ${MAX_VIDEOS_PER_LOT} videos.`);
      return;
    }
    // Android will not record audio until RECORD_AUDIO is granted. Ask here so the
    // first take matches the iPhone prompt instead of failing after the shutter.
    if (!videoMuted && !micPermission?.granted) {
      let granted = false;
      try {
        granted = Boolean((await requestMicPermission()).granted);
      } catch {
        granted = false;
      }
      const plan = microphoneRecordPlan(granted);
      if (plan.mute) {
        setVideoMuted(true);
        setError(plan.note ?? MICROPHONE_OFF_NOTE);
        return;
      }
    }
    setError("");
    setBusy(true);
    setRecording(true);
    startRecordClock();
    try {
      let clip: { uri?: string } | undefined;
      try {
        // 720p + bitrate cap (CameraView props; iOS needs the codec here to apply the bitrate)
        // and a file-size stop keep a 45s clip well under the website's upload limit.
        clip = await cameraRef.current.recordAsync(videoRecordOptions(Platform.OS));
      } catch (e) {
        if (!videoMuted) {
          setVideoMuted(true);
          setError("Could not record with sound — tap Record again to record without sound.");
          return;
        }
        throw e;
      }
      if (!clip?.uri) {
        setError("Camera returned no video.");
        return;
      }
      let size: number | null = null;
      try {
        const info = await FileSystem.getInfoAsync(clip.uri);
        size = info.exists && !info.isDirectory ? (info.size ?? null) : null;
      } catch {
        size = null;
      }
      const problem = videoSizeProblem(size);
      if (problem) {
        await FileSystem.deleteAsync(clip.uri, { idempotent: true }).catch(() => undefined);
        setError(problem);
        return;
      }
      await saveCapturedVideo(clip.uri);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Video capture failed");
    } finally {
      setRecording(false);
      stopRecordClock();
      setBusy(false);
    }
  }

  function stopVideo() {
    try {
      cameraRef.current?.stopRecording();
    } catch {
      /* already stopping */
    }
  }

  function openPreview(uri: string, index: number) {
    setPreviewIndex(index);
    setPreviewUri(uri);
  }

  async function reloadThumbs(id: string): Promise<string[]> {
    const photos = await listDraftPhotos(id);
    const uris = photos.map((p) => p.file_uri);
    setThumbs(uris);
    return uris;
  }

  async function removePhoto(uri: string) {
    if (!draftId) return;

    async function performDelete() {
      try {
        // The thumb can still show the raw path for a moment after SQLite moved to the
        // processed file; delete whichever row this shot has now.
        const processed = processedUriForRaw(uri);
        const removed =
          (await deletePhotoFromDraft(draftId!, uri)) ||
          (processed ? await deletePhotoFromDraft(draftId!, processed) : false);
        for (const target of removed && processed ? [uri, processed] : [uri]) {
          try {
            await FileSystem.deleteAsync(target, { idempotent: true });
          } catch {
            /* file may already be gone */
          }
        }
        const uris = await reloadThumbs(draftId!);
        if (previewUri === uri) {
          if (!uris.length) {
            setPreviewUri(null);
          } else {
            const nextIndex = Math.min(previewIndex, uris.length - 1);
            setPreviewIndex(nextIndex);
            setPreviewUri(uris[nextIndex] ?? null);
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not delete photo");
      }
    }

    const isClip = isVideoUri(uri);
    Alert.alert(
      isClip ? "Delete video?" : "Delete photo?",
      isClip ? "Remove this video from the lot?" : "Remove this photo from the lot?",
      [
        { text: "Cancel", style: "cancel" },
        { text: "Delete", style: "destructive", onPress: () => void performDelete() }
      ]
    );
  }

  function deleteFromStrip(uri: string, _index: number) {
    void removePhoto(uri);
  }

  function deleteFromPreview() {
    if (!previewUri) return;
    void removePhoto(previewUri);
  }

  function onSkuChange(text: string) {
    setManualSku(text);
    if (draftId) {
      void updateDraftManualSku(draftId, text);
    }
  }

  /** The camera X should land on Import / Start lot, not whichever tab was open before. */
  function leaveCamera() {
    if (isAppendMode || isFacebook) {
      router.back();
      return;
    }
    router.replace("/(tabs)/capture");
  }

  function confirmFinish() {
    if (isAppendMode) {
      if (!thumbs.length) {
        Alert.alert("Nothing to add", "Snap a photo or record a video to add to this lot.");
        return;
      }
      const { images, videos } = splitMediaUris(thumbs);
      const label = appendSku || appendTitle || "this lot";
      const parts = [
        images.length ? `${images.length} photo${images.length === 1 ? "" : "s"}` : "",
        videos.length ? `${videos.length} video${videos.length === 1 ? "" : "s"}` : ""
      ].filter(Boolean);
      Alert.alert(
        "Add to lot?",
        `${parts.join(" · ")} will be uploaded to ${label}. Photos attach to the listing. Videos go to the website, then to CT Seller when the lot is posted.`,
        [
          { text: "Keep editing", style: "cancel" },
          { text: "Upload", style: "default", onPress: () => void finishAppend() }
        ]
      );
      return;
    }
    if (!draftId) {
      leaveCamera();
      return;
    }
    const pending = splitMediaUris(thumbs);
    if (!pending.images.length) {
      Alert.alert(
        "Need a photo",
        pending.videos.length
          ? "Your video is already saved on this lot. Snap at least one still photo, then tap Finish lot. The clip uploads after the listing is on the website."
          : "AI catalog needs at least one still photo. Videos attach after the lot is on the website."
      );
      return;
    }
    const skuLabel = isFacebook
      ? manualSku.trim()
        ? `note “${manualSku.trim()}”`
        : "no price note"
      : manualSku.trim()
        ? `SKU ${manualSku.trim().toUpperCase()}`
        : "no manual SKU";
    const saleLabel = selectedSaleName ? `"${selectedSaleName}"` : "this sale";
    Alert.alert(
      isFacebook ? "Send Facebook item to pipeline?" : "Send lot to pipeline?",
      `${pending.images.length} photo${pending.images.length === 1 ? "" : "s"}${
        pending.videos.length
          ? ` · ${pending.videos.length} video${pending.videos.length === 1 ? "" : "s"}`
          : ""
      } · ${skuLabel}\n\nThis uploads to ${saleLabel} as ${
        isFacebook ? "Facebook Marketplace" : "CT Bids"
      } inventory and starts AI processing.`,
      [
        { text: "Keep editing", style: "cancel" },
        { text: "Send", style: "default", onPress: () => void finishLot() }
      ]
    );
  }

  async function finishAppend() {
    if (!isAppendMode || !draftId || !thumbs.length) return;
    setBusy(true);
    setError("");
    try {
      // Let orientation/crop finish so Sync uploads the final files (it also re-checks).
      await captureQueue.idle();
      await requeueDraft(draftId, "queued");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not queue these shots");
      return;
    } finally {
      setBusy(false);
    }
    requestUpload();
    const label = appendSku || appendTitle || "this lot";
    Alert.alert(
      "Queued for upload",
      `${thumbs.length} file${thumbs.length === 1 ? "" : "s"} will be added to ${label}. They stay on this phone (Queue tab) until the website has them, and upload on their own once the connection is steady.`,
      [{ text: "OK", onPress: () => router.back() }]
    );
  }

  /** Cancel in Add photos mode discards the new shots — ask first, they are not on the website. */
  function cancelAppend() {
    const id = draftIdRef.current;
    if (!id) {
      router.back();
      return;
    }
    if (!thumbs.length) {
      void deleteLotDraft(id).finally(() => router.back());
      return;
    }
    const { images, videos } = splitMediaUris(thumbs);
    const parts = [
      images.length ? `${images.length} photo${images.length === 1 ? "" : "s"}` : "",
      videos.length ? `${videos.length} video${videos.length === 1 ? "" : "s"}` : ""
    ].filter(Boolean);
    Alert.alert(
      "Discard new shots?",
      `${parts.join(" · ")} will be deleted from this phone. They have not been added to the lot.`,
      [
        { text: "Keep shooting", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: () => {
            void (async () => {
              await captureQueue.idle().catch(() => undefined);
              await deleteLotDraft(id);
              draftIdRef.current = null;
              setThumbs([]);
              router.back();
            })();
          }
        }
      ]
    );
  }

  async function finishLot() {
    if (!draftId || !thumbs.length) return;
    setBusy(true);
    try {
      // Let orientation/crop finish so Sync uploads the final files (it also re-checks).
      await captureQueue.idle();
    } finally {
      setBusy(false);
    }
    await updateDraftManualSku(draftId, manualSku);
    await requeueDraft(draftId, "queued");
    requestUpload();
    Alert.alert(
      "Lot queued",
      `${thumbs.length} file${thumbs.length === 1 ? "" : "s"} queued for ${selectedSaleName ? `"${selectedSaleName}"` : "this sale"}. Saved on this phone — it uploads on its own once the connection is steady, even if you are offline now.\n\nPhotos catalog first; videos attach after the lot is on the website.`,
      [{ text: "OK", onPress: () => router.back() }]
    );
  }

  if (!permission) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.text}>Camera permission is required for lot capture.</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Allow camera access"
          style={styles.primary}
          onPress={() => void requestPermission()}
        >
          <Text style={styles.primaryText}>Allow camera</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View
      style={[
        styles.root,
        landscape && styles.rootLandscape,
        landscape
          ? {
              // Notch sits on the sides in landscape — don't pad the top (that black strip).
              paddingLeft: Math.max(insets.left, 0),
              paddingRight: 0,
              paddingTop: 0,
              // Android's 3-button / gesture bar sits on the bottom in landscape too.
              paddingBottom: Platform.OS === "android" ? insets.bottom : 0
            }
          : {
              paddingTop: insets.top,
              paddingBottom: 0
            }
      ]}
    >
      <View
        style={[
          styles.cameraWrap,
          landscape && styles.cameraWrapLandscape
        ]}
        onLayout={(e) => {
          const { width, height } = e.nativeEvent.layout;
          setCameraLayout({ width, height });
        }}
      >
        <CameraView
          ref={cameraRef}
          style={StyleSheet.absoluteFill}
          facing="back"
          mode={captureMode === "video" ? "video" : "picture"}
          videoQuality={VIDEO_QUALITY}
          videoBitrate={VIDEO_BITRATE}
          mute={videoMuted}
          zoom={zoom}
          enableTorch={torchOn}
          autofocus={autofocus}
          pictureSize={pictureSize}
          onCameraReady={() => void handleCameraReady()}
        />
        {recording ? (
          <View pointerEvents="none" style={styles.recBadge}>
            <Text style={styles.recBadgeText}>
              REC {String(Math.floor(recordSecs / 60)).padStart(2, "0")}:
              {String(recordSecs % 60).padStart(2, "0")}
            </Text>
          </View>
        ) : null}
        <View style={StyleSheet.absoluteFill} {...panResponder.panHandlers} />
        {aspectId !== "full" && cameraLayout.width > 0 && cameraLayout.height > 0 ? (
          <AspectMask
            layout={cameraLayout}
            ratio={aspectPresetById(aspectId).ratio}
          />
        ) : null}
        {focusPoint ? (
          <View
            pointerEvents="none"
            style={[styles.focusRing, { left: focusPoint.x - 36, top: focusPoint.y - 36 }]}
          />
        ) : null}
        <Pressable
          accessibilityRole="button"
          style={[
            styles.closeFab,
            { top: landscape ? 10 : Math.max(10, 4), left: landscape ? 10 : 12 }
          ]}
          onPress={() => leaveCamera()}
          hitSlop={10}
          accessibilityLabel="Close capture"
        >
          <Text style={styles.closeFabText}>✕</Text>
        </Pressable>
        <View style={[styles.cameraControls, landscape && styles.cameraControlsLandscape]}>
          <View style={styles.zoomTorchRow}>
            <View style={styles.zoomRow}>
              {ZOOM_PRESETS.map((preset) => {
                const active =
                  Math.abs(zoom - preset.value) < (preset.value === 0 ? 0.02 : 0.08);
                return (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Zoom ${preset.label}`}
                    accessibilityState={{ selected: active }}
                    hitSlop={hitSlopFor(30)}
                    key={preset.label}
                    style={[styles.zoomBtn, active && styles.zoomBtnActive]}
                    onPress={() => applyZoom(preset.value)}
                  >
                    <Text style={[styles.zoomBtnText, active && styles.zoomBtnTextActive]}>
                      {preset.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ selected: torchOn }}
              hitSlop={hitSlopFor(32)}
              style={[styles.torchBtn, torchOn && styles.torchBtnActive]}
              onPress={() => setTorchOn((v) => !v)}
              accessibilityLabel={torchOn ? "Turn torch off" : "Turn torch on"}
            >
              <Text style={[styles.torchBtnText, torchOn && styles.torchBtnTextActive]}>Torch</Text>
            </Pressable>
          </View>
        </View>
      </View>

      <View
        style={[
          styles.panel,
          landscape && styles.panelLandscape,
          landscape && { paddingRight: Math.max(insets.right, 8) }
        ]}
      >
        <View
          style={[
            styles.panelControls,
            landscape && styles.panelControlsLandscape,
            !landscape && { paddingBottom: 6 }
          ]}
        >
          <View style={styles.aspectRow}>
            {CAMERA_ASPECT_PRESETS.map((preset) => {
              const active = aspectId === preset.id;
              return (
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  hitSlop={hitSlopFor(32)}
                  key={preset.id}
                  style={[styles.aspectBtn, active && styles.aspectBtnActive]}
                  onPress={() => setAspectId(preset.id)}
                  accessibilityLabel={`Aspect ratio ${preset.label}`}
                >
                  <Text style={[styles.aspectBtnText, active && styles.aspectBtnTextActive]}>
                    {preset.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          <View style={[styles.sessionMeta, landscape && styles.sessionMetaLandscape]}>
            <Text style={[styles.sessionMetaLabel, landscape && styles.sessionMetaLabelLandscape]}>
              {isAppendMode
                ? thumbs.length
                  ? `${thumbs.filter((u) => !isVideoUri(u)).length} photo${
                      thumbs.filter((u) => !isVideoUri(u)).length === 1 ? "" : "s"
                    }${
                      thumbs.some(isVideoUri)
                        ? ` · ${thumbs.filter(isVideoUri).length} video${
                            thumbs.filter(isVideoUri).length === 1 ? "" : "s"
                          }`
                        : ""
                    }`
                  : "Add photos or video"
                : isFacebook
                  ? thumbs.length
                    ? `Facebook · ${thumbs.length}`
                    : "Facebook"
                  : thumbs.length
                    ? `${thumbs.filter((u) => !isVideoUri(u)).length} photo${
                        thumbs.filter((u) => !isVideoUri(u)).length === 1 ? "" : "s"
                      }${
                        thumbs.some(isVideoUri)
                          ? ` · ${thumbs.filter(isVideoUri).length} video`
                          : ""
                      }`
                    : "New lot"}
            </Text>
            {resumedNotice ? (
              <Text style={styles.sessionMetaResumed} numberOfLines={2}>
                {resumedNotice}
              </Text>
            ) : null}
            {isAppendMode && (appendSku || appendTitle) ? (
              <Text style={styles.sessionMetaSale} numberOfLines={2}>
                {[appendSku, appendTitle].filter(Boolean).join(" · ")}
              </Text>
            ) : selectedSaleName ? (
              <Text style={styles.sessionMetaSale} numberOfLines={1}>
                {selectedSaleName}
              </Text>
            ) : null}
          </View>

          {captureModes.length > 1 ? (
            <View style={styles.modeRow}>
              {captureModes.map((mode) => {
                const active = captureMode === mode;
                return (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={mode === "photo" ? "Photo mode" : "Video mode"}
                    accessibilityState={{ selected: active, disabled: recording }}
                    hitSlop={hitSlopFor(34)}
                    key={mode}
                    style={[styles.modeBtn, active && styles.modeBtnActive]}
                    disabled={recording}
                    onPress={() => void selectCaptureMode(mode)}
                  >
                    <Text style={[styles.modeBtnText, active && styles.modeBtnTextActive]}>
                      {mode === "photo" ? "Photo" : "Video"}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          ) : null}

          <View style={[styles.actions, landscape && styles.actionsLandscape]}>
            {!landscape ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={isAppendMode ? "Cancel and discard these new shots" : "Close camera"}
                style={styles.secondary}
                onPress={() => {
                  if (isAppendMode) {
                    cancelAppend();
                    return;
                  }
                  leaveCamera();
                }}
              >
                <Text style={styles.secondaryText}>Cancel</Text>
              </Pressable>
            ) : null}
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={captureMode === "video" ? (recording ? "Stop recording" : "Record video") : "Take photo"}
              accessibilityState={{ disabled: !recording && (busy || !cameraReady || !sessionReady) }}
              style={[
                styles.snap,
                landscape && styles.snapLandscape,
                recording && styles.snapRecording,
                (!recording && (busy || !cameraReady || !sessionReady)) && styles.disabled
              ]}
              disabled={!recording && (busy || !cameraReady || !sessionReady)}
              onPress={() => {
                if (captureMode === "video") {
                  if (recording) stopVideo();
                  else void recordVideo();
                  return;
                }
                void snap();
              }}
            >
              {busy && !recording ? (
                <ActivityIndicator color={theme.onPrimary} />
              ) : (
                <Text style={[styles.primaryText, recording && styles.primaryTextOnDanger]}>
                  {captureMode === "video"
                    ? recording
                      ? "Stop"
                      : `Record (${MAX_VIDEO_DURATION_SEC}s)`
                    : "Snap"}
                </Text>
              )}
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={isAppendMode ? "Upload to lot" : "Finish lot"}
              accessibilityState={{ disabled: !thumbs.length || busy }}
              style={[
                styles.primary,
                landscape && styles.primaryLandscape,
                (!thumbs.length || busy) && styles.disabled
              ]}
              disabled={!thumbs.length || busy}
              onPress={confirmFinish}
            >
              <Text style={styles.primaryText}>
                {isAppendMode ? "Upload" : landscape ? "Finish" : "Finish lot"}
              </Text>
            </Pressable>
          </View>

          {!isAppendMode ? (
            <TextInput
              placeholder={isFacebook ? "Asking price note (optional)" : "Manual SKU (optional)"}
              placeholderTextColor={theme.textSubtle}
              style={[styles.input, landscape && styles.inputLandscape]}
              value={manualSku}
              autoCapitalize={isFacebook ? "sentences" : "characters"}
              autoCorrect={false}
              onChangeText={onSkuChange}
            />
          ) : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {libraryNote ? (
            <Text style={styles.libraryNote}>{libraryNote}</Text>
          ) : photosBackup === "on" ? (
            <Text style={styles.libraryHint}>
              {Platform.OS === "android"
                ? "A copy of each shot is also saved in Gallery."
                : "A copy of each shot is also saved in the Photos app."}
            </Text>
          ) : null}
        </View>

        <ScrollView
          style={styles.panelThumbs}
          contentContainerStyle={[
            styles.panelThumbsContent,
            !landscape && { paddingBottom: Math.max(insets.bottom, 14) }
          ]}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {!landscape ? (
            <Text style={styles.sectionTitle}>{isAppendMode ? "New media" : "Photos & videos"}</Text>
          ) : (
            <Text style={styles.sectionHintLandscape}>{isAppendMode ? "New media" : "Photos & videos"}</Text>
          )}
          <DraftPhotoStrip
            uris={thumbs}
            size="md"
            onPressPhoto={openPreview}
            onDeletePhoto={deleteFromStrip}
          />
        </ScrollView>
      </View>

      <PhotoPreviewModal
        uris={previewUri ? thumbs : []}
        index={previewIndex}
        onIndexChange={(next) => {
          setPreviewIndex(next);
          setPreviewUri(thumbs[next] ?? null);
        }}
        onClose={() => setPreviewUri(null)}
        onDelete={deleteFromPreview}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.black },
  rootLandscape: { flexDirection: "row", alignItems: "stretch" },
  cameraWrap: { flex: 1, minHeight: 320, position: "relative" },
  cameraWrapLandscape: { flex: 1, minHeight: 0, minWidth: 0 },
  closeFab: {
    position: "absolute",
    zIndex: 5,
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "rgba(14, 14, 16, 0.75)",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "rgba(168, 162, 158, 0.35)"
  },
  closeFabText: { color: theme.text, fontSize: 16, fontWeight: "700", lineHeight: 18 },
  cameraControls: {
    position: "absolute",
    left: 10,
    right: 10,
    bottom: 10
  },
  cameraControlsLandscape: {
    bottom: 10,
    left: 10,
    right: 10
  },
  aspectRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: theme.surface,
    borderRadius: 10,
    paddingHorizontal: 4,
    paddingVertical: 3,
    borderWidth: 1,
    borderColor: theme.border
  },
  aspectBtn: {
    flex: 1,
    minWidth: 0,
    paddingVertical: 8,
    paddingHorizontal: 6,
    borderRadius: 8,
    alignItems: "center",
    backgroundColor: "transparent"
  },
  aspectBtnActive: { backgroundColor: theme.primary },
  aspectBtnText: { color: theme.textSoft, fontSize: 12, fontWeight: "700" },
  aspectBtnTextActive: { color: theme.onPrimary },
  zoomTorchRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  zoomRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: "rgba(14, 14, 16, 0.78)",
    borderRadius: 999,
    paddingHorizontal: 4,
    paddingVertical: 3
  },
  zoomBtn: {
    minWidth: 40,
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 999,
    alignItems: "center",
    backgroundColor: "transparent"
  },
  zoomBtnActive: { backgroundColor: theme.primary },
  zoomBtnText: { color: theme.textSoft, fontSize: 12, fontWeight: "700" },
  zoomBtnTextActive: { color: theme.onPrimary },
  torchBtn: {
    backgroundColor: "rgba(14, 14, 16, 0.78)",
    borderRadius: 999,
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: "rgba(168, 162, 158, 0.35)"
  },
  torchBtnActive: { backgroundColor: theme.warning, borderColor: theme.warning },
  torchBtnText: { color: theme.textSoft, fontSize: 12, fontWeight: "700" },
  torchBtnTextActive: { color: theme.warningText },
  maskBar: {
    position: "absolute",
    backgroundColor: "rgba(0,0,0,0.45)"
  },
  maskFrame: {
    position: "absolute",
    borderWidth: 1.5,
    borderColor: "rgba(255,255,255,0.85)"
  },
  focusRing: {
    position: "absolute",
    width: 72,
    height: 72,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: theme.warning,
    shadowColor: theme.black,
    shadowOpacity: 0.4,
    shadowRadius: 2,
    zIndex: 3
  },
  panel: {
    flexGrow: 0,
    flexShrink: 0,
    maxHeight: "40%",
    backgroundColor: theme.bgElevated,
    borderTopWidth: 1,
    borderTopColor: theme.border
  },
  panelLandscape: {
    flexGrow: 0,
    flexShrink: 0,
    flexBasis: 220,
    width: 220,
    maxWidth: 220,
    maxHeight: "100%",
    borderTopWidth: 0,
    borderLeftWidth: 1,
    borderLeftColor: theme.border,
    alignSelf: "stretch"
  },
  panelControls: {
    paddingHorizontal: 12,
    paddingTop: 8,
    gap: 6
  },
  panelControlsLandscape: {
    paddingTop: 10,
    paddingLeft: 10,
    paddingRight: 6
  },
  panelThumbs: {
    flexGrow: 1,
    flexShrink: 1,
    minHeight: 72
  },
  panelThumbsContent: {
    paddingHorizontal: 12,
    paddingTop: 4,
    paddingBottom: 8,
    gap: 6
  },
  sessionMeta: {
    gap: 2,
    paddingBottom: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.border
  },
  sessionMetaLandscape: {
    marginBottom: 0,
    paddingBottom: 4
  },
  sessionMetaLabel: { color: theme.text, fontSize: 13, fontWeight: "600" },
  sessionMetaLabelLandscape: { fontSize: 13 },
  sessionMetaResumed: { color: theme.success, fontSize: 12, fontWeight: "600" },
  sessionMetaSale: { color: theme.primaryHover, fontSize: 12, fontWeight: "600" },
  sectionTitle: { color: theme.text, fontSize: 14, fontWeight: "700" },
  sectionHint: { color: theme.textMuted, fontSize: 12, marginTop: -4 },
  sectionHintLandscape: { color: theme.textMuted, fontSize: 11, fontWeight: "600" },
  input: {
    borderWidth: 1,
    borderColor: theme.borderStrong,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 9,
    color: theme.text
  },
  inputLandscape: {
    paddingVertical: 8,
    paddingHorizontal: 10,
    fontSize: 14
  },
  actions: { flexDirection: "row", gap: 8, alignItems: "center", marginTop: 2 },
  actionsLandscape: { flexDirection: "column", gap: 6 },
  modeRow: {
    flexDirection: "row",
    gap: 6,
    backgroundColor: theme.surface,
    borderRadius: 10,
    padding: 3,
    borderWidth: 1,
    borderColor: theme.border
  },
  modeBtn: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: 8,
    alignItems: "center"
  },
  modeBtnActive: { backgroundColor: theme.primary },
  modeBtnText: { color: theme.textSoft, fontSize: 13, fontWeight: "700" },
  modeBtnTextActive: { color: theme.onPrimary },
  recBadge: {
    position: "absolute",
    top: 12,
    alignSelf: "center",
    backgroundColor: "rgba(185, 28, 28, 0.92)",
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 999,
    zIndex: 6
  },
  recBadgeText: { color: theme.text, fontSize: 13, fontWeight: "800", letterSpacing: 0.4 },
  snapRecording: { backgroundColor: theme.errorBtn },
  snap: {
    flex: 1,
    backgroundColor: theme.primary,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    minHeight: 44,
    justifyContent: "center",
    minWidth: 72
  },
  snapLandscape: {
    flex: 0,
    width: "100%",
    minWidth: 0,
    paddingVertical: 12,
    minHeight: 44
  },
  primary: {
    flex: 1,
    backgroundColor: theme.borderStrong,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    minWidth: 72
  },
  primaryLandscape: {
    flex: 0,
    width: "100%",
    minWidth: 0,
    paddingVertical: 12
  },
  secondary: { paddingVertical: 12, paddingHorizontal: 10 },
  secondaryText: { color: theme.textMuted },
  primaryText: { color: theme.onPrimary, fontWeight: "600" },
  primaryTextOnDanger: { color: theme.text },
  disabled: { opacity: 0.45 },
  error: { color: theme.error, fontSize: 13 },
  libraryHint: { color: theme.textMuted, fontSize: 12, textAlign: "center" },
  libraryNote: { color: theme.warning, fontSize: 12, textAlign: "center" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  text: { color: theme.textSoft, textAlign: "center" }
});
