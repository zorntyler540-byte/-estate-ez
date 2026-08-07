import { CameraView, useCameraPermissions, type FocusMode } from "expo-camera";
import * as FileSystem from "expo-file-system/legacy";
import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  PanResponder,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
  type AppStateStatus,
  type GestureResponderEvent
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { DraftPhotoStrip, PhotoPreviewModal } from "@/components/DraftPhotos";
import { useAuth } from "@/context/AuthContext";
import { useSync } from "@/context/SyncContext";
import {
  CAMERA_ASPECT_PRESETS,
  aspectPresetById,
  centeredAspectRect,
  maybeCropToAspect,
  type CameraAspectId
} from "@/lib/cameraAspect";
import { uploadLotPhotos } from "@/lib/api";
import { MAX_DETAIL_CAPTURE_OPTIONS, pickLargestPictureSize } from "@/lib/cameraCapture";
import { CTBIDS_MAX_PHOTOS_PER_LOT } from "@/lib/catalogLimits";
import {
  addPhotoToDraft,
  createLotDraft,
  deletePhotoFromDraft,
  findOpenDraft,
  getLotDraft,
  listDraftPhotos,
  setDraftStatus,
  updateDraftManualSku
} from "@/lib/queue/db";

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
  }>();
  const listingChannel = params.channel === "facebook" ? "facebook" : "ctseller";
  const isFacebook = listingChannel === "facebook";
  const resumeDraftId = paramString(params.draftId);
  const appendLotId = paramString(params.appendLotId);
  const appendSku = paramString(params.appendSku);
  const appendTitle = paramString(params.appendTitle);
  const appendExistingCount = Math.max(
    0,
    Number.parseInt(paramString(params.appendExistingCount) || "0", 10) || 0
  );
  const isAppendMode = Boolean(appendLotId);
  const { token, selectedSaleId, selectedSaleName } = useAuth();
  const { syncNow } = useSync();
  const { width: windowWidth, height: windowHeight } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const landscape = windowWidth > windowHeight;
  const cameraRef = useRef<CameraView>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [draftId, setDraftId] = useState<string | null>(null);
  const [manualSku, setManualSku] = useState("");
  const [thumbs, setThumbs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [cameraReady, setCameraReady] = useState(false);
  const [pictureSize, setPictureSize] = useState<string | undefined>();
  const [aspectId, setAspectId] = useState<CameraAspectId>("full");
  const [cameraLayout, setCameraLayout] = useState({ width: 0, height: 0 });
  const [sessionReady, setSessionReady] = useState(false);
  const [resumedNotice, setResumedNotice] = useState("");

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
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);

  const applyZoom = useCallback((next: number) => {
    const clamped = clamp01(next);
    zoomRef.current = clamped;
    setZoom(clamped);
  }, []);

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

    async function hydrate() {
      // Append-to-lot mode starts empty — do not pull an unfinished new-lot draft.
      if (isAppendMode) {
        if (!cancelled) setSessionReady(true);
        return;
      }
      try {
        let draft = resumeDraftId ? await getLotDraft(resumeDraftId) : null;
        if (draft && draft.status !== "draft") {
          draft = null;
        }
        if (!draft && selectedSaleId) {
          draft = await findOpenDraft(selectedSaleId, listingChannel);
        }
        if (cancelled) return;
        if (draft) {
          await applyDraft(draft.id, draft.manual_sku, true);
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
  }, [applyDraft, isAppendMode, listingChannel, resumeDraftId, selectedSaleId]);

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
      if (focusRingTimerRef.current) clearTimeout(focusRingTimerRef.current);
      if (refocusTimerRef.current) clearTimeout(refocusTimerRef.current);
    };
  }, []);

  async function ensureDraft(): Promise<string | null> {
    if (draftIdRef.current) return draftIdRef.current;
    if (!selectedSaleId) {
      setError("Select a sale first.");
      return null;
    }
    // Prefer an existing unfinished draft so a remount / race doesn't orphan photos.
    const existing = await findOpenDraft(selectedSaleId, listingChannel);
    if (existing) {
      await applyDraft(existing.id, existing.manual_sku || manualSku, existing.photo_count > 0);
      return existing.id;
    }
    const id = await createLotDraft(selectedSaleId, selectedSaleName, manualSku, listingChannel);
    draftIdRef.current = id;
    setDraftId(id);
    return id;
  }

  function appendDir(): string {
    return `${FileSystem.documentDirectory}lot-append/${appendLotId}/`;
  }

  async function cleanupAppendFiles(uris: string[]) {
    for (const uri of uris) {
      try {
        await FileSystem.deleteAsync(uri, { idempotent: true });
      } catch {
        /* already gone */
      }
    }
    if (appendLotId) {
      try {
        await FileSystem.deleteAsync(appendDir(), { idempotent: true });
      } catch {
        /* ignore */
      }
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

  async function snap() {
    if (!cameraRef.current || busy || !cameraReady || !sessionReady) return;
    const used = (isAppendMode ? appendExistingCount : 0) + thumbs.length;
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
      const croppedUri = await maybeCropToAspect(photo.uri, photo.width, photo.height, ratio);
      if (isAppendMode && appendLotId) {
        const dir = appendDir();
        await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
        const dest = `${dir}${Date.now()}.jpg`;
        await FileSystem.copyAsync({ from: croppedUri, to: dest });
        setThumbs((prev) => [...prev, dest]);
        return;
      }
      const id = await ensureDraft();
      if (!id) return;
      const dir = `${FileSystem.documentDirectory}lot-captures/${id}/`;
      await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
      const dest = `${dir}${Date.now()}.jpg`;
      await FileSystem.copyAsync({ from: croppedUri, to: dest });
      await addPhotoToDraft(id, dest);
      const photos = await listDraftPhotos(id);
      const uris = photos.map((p) => p.file_uri);
      setThumbs(uris);
      setResumedNotice("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Capture failed");
    } finally {
      setBusy(false);
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

  async function removePhoto(uri: string, fromPreview: boolean) {
    if (!isAppendMode && !draftId) return;

    async function performDelete() {
      try {
        if (isAppendMode) {
          try {
            await FileSystem.deleteAsync(uri, { idempotent: true });
          } catch {
            /* file may already be gone */
          }
          setThumbs((prev) => {
            const uris = prev.filter((u) => u !== uri);
            if (previewUri === uri) {
              if (!uris.length) {
                setPreviewUri(null);
              } else {
                const nextIndex = Math.min(previewIndex, uris.length - 1);
                setPreviewIndex(nextIndex);
                setPreviewUri(uris[nextIndex] ?? null);
              }
            }
            return uris;
          });
          return;
        }
        await deletePhotoFromDraft(draftId!, uri);
        try {
          await FileSystem.deleteAsync(uri, { idempotent: true });
        } catch {
          /* file may already be gone */
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

    if (fromPreview) {
      Alert.alert("Delete photo?", "Remove this photo from the lot?", [
        { text: "Cancel", style: "cancel" },
        { text: "Delete", style: "destructive", onPress: () => void performDelete() }
      ]);
    } else {
      await performDelete();
    }
  }

  function deleteFromStrip(uri: string, _index: number) {
    void removePhoto(uri, false);
  }

  function deleteFromPreview() {
    if (!previewUri) return;
    void removePhoto(previewUri, true);
  }

  function onSkuChange(text: string) {
    setManualSku(text);
    if (draftId) {
      void updateDraftManualSku(draftId, text);
    }
  }

  function confirmFinish() {
    if (isAppendMode) {
      if (!thumbs.length) {
        Alert.alert("No photos", "Snap at least one photo to add to this lot.");
        return;
      }
      const label = appendSku || appendTitle || "this lot";
      Alert.alert(
        "Add photos to lot?",
        `${thumbs.length} new photo${thumbs.length === 1 ? "" : "s"} will be uploaded to ${label}. They attach to the existing listing (no new AI job).`,
        [
          { text: "Keep editing", style: "cancel" },
          { text: "Upload", style: "default", onPress: () => void finishAppend() }
        ]
      );
      return;
    }
    if (!draftId) {
      router.back();
      return;
    }
    if (!thumbs.length) {
      Alert.alert("No photos", "Snap at least one photo before finishing this lot.");
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
      `${thumbs.length} photo${thumbs.length === 1 ? "" : "s"} · ${skuLabel}\n\nThis uploads to ${saleLabel} as ${
        isFacebook ? "Facebook Marketplace" : "CT Bids"
      } inventory and starts AI processing.`,
      [
        { text: "Keep editing", style: "cancel" },
        { text: "Send", style: "default", onPress: () => void finishLot() }
      ]
    );
  }

  async function finishAppend() {
    if (!isAppendMode || !appendLotId || !thumbs.length) return;
    if (!token) {
      setError("Sign in again to upload photos.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await uploadLotPhotos(
        token,
        appendLotId,
        thumbs.map((uri, i) => ({
          uri,
          name: `append-${String(i + 1).padStart(2, "0")}.jpg`
        }))
      );
      const uploaded = thumbs.slice();
      setThumbs([]);
      await cleanupAppendFiles(uploaded);
      Alert.alert(
        "Photos added",
        `${uploaded.length} photo${uploaded.length === 1 ? "" : "s"} added to the listing.`,
        [{ text: "OK", onPress: () => router.back() }]
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not upload photos");
    } finally {
      setBusy(false);
    }
  }

  async function finishLot() {
    if (!draftId || !thumbs.length) return;
    await updateDraftManualSku(draftId, manualSku);
    await setDraftStatus(draftId, "queued", null);
    void syncNow();
    Alert.alert(
      "Lot queued",
      `${thumbs.length} photo${thumbs.length === 1 ? "" : "s"} queued for ${selectedSaleName ? `"${selectedSaleName}"` : "this sale"}.\n\nThey'll upload automatically when you're online. Review the listing on the web dashboard after processing.`,
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
        <Pressable style={styles.primary} onPress={() => void requestPermission()}>
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
              paddingBottom: 0
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
          zoom={zoom}
          enableTorch={torchOn}
          autofocus={autofocus}
          pictureSize={pictureSize}
          onCameraReady={() => void handleCameraReady()}
        />
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
          style={[
            styles.closeFab,
            { top: landscape ? 10 : Math.max(10, 4), left: landscape ? 10 : 12 }
          ]}
          onPress={() => router.back()}
          hitSlop={10}
          accessibilityLabel="Close capture"
        >
          <Text style={styles.closeFabText}>✕</Text>
        </Pressable>
        <View style={[styles.cameraControls, landscape && styles.cameraControlsLandscape]}>
          <View style={styles.controlsCol}>
            <View style={styles.aspectRow}>
              {CAMERA_ASPECT_PRESETS.map((preset) => {
                const active = aspectId === preset.id;
                return (
                  <Pressable
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
            <View style={styles.zoomTorchRow}>
              <View style={styles.zoomRow}>
                {ZOOM_PRESETS.map((preset) => {
                  const active =
                    Math.abs(zoom - preset.value) <
                    (preset.value === 0 ? 0.02 : 0.08);
                  return (
                    <Pressable
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
                {zoom > 0.02 ? (
                  <Text style={styles.zoomReadout}>{Math.round(zoom * 100)}%</Text>
                ) : null}
              </View>
              <Pressable
                style={[styles.torchBtn, torchOn && styles.torchBtnActive]}
                onPress={() => setTorchOn((v) => !v)}
              >
                <Text style={[styles.torchBtnText, torchOn && styles.torchBtnTextActive]}>
                  {torchOn ? "Torch on" : "Torch off"}
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
        {!landscape ? (
          <Text style={styles.cameraHint} pointerEvents="none">
            Pinch to zoom · tap to refocus · aspect crops on snap
          </Text>
        ) : null}
      </View>

      <ScrollView
        style={[
          styles.panel,
          landscape && styles.panelLandscape,
          landscape && { paddingRight: Math.max(insets.right, 8) }
        ]}
        contentContainerStyle={[
          styles.panelContent,
          landscape && styles.panelContentLandscape,
          !landscape && { paddingBottom: Math.max(insets.bottom, 14) }
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={[styles.sessionMeta, landscape && styles.sessionMetaLandscape]}>
          <Text style={[styles.sessionMetaLabel, landscape && styles.sessionMetaLabelLandscape]}>
            {isAppendMode
              ? thumbs.length
                ? `Add to lot · ${thumbs.length} new photo${thumbs.length === 1 ? "" : "s"}`
                : "Add photos · tap Snap"
              : isFacebook
                ? thumbs.length
                  ? `Facebook · ${thumbs.length} photo${thumbs.length === 1 ? "" : "s"}`
                  : "Facebook furniture · Snap photos"
                : thumbs.length
                  ? `Lot · ${thumbs.length} photo${thumbs.length === 1 ? "" : "s"}`
                  : "Aim · tap Snap"}
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

        {!landscape ? (
          <>
            <Text style={styles.sectionTitle}>{isAppendMode ? "New photos" : "Photos"}</Text>
            <Text style={styles.sectionHint}>
              {isAppendMode
                ? "These attach to the existing listing · tap to review · × to remove"
                : "Tap to review · × to remove"}
            </Text>
          </>
        ) : (
          <Text style={styles.sectionHintLandscape}>{isAppendMode ? "New photos" : "Photos"}</Text>
        )}

        <DraftPhotoStrip
          uris={thumbs}
          size="md"
          onPressPhoto={openPreview}
          onDeletePhoto={deleteFromStrip}
        />

        {!isAppendMode ? (
          <TextInput
            placeholder={isFacebook ? "Asking price note (optional)" : "Manual SKU (optional)"}
            placeholderTextColor="#78716c"
            style={[styles.input, landscape && styles.inputLandscape]}
            value={manualSku}
            autoCapitalize={isFacebook ? "sentences" : "characters"}
            autoCorrect={false}
            onChangeText={onSkuChange}
          />
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <View style={[styles.actions, landscape && styles.actionsLandscape]}>
          {!landscape ? (
            <Pressable
              style={styles.secondary}
              onPress={() => {
                if (isAppendMode && thumbs.length) {
                  void cleanupAppendFiles(thumbs).then(() => {
                    setThumbs([]);
                    router.back();
                  });
                  return;
                }
                router.back();
              }}
            >
              <Text style={styles.secondaryText}>Cancel</Text>
            </Pressable>
          ) : null}
          <Pressable
            style={[
              styles.snap,
              landscape && styles.snapLandscape,
              (busy || !cameraReady || !sessionReady) && styles.disabled
            ]}
            disabled={busy || !cameraReady || !sessionReady}
            onPress={() => void snap()}
          >
            {busy || !sessionReady ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.primaryText}>Snap</Text>
            )}
          </Pressable>
          <Pressable
            style={[
              styles.primary,
              landscape && styles.primaryLandscape,
              (!thumbs.length || busy) && styles.disabled
            ]}
            disabled={!thumbs.length || busy}
            onPress={confirmFinish}
          >
            <Text style={styles.primaryText}>
              {isAppendMode ? (landscape ? "Upload" : "Upload photos") : landscape ? "Finish" : "Finish lot"}
            </Text>
          </Pressable>
        </View>
      </ScrollView>

      <PhotoPreviewModal
        uri={previewUri}
        index={previewIndex}
        total={thumbs.length}
        onClose={() => setPreviewUri(null)}
        onDelete={deleteFromPreview}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
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
  closeFabText: { color: "#f5f5f4", fontSize: 16, fontWeight: "700", lineHeight: 18 },
  cameraControls: {
    position: "absolute",
    left: 12,
    right: 12,
    bottom: 36,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  cameraControlsLandscape: {
    bottom: 12,
    left: 12,
    right: 12
  },
  controlsCol: {
    flex: 1,
    gap: 8
  },
  aspectRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "rgba(14, 14, 16, 0.72)",
    borderRadius: 999,
    paddingHorizontal: 6,
    paddingVertical: 4
  },
  aspectBtn: {
    flex: 1,
    minWidth: 44,
    paddingVertical: 7,
    paddingHorizontal: 6,
    borderRadius: 999,
    alignItems: "center",
    backgroundColor: "transparent"
  },
  aspectBtnActive: { backgroundColor: "#ff4d1a" },
  aspectBtnText: { color: "#e7e5e4", fontSize: 12, fontWeight: "700" },
  aspectBtnTextActive: { color: "#fff" },
  zoomTorchRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  zoomRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "rgba(14, 14, 16, 0.72)",
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 5
  },
  zoomBtn: {
    minWidth: 40,
    paddingVertical: 7,
    paddingHorizontal: 10,
    borderRadius: 999,
    alignItems: "center",
    backgroundColor: "transparent"
  },
  zoomBtnActive: { backgroundColor: "#ff4d1a" },
  zoomBtnText: { color: "#e7e5e4", fontSize: 13, fontWeight: "700" },
  zoomBtnTextActive: { color: "#fff" },
  zoomReadout: { color: "#a8a29e", fontSize: 12, fontWeight: "600", paddingHorizontal: 4 },
  torchBtn: {
    backgroundColor: "rgba(14, 14, 16, 0.72)",
    borderRadius: 999,
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: "rgba(168, 162, 158, 0.35)"
  },
  torchBtnActive: { backgroundColor: "#facc15", borderColor: "#facc15" },
  torchBtnText: { color: "#e7e5e4", fontSize: 13, fontWeight: "700" },
  torchBtnTextActive: { color: "#1c1917" },
  maskBar: {
    position: "absolute",
    backgroundColor: "rgba(0,0,0,0.45)"
  },
  maskFrame: {
    position: "absolute",
    borderWidth: 1.5,
    borderColor: "rgba(255,255,255,0.85)"
  },
  cameraHint: {
    position: "absolute",
    bottom: 96,
    left: 0,
    right: 0,
    textAlign: "center",
    color: "rgba(245, 245, 244, 0.75)",
    fontSize: 11,
    fontWeight: "600"
  },
  focusRing: {
    position: "absolute",
    width: 72,
    height: 72,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: "#fde047",
    shadowColor: "#000",
    shadowOpacity: 0.4,
    shadowRadius: 2,
    zIndex: 3
  },
  panel: {
    flexGrow: 0,
    flexShrink: 0,
    maxHeight: "38%",
    backgroundColor: "#0e0e10",
    borderTopWidth: 1,
    borderTopColor: "#141416"
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
    borderLeftColor: "#141416",
    alignSelf: "stretch"
  },
  panelContent: {
    paddingHorizontal: 12,
    paddingTop: 10,
    paddingBottom: 10,
    gap: 8
  },
  panelContentLandscape: {
    paddingTop: 10,
    paddingBottom: 10,
    paddingLeft: 10,
    paddingRight: 6,
    gap: 6,
    flexGrow: 1,
    justifyContent: "flex-start"
  },
  sessionMeta: {
    gap: 2,
    paddingBottom: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#141416"
  },
  sessionMetaLandscape: {
    marginBottom: 0,
    paddingBottom: 4
  },
  sessionMetaLabel: { color: "#f5f5f4", fontSize: 13, fontWeight: "600" },
  sessionMetaLabelLandscape: { fontSize: 13 },
  sessionMetaResumed: { color: "#86efac", fontSize: 12, fontWeight: "600" },
  sessionMetaSale: { color: "#ff7347", fontSize: 12, fontWeight: "600" },
  sectionTitle: { color: "#f5f5f4", fontSize: 14, fontWeight: "700" },
  sectionHint: { color: "#a8a29e", fontSize: 12, marginTop: -4 },
  sectionHintLandscape: { color: "#a8a29e", fontSize: 11, fontWeight: "600" },
  input: {
    borderWidth: 1,
    borderColor: "#3f3f46",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 9,
    color: "#f5f5f4"
  },
  inputLandscape: {
    paddingVertical: 8,
    paddingHorizontal: 10,
    fontSize: 14
  },
  actions: { flexDirection: "row", gap: 8, alignItems: "center", marginTop: 2 },
  actionsLandscape: { flexDirection: "column", gap: 6, marginTop: "auto" },
  snap: {
    flex: 1,
    backgroundColor: "#3f3f46",
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
    backgroundColor: "#ff4d1a",
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
  secondaryText: { color: "#a8a29e" },
  primaryText: { color: "#fff", fontWeight: "600" },
  disabled: { opacity: 0.45 },
  error: { color: "#fca5a5", fontSize: 13 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  text: { color: "#d6d3d1", textAlign: "center" }
});
