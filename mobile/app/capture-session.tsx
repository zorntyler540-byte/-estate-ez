import { CameraView, useCameraPermissions } from "expo-camera";
import * as FileSystem from "expo-file-system/legacy";
import { router } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import { DraftPhotoStrip, PhotoPreviewModal } from "@/components/DraftPhotos";
import { useAuth } from "@/context/AuthContext";
import { useSync } from "@/context/SyncContext";
import {
  addPhotoToDraft,
  createLotDraft,
  deletePhotoFromDraft,
  listDraftPhotos,
  setDraftStatus
} from "@/lib/queue/db";

export default function CaptureSessionScreen() {
  const { selectedSaleId, selectedSaleName } = useAuth();
  const { syncNow } = useSync();
  const cameraRef = useRef<CameraView>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [draftId, setDraftId] = useState<string | null>(null);
  const [manualSku, setManualSku] = useState("");
  const [thumbs, setThumbs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [previewIndex, setPreviewIndex] = useState(0);

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

  async function snap() {
    if (!cameraRef.current || busy) return;
    setError("");
    setBusy(true);
    try {
      const id = await ensureDraft();
      if (!id) return;
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.92,
        skipProcessing: false
      });
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

  async function finish() {
    if (!draftId) {
      router.back();
      return;
    }
    if (!thumbs.length) {
      Alert.alert("No photos", "Snap at least one photo before finishing this lot.");
      return;
    }
    await setDraftStatus(draftId, "queued", null);
    void syncNow();
    Alert.alert(
      "Lot saved",
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
    <View style={styles.root}>
      <View style={[styles.cameraWrap, thumbs.length > 0 && styles.cameraWrapCompact]}>
        <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" />
        <View style={styles.cameraOverlay}>
          <Text style={styles.cameraLabel}>
            {thumbs.length ? `Lot in progress · ${thumbs.length} photo${thumbs.length === 1 ? "" : "s"}` : "Aim and tap Snap"}
          </Text>
          {selectedSaleName ? (
            <Text style={styles.cameraSale}>Sale: {selectedSaleName}</Text>
          ) : null}
        </View>
      </View>

      <View style={styles.panel}>
        <Text style={styles.sectionTitle}>Photos for this lot</Text>
        <Text style={styles.sectionHint}>Tap a photo to review. Tap × on a thumb to remove it.</Text>

        <DraftPhotoStrip
          uris={thumbs}
          size="lg"
          onPressPhoto={openPreview}
          onDeletePhoto={deleteFromStrip}
        />

        <TextInput
          placeholder="Manual SKU (optional)"
          placeholderTextColor="#64748b"
          style={styles.input}
          value={manualSku}
          onChangeText={setManualSku}
          editable={!draftId}
        />
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <View style={styles.actions}>
          <Pressable style={styles.secondary} onPress={() => router.back()}>
            <Text style={styles.secondaryText}>Cancel</Text>
          </Pressable>
          <Pressable style={[styles.snap, busy && styles.disabled]} disabled={busy} onPress={() => void snap()}>
            {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>Snap</Text>}
          </Pressable>
          <Pressable
            style={[styles.primary, !thumbs.length && styles.disabled]}
            disabled={!thumbs.length}
            onPress={() => void finish()}
          >
            <Text style={styles.primaryText}>Finish lot</Text>
          </Pressable>
        </View>
      </View>

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
  cameraWrap: { flex: 1, minHeight: 280, position: "relative" },
  cameraWrapCompact: { flex: 0.48, minHeight: 220 },
  cameraOverlay: {
    position: "absolute",
    top: 12,
    left: 12,
    right: 12,
    backgroundColor: "rgba(15, 23, 42, 0.72)",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  cameraLabel: { color: "#f8fafc", fontSize: 14, fontWeight: "600", textAlign: "center" },
  cameraSale: { color: "#5eead4", fontSize: 12, fontWeight: "600", textAlign: "center", marginTop: 2 },
  panel: {
    flex: 1,
    padding: 14,
    gap: 10,
    backgroundColor: "#0f172a",
    borderTopWidth: 1,
    borderTopColor: "#1e293b"
  },
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
  snap: {
    flex: 1,
    backgroundColor: "#334155",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
    minHeight: 48,
    justifyContent: "center"
  },
  primary: {
    flex: 1,
    backgroundColor: "#0d9488",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  secondary: { paddingVertical: 14, paddingHorizontal: 12 },
  secondaryText: { color: "#94a3b8" },
  primaryText: { color: "#fff", fontWeight: "600" },
  disabled: { opacity: 0.45 },
  error: { color: "#fca5a5", fontSize: 13 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  text: { color: "#cbd5e1", textAlign: "center" }
});
