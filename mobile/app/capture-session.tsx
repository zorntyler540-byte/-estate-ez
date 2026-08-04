import { CameraView, useCameraPermissions, type FocusMode } from "expo-camera";
import * as FileSystem from "expo-file-system/legacy";
import { router } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  PanResponder,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
  type GestureResponderEvent
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { DraftPhotoStrip, PhotoPreviewModal } from "@/components/DraftPhotos";
import { useAuth } from "@/context/AuthContext";
import { useSync } from "@/context/SyncContext";
import { MAX_DETAIL_CAPTURE_OPTIONS, pickLargestPictureSize } from "@/lib/cameraCapture";
import {
  addPhotoToDraft,
  createLotDraft,
  deletePhotoFromDraft,
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

export default function CaptureSessionScreen() {
  const { selectedSaleId, selectedSaleName } = useAuth();
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

  useEffect(() => {
    return () => {
      if (focusRingTimerRef.current) clearTimeout(focusRingTimerRef.current);
      if (refocusTimerRef.current) clearTimeout(refocusTimerRef.current);
    };
  }, []);

  async function ensureDraft(): Promise<string | null> {
    if (draftId) return draftId;
    if (!selectedSaleId) {
      setError("Select a sale first.");
      return null;
    }
    const id = await createLotDraft(selectedSaleId, selectedSaleName, manualSku);
    setDraftId(id);
    return id;
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
    if (!cameraRef.current || busy || !cameraReady) return;
    setError("");
    setBusy(true);
    try {
      const id = await ensureDraft();
      if (!id) return;
      const photo = await cameraRef.current.takePictureAsync(MAX_DETAIL_CAPTURE_OPTIONS);
      if (!photo?.uri) {
        setError("Camera returned no image.");
        return;
      }
      const dir = `${FileSystem.documentDirectory}lot-captures/${id}/`;
      await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
      const dest = `${dir}${Date.now()}.jpg`;
      await FileSystem.copyAsync({ from: photo.uri, to: dest });
      await addPhotoToDraft(id, dest);
      const photos = await listDraftPhotos(id);
      const uris = photos.map((p) => p.file_uri);
      setThumbs(uris);
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
    if (!draftId) return;

    async function performDelete() {
      try {
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
    if (!draftId) {
      router.back();
      return;
    }
    if (!thumbs.length) {
      Alert.alert("No photos", "Snap at least one photo before finishing this lot.");
      return;
    }
    const skuLabel = manualSku.trim() ? `SKU ${manualSku.trim().toUpperCase()}` : "no manual SKU";
    const saleLabel = selectedSaleName ? `"${selectedSaleName}"` : "this sale";
    Alert.alert(
      "Send lot to pipeline?",
      `${thumbs.length} photo${thumbs.length === 1 ? "" : "s"} · ${skuLabel}\n\nThis uploads to ${saleLabel} and starts AI processing. You can’t undo from here.`,
      [
        { text: "Keep editing", style: "cancel" },
        { text: "Send", style: "default", onPress: () => void finishLot() }
      ]
    );
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
        {
          paddingTop: landscape ? insets.top : 0,
          paddingBottom: landscape ? insets.bottom : 0,
          paddingLeft: landscape ? insets.left : 0,
          paddingRight: landscape ? insets.right : 0
        }
      ]}
    >
      <View
        style={[
          styles.cameraWrap,
          landscape ? styles.cameraWrapLandscape : thumbs.length > 0 && styles.cameraWrapCompact
        ]}
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
        {focusPoint ? (
          <View
            pointerEvents="none"
            style={[styles.focusRing, { left: focusPoint.x - 36, top: focusPoint.y - 36 }]}
          />
        ) : null}
        <View style={[styles.cameraControls, landscape && styles.cameraControlsLandscape]}>
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
        <Text
          style={[styles.cameraHint, landscape && styles.cameraHintLandscape]}
          pointerEvents="none"
        >
          Pinch to zoom · tap to refocus
        </Text>
      </View>

      <ScrollView
        style={[styles.panel, landscape && styles.panelLandscape]}
        contentContainerStyle={[
          styles.panelContent,
          landscape && styles.panelContentLandscape
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.sessionMeta}>
          <Text style={styles.sessionMetaLabel}>
            {thumbs.length
              ? `Lot in progress · ${thumbs.length} photo${thumbs.length === 1 ? "" : "s"}`
              : "Aim and tap Snap"}
          </Text>
          {selectedSaleName ? (
            <Text style={styles.sessionMetaSale} numberOfLines={1}>
              {selectedSaleName}
            </Text>
          ) : null}
        </View>

        <Text style={styles.sectionTitle}>Photos for this lot</Text>
        <Text style={styles.sectionHint}>Tap a photo to review. Tap × on a thumb to remove it.</Text>

        <DraftPhotoStrip
          uris={thumbs}
          size={landscape ? "md" : "lg"}
          onPressPhoto={openPreview}
          onDeletePhoto={deleteFromStrip}
        />

        <TextInput
          placeholder="Manual SKU (optional)"
          placeholderTextColor="#64748b"
          style={styles.input}
          value={manualSku}
          onChangeText={onSkuChange}
          autoCapitalize="characters"
          autoCorrect={false}
        />
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <View style={[styles.actions, landscape && styles.actionsLandscape]}>
          <Pressable style={styles.secondary} onPress={() => router.back()}>
            <Text style={styles.secondaryText}>Cancel</Text>
          </Pressable>
          <Pressable
            style={[styles.snap, (busy || !cameraReady) && styles.disabled]}
            disabled={busy || !cameraReady}
            onPress={() => void snap()}
          >
            {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>Snap</Text>}
          </Pressable>
          <Pressable
            style={[styles.primary, !thumbs.length && styles.disabled]}
            disabled={!thumbs.length}
            onPress={confirmFinish}
          >
            <Text style={styles.primaryText}>Finish lot</Text>
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
  rootLandscape: { flexDirection: "row" },
  cameraWrap: { flex: 1, minHeight: 280, position: "relative" },
  cameraWrapCompact: { flex: 0.48, minHeight: 220 },
  cameraWrapLandscape: { flex: 1, minHeight: 0, minWidth: 0 },
  cameraControls: {
    position: "absolute",
    left: 12,
    right: 12,
    bottom: 34,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  cameraControlsLandscape: {
    bottom: 16,
    left: 16,
    right: 16
  },
  zoomRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "rgba(15, 23, 42, 0.72)",
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
  zoomBtnActive: { backgroundColor: "#0d9488" },
  zoomBtnText: { color: "#e2e8f0", fontSize: 13, fontWeight: "700" },
  zoomBtnTextActive: { color: "#fff" },
  zoomReadout: { color: "#94a3b8", fontSize: 12, fontWeight: "600", paddingHorizontal: 4 },
  torchBtn: {
    backgroundColor: "rgba(15, 23, 42, 0.72)",
    borderRadius: 999,
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: "rgba(148, 163, 184, 0.35)"
  },
  torchBtnActive: { backgroundColor: "#facc15", borderColor: "#facc15" },
  torchBtnText: { color: "#e2e8f0", fontSize: 13, fontWeight: "700" },
  torchBtnTextActive: { color: "#1c1917" },
  cameraHint: {
    position: "absolute",
    bottom: 10,
    left: 0,
    right: 0,
    textAlign: "center",
    color: "rgba(226, 232, 240, 0.75)",
    fontSize: 11,
    fontWeight: "600"
  },
  cameraHintLandscape: {
    bottom: 4,
    opacity: 0.85
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
    flex: 1,
    backgroundColor: "#0f172a",
    borderTopWidth: 1,
    borderTopColor: "#1e293b"
  },
  panelLandscape: {
    flex: 0,
    width: 300,
    maxWidth: "42%",
    borderTopWidth: 0,
    borderLeftWidth: 1,
    borderLeftColor: "#1e293b"
  },
  panelContent: {
    padding: 14,
    gap: 10,
    flexGrow: 1
  },
  panelContentLandscape: {
    paddingVertical: 12,
    paddingHorizontal: 12,
    gap: 8
  },
  sessionMeta: {
    gap: 2,
    paddingBottom: 2,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#1e293b",
    marginBottom: 2
  },
  sessionMetaLabel: { color: "#f8fafc", fontSize: 14, fontWeight: "600" },
  sessionMetaSale: { color: "#5eead4", fontSize: 12, fontWeight: "600" },
  sectionTitle: { color: "#f8fafc", fontSize: 16, fontWeight: "700" },
  sectionHint: { color: "#94a3b8", fontSize: 13, marginTop: -4 },
  input: {
    borderWidth: 1,
    borderColor: "#334155",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: "#f8fafc"
  },
  actions: { flexDirection: "row", gap: 8, alignItems: "center", marginTop: 4 },
  actionsLandscape: { flexWrap: "wrap" },
  snap: {
    flex: 1,
    backgroundColor: "#334155",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
    minHeight: 48,
    justifyContent: "center",
    minWidth: 72
  },
  primary: {
    flex: 1,
    backgroundColor: "#0d9488",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
    minWidth: 72
  },
  secondary: { paddingVertical: 14, paddingHorizontal: 12 },
  secondaryText: { color: "#94a3b8" },
  primaryText: { color: "#fff", fontWeight: "600" },
  disabled: { opacity: 0.45 },
  error: { color: "#fca5a5", fontSize: 13 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  text: { color: "#cbd5e1", textAlign: "center" }
});
