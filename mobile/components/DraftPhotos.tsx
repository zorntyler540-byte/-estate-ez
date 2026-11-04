import { useState } from "react";
import { Modal, Pressable, ScrollView, StyleSheet, View } from "react-native";
import { Text } from "@/components/AppText";

import { StableUriImage } from "@/components/StableUriImage";
import { ZoomablePreview } from "@/components/ZoomablePreview";

import { isVideoUri } from "@/lib/mediaKind";
import { playVideoUri } from "@/lib/playVideo";
import { stepCaptureIndex } from "@/lib/queue/previewOrder";
import { queueRowView } from "@/lib/queue/queueStatus";
import { theme } from "@/constants/theme";
import { hitSlopFor } from "@/lib/a11y";

type DraftPhotoStripProps = {
  uris: string[];
  size?: "md" | "lg";
  /** Decode at most this many thumbs (Queue). Remaining URIs still open in Review. */
  maxVisible?: number;
  cachePolicy?: "none" | "disk" | "memory" | "memory-disk";
  onPressPhoto?: (uri: string, index: number) => void;
  onDeletePhoto?: (uri: string, index: number) => void;
};

export function DraftPhotoStrip({
  uris,
  size = "md",
  maxVisible,
  cachePolicy,
  onPressPhoto,
  onDeletePhoto
}: DraftPhotoStripProps) {
  const thumbSize = size === "lg" ? 96 : 72;
  const shown = maxVisible != null && maxVisible >= 0 ? uris.slice(0, maxVisible) : uris;

  if (!uris.length) {
    return (
      <View style={styles.emptyBox}>
        <Text style={styles.emptyText}>No photos or videos yet</Text>
      </View>
    );
  }

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
      {shown.map((uri, index) => (
        <View key={`${index}:${uri}`} style={[styles.thumbWrap, { width: thumbSize, height: thumbSize }]}>
          <Pressable
            accessibilityRole="imagebutton"
            accessibilityLabel={`${isVideoUri(uri) ? "Video" : "Photo"} ${index + 1}, open preview`}
            style={styles.thumbPress}
            onPress={() => onPressPhoto?.(uri, index)}
          >
            {isVideoUri(uri) ? (
              <View style={[styles.thumbImage, styles.videoThumb]}>
                <Text style={styles.videoThumbMark}>▶</Text>
              </View>
            ) : (
              <StableUriImage
                uri={uri}
                style={styles.thumbImage}
                recyclingKey={uri}
                cachePolicy={cachePolicy}
              />
            )}
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{isVideoUri(uri) ? "Vid" : String(index + 1)}</Text>
            </View>
          </Pressable>
          {onDeletePhoto ? (
            <Pressable
              accessibilityRole="button"
              style={styles.stripDelete}
              hitSlop={hitSlopFor(24)}
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
  /** Photos in capture order (first taken = index 0). */
  uris: string[];
  index: number;
  onIndexChange?: (index: number) => void;
  onClose: () => void;
  onDelete?: () => void;
};

export function PhotoPreviewModal({
  uris,
  index,
  onIndexChange,
  onClose,
  onDelete
}: PhotoPreviewModalProps) {
  const uri = uris[index] ?? null;
  const total = uris.length;
  const canPrev = index > 0;
  const canNext = index < total - 1;
  const [playError, setPlayError] = useState("");
  const [playing, setPlaying] = useState(false);

  async function playCurrent() {
    if (!uri || !isVideoUri(uri)) return;
    setPlayError("");
    setPlaying(true);
    try {
      await playVideoUri(uri);
    } catch (e) {
      setPlayError(e instanceof Error ? e.message : "Could not play this video");
    } finally {
      setPlaying(false);
    }
  }

  function go(delta: number) {
    const next = stepCaptureIndex(index, total, delta);
    if (next === index) return;
    setPlayError("");
    onIndexChange?.(next);
  }

  return (
    <Modal visible={Boolean(uri)} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close preview"
          style={StyleSheet.absoluteFill}
          onPress={onClose}
        />
        <View style={styles.modalCard}>
          <Text style={styles.modalTitle}>
            {isVideoUri(uri) ? "Video" : "Photo"} {index + 1} of {total}
            {total > 1 ? " · taken order" : ""}
          </Text>
          {uri && !isVideoUri(uri) ? (
            <Text style={styles.modalZoomHint}>Pinch to zoom · swipe when not zoomed to change photos</Text>
          ) : null}
          {uri ? (
            <View style={styles.modalImageWrap}>
              {isVideoUri(uri) ? (
                <Pressable
                  accessibilityRole="button"
                  style={[styles.modalImage, styles.videoPreview]}
                  onPress={() => void playCurrent()}
                  accessibilityLabel="Play video"
                >
                  <Text style={styles.videoPreviewMark}>▶</Text>
                  <Text style={styles.videoPreviewHint}>
                    {playing ? "Opening…" : "Tap to play"}
                  </Text>
                  {playError ? <Text style={styles.videoPreviewError}>{playError}</Text> : null}
                </Pressable>
              ) : (
                <ZoomablePreview resetKey={`${index}:${uri}`} onSwipe={go}>
                  <StableUriImage uri={uri} style={styles.modalImage} recyclingKey={uri} contentFit="contain" />
                </ZoomablePreview>
              )}
            </View>
          ) : null}
          {total > 1 ? (
            <View style={styles.modalNav}>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ disabled: !canPrev }}
                style={[styles.modalNavBtn, !canPrev && styles.modalNavBtnDisabled]}
                disabled={!canPrev}
                onPress={() => go(-1)}
                accessibilityLabel="Previous photo"
              >
                <Text style={styles.modalNavText}>Previous</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ disabled: !canNext }}
                style={[styles.modalNavBtn, styles.modalNavBtnNext, !canNext && styles.modalNavBtnDisabled]}
                disabled={!canNext}
                onPress={() => go(1)}
                accessibilityLabel="Next photo"
              >
                <Text style={styles.modalNavText}>Next</Text>
              </Pressable>
            </View>
          ) : null}
          <View style={styles.modalActions}>
            {uri && isVideoUri(uri) ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Play video"
                style={styles.modalPlay}
                onPress={() => void playCurrent()}
              >
                <Text style={styles.modalPlayText}>{playing ? "Opening…" : "Play video"}</Text>
              </Pressable>
            ) : null}
            {onDelete ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={isVideoUri(uri) ? "Delete video" : "Delete photo"}
                style={styles.modalDelete}
                onPress={onDelete}
              >
                <Text style={styles.modalDeleteText}>{isVideoUri(uri) ? "Delete video" : "Delete photo"}</Text>
              </Pressable>
            ) : null}
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close preview"
              style={styles.modalClose}
              onPress={onClose}
            >
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
  opts?: {
    online?: boolean | null;
    syncing?: boolean;
    errorMessage?: string | null;
    archived?: boolean;
  }
): { label: string; hint: string } {
  const view = queueRowView({
    status,
    error_message: opts?.errorMessage,
    archived_at: opts?.archived ? 1 : null,
    online: opts?.online,
    syncing: opts?.syncing
  });
  return { label: view.label, hint: view.hint };
}

