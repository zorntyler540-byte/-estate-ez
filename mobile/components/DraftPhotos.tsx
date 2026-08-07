import { Image } from "expo-image";
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

type DraftPhotoStripProps = {
  uris: string[];
  size?: "md" | "lg";
  onPressPhoto?: (uri: string, index: number) => void;
  onDeletePhoto?: (uri: string, index: number) => void;
};

export function DraftPhotoStrip({ uris, size = "md", onPressPhoto, onDeletePhoto }: DraftPhotoStripProps) {
  const thumbSize = size === "lg" ? 96 : 72;

  if (!uris.length) {
    return (
      <View style={styles.emptyBox}>
        <Text style={styles.emptyText}>No photos yet</Text>
      </View>
    );
  }

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
      {uris.map((uri, index) => (
        <View key={uri} style={[styles.thumbWrap, { width: thumbSize, height: thumbSize }]}>
          <Pressable style={styles.thumbPress} onPress={() => onPressPhoto?.(uri, index)}>
            <Image source={{ uri }} style={styles.thumbImage} contentFit="cover" />
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{index + 1}</Text>
            </View>
          </Pressable>
          {onDeletePhoto ? (
            <Pressable
              style={styles.stripDelete}
              hitSlop={8}
              onPress={() => onDeletePhoto(uri, index)}
              accessibilityLabel={`Delete photo ${index + 1}`}
            >
              <Text style={styles.stripDeleteText}>×</Text>
            </Pressable>
          ) : null}
        </View>
      ))}
    </ScrollView>
  );
}

type PhotoPreviewModalProps = {
  uri: string | null;
  index: number;
  total: number;
  onClose: () => void;
  onDelete?: () => void;
};

export function PhotoPreviewModal({ uri, index, total, onClose, onDelete }: PhotoPreviewModalProps) {
  return (
    <Modal visible={Boolean(uri)} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={styles.modalCard}>
          <Text style={styles.modalTitle}>
            Photo {index + 1} of {total}
          </Text>
          {uri ? <Image source={{ uri }} style={styles.modalImage} contentFit="contain" /> : null}
          <View style={styles.modalActions}>
            {onDelete ? (
              <Pressable style={styles.modalDelete} onPress={onDelete}>
                <Text style={styles.modalDeleteText}>Delete photo</Text>
              </Pressable>
            ) : null}
            <Pressable style={styles.modalClose} onPress={onClose}>
              <Text style={styles.modalCloseText}>Close</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

export function draftStatusDisplay(
  status: string,
  opts?: { online?: boolean | null; syncing?: boolean }
): { label: string; hint: string } {
  switch (status) {
    case "draft":
      return {
        label: "Saved on phone",
        hint: "Photos are kept if you leave the app — tap Continue to add more, or Queue for upload"
      };
    case "queued":
      if (opts?.syncing) {
        return { label: "Ready to upload", hint: "In line — Sync is sending lots now" };
      }
      if (opts?.online === false) {
        return { label: "Ready to upload", hint: "Will send when you're back online" };
      }
      return { label: "Ready to upload", hint: "Tap Sync now to send" };
    case "uploading":
      return {
        label: "Uploading…",
        hint: "Sending photos to the server. If this sticks, Sync now will retry."
      };
    case "uploaded":
      return { label: "Sent to server", hint: "Open the web dashboard to review the catalog listing" };
    case "failed":
      return { label: "Upload failed", hint: "Tap Sync now to retry" };
    default:
      return { label: status, hint: "" };
  }
}

const styles = StyleSheet.create({
  row: { gap: 10, paddingVertical: 8, paddingTop: 10, paddingRight: 6 },
  emptyBox: {
    borderWidth: 1,
    borderColor: "#3f3f46",
    borderStyle: "dashed",
    borderRadius: 10,
    paddingVertical: 18,
    alignItems: "center"
  },
  emptyText: { color: "#78716c", fontSize: 14 },
  thumbWrap: {
    borderRadius: 10,
    overflow: "visible",
    backgroundColor: "#141416",
    borderWidth: 2,
    borderColor: "#3f3f46"
  },
  thumbPress: { flex: 1, borderRadius: 8, overflow: "hidden" },
  thumbImage: { width: "100%", height: "100%" },
  stripDelete: {
    position: "absolute",
    top: -6,
    right: -6,
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: "#b91c1c",
    borderWidth: 2,
    borderColor: "#0e0e10",
    alignItems: "center",
    justifyContent: "center"
  },
  stripDeleteText: { color: "#fff", fontSize: 18, fontWeight: "700", lineHeight: 20, marginTop: -1 },
  badge: {
    position: "absolute",
    top: 6,
    left: 6,
    backgroundColor: "rgba(14, 14, 16, 0.85)",
    borderRadius: 999,
    minWidth: 22,
    height: 22,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 6
  },
  badgeText: { color: "#f5f5f4", fontSize: 12, fontWeight: "700" },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(7, 7, 8, 0.92)",
    justifyContent: "center",
    padding: 16
  },
  modalCard: { gap: 12 },
  modalTitle: { color: "#e7e5e4", fontSize: 16, fontWeight: "600", textAlign: "center" },
  modalImage: { width: "100%", height: "72%", borderRadius: 12, backgroundColor: "#0e0e10" },
  modalActions: { flexDirection: "row", justifyContent: "center", gap: 12 },
  modalDelete: {
    backgroundColor: "#7f1d1d",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 20
  },
  modalDeleteText: { color: "#fecaca", fontWeight: "600", fontSize: 16 },
  modalClose: {
    backgroundColor: "#3f3f46",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 28
  },
  modalCloseText: { color: "#f5f5f4", fontWeight: "600", fontSize: 16 }
});
