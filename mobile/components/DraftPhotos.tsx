import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

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
            <Image source={{ uri }} style={styles.thumbImage} />
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
          {uri ? <Image source={{ uri }} style={styles.modalImage} resizeMode="contain" /> : null}
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

export function draftStatusDisplay(status: string): { label: string; hint: string } {
  switch (status) {
    case "draft":
      return { label: "Saved on phone", hint: "Not queued for upload yet" };
    case "queued":
      return { label: "Ready to upload", hint: "Will send when you're online" };
    case "uploading":
      return { label: "Uploading…", hint: "Sending photos to the server" };
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
    borderColor: "#334155",
    borderStyle: "dashed",
    borderRadius: 10,
    paddingVertical: 18,
    alignItems: "center"
  },
  emptyText: { color: "#64748b", fontSize: 14 },
  thumbWrap: {
    borderRadius: 10,
    overflow: "visible",
    backgroundColor: "#1e293b",
    borderWidth: 2,
    borderColor: "#334155"
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
    borderColor: "#0f172a",
    alignItems: "center",
    justifyContent: "center"
  },
  stripDeleteText: { color: "#fff", fontSize: 18, fontWeight: "700", lineHeight: 20, marginTop: -1 },
  badge: {
    position: "absolute",
    top: 6,
    left: 6,
    backgroundColor: "rgba(15, 23, 42, 0.85)",
    borderRadius: 999,
    minWidth: 22,
    height: 22,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 6
  },
  badgeText: { color: "#f8fafc", fontSize: 12, fontWeight: "700" },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(2, 6, 23, 0.92)",
    justifyContent: "center",
    padding: 16
  },
  modalCard: { gap: 12 },
  modalTitle: { color: "#e2e8f0", fontSize: 16, fontWeight: "600", textAlign: "center" },
  modalImage: { width: "100%", height: "72%", borderRadius: 12, backgroundColor: "#0f172a" },
  modalActions: { flexDirection: "row", justifyContent: "center", gap: 12 },
  modalDelete: {
    backgroundColor: "#7f1d1d",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 20
  },
  modalDeleteText: { color: "#fecaca", fontWeight: "600", fontSize: 16 },
  modalClose: {
    backgroundColor: "#334155",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 28
  },
  modalCloseText: { color: "#f8fafc", fontWeight: "600", fontSize: 16 }
});