const styles = StyleSheet.create({
  row: { gap: 10, paddingVertical: 8, paddingTop: 10, paddingRight: 6 },
  emptyBox: {
    borderWidth: 1,
    borderColor: theme.borderStrong,
    borderStyle: "dashed",
    borderRadius: 10,
    paddingVertical: 18,
    alignItems: "center"
  },
  emptyText: { color: theme.textSubtle, fontSize: 14 },
  thumbWrap: {
    borderRadius: 10,
    overflow: "visible",
    backgroundColor: theme.surface,
    borderWidth: 2,
    borderColor: theme.borderStrong
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
    backgroundColor: theme.errorBtn,
    borderWidth: 2,
    borderColor: theme.bgElevated,
    alignItems: "center",
    justifyContent: "center"
  },
  stripDeleteText: { color: theme.text, fontSize: 18, fontWeight: "700", lineHeight: 20, marginTop: -1 },
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
  badgeText: { color: theme.text, fontSize: 12, fontWeight: "700" },
  videoThumb: { alignItems: "center", justifyContent: "center", backgroundColor: theme.surfaceRaised },
  videoThumbMark: { color: theme.text, fontSize: 22, fontWeight: "700" },
  videoPreview: { alignItems: "center", justifyContent: "center", backgroundColor: theme.surfaceRaised },
  videoPreviewMark: { color: theme.text, fontSize: 48, fontWeight: "700" },
  videoPreviewHint: { color: theme.textMuted, marginTop: 12, fontSize: 15, fontWeight: "600" },
  videoPreviewError: { color: theme.error, marginTop: 10, fontSize: 13, textAlign: "center" },
  modalPlay: {
    backgroundColor: theme.primary,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 20
  },
  modalPlayText: { color: theme.onPrimary, fontWeight: "700", fontSize: 16 },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(7, 7, 8, 0.92)",
    justifyContent: "center",
    padding: 16
  },
  modalCard: { gap: 12 },
  modalTitle: { color: theme.textSoft, fontSize: 16, fontWeight: "600", textAlign: "center" },
  modalZoomHint: { color: theme.textMuted, fontSize: 13, textAlign: "center" },
  modalImageWrap: { width: "100%", height: "68%", overflow: "hidden", borderRadius: 12 },
  modalImage: { width: "100%", height: "100%", borderRadius: 12, backgroundColor: theme.bgElevated },
  modalNav: { flexDirection: "row", gap: 10 },
  modalNavBtn: {
    flex: 1,
    backgroundColor: theme.surfaceRaised,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    borderWidth: 1,
    borderColor: theme.borderStrong
  },
  modalNavBtnNext: { backgroundColor: theme.surfaceRaised },
  modalNavBtnDisabled: { opacity: 0.35 },
  modalNavText: { color: theme.text, fontWeight: "700", fontSize: 16 },
  modalActions: { flexDirection: "row", justifyContent: "center", gap: 12 },
  modalDelete: {
    backgroundColor: theme.errorBorder,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 20
  },
  modalDeleteText: { color: theme.error, fontWeight: "600", fontSize: 16 },
  modalClose: {
    backgroundColor: theme.borderStrong,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 28
  },
  modalCloseText: { color: theme.text, fontWeight: "600", fontSize: 16 }
});
