import { router, Stack } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Text, TextInput } from "@/components/AppText";
import { StableUriImage } from "@/components/StableUriImage";
import { theme } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { useSyncActions } from "@/context/SyncContext";
import { MIN_TAP_PX } from "@/lib/a11y";
import { CTBIDS_MAX_PHOTOS_PER_LOT } from "@/lib/catalogLimits";
import { captureGate } from "@/lib/clientCapture";
import {
  discardInterruptedImports,
  importLots,
  pickCameraRollPhotos,
  type ImportProgress
} from "@/lib/queue/cameraRollImport";
import {
  IMPORT_DEFAULT_GAP_MS,
  IMPORT_GAP_CHOICES_MS,
  IMPORT_MAX_PICKS,
  planImportGroups,
  type ImportGroup,
  type ImportGroupMode,
  type ImportPick
} from "@/lib/queue/importGroups";

function gapLabel(ms: number): string {
  return ms < 60_000 ? `${Math.round(ms / 1000)}s` : `${Math.round(ms / 60_000)} min`;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export default function ImportPhotosScreen() {
  const insets = useSafeAreaInsets();
  const { selectedSaleId, selectedSaleName, signedInRole } = useAuth();
  const { getSaleName } = useSales();
  const { requestUpload } = useSyncActions();
  const saleLabel = selectedSaleId ? getSaleName(selectedSaleId, selectedSaleName) : "";
  const gate = captureGate({ role: signedInRole, saleId: selectedSaleId, saleName: saleLabel });

  const [picks, setPicks] = useState<ImportPick[]>([]);
  const [mode, setMode] = useState<ImportGroupMode>("gap");
  const [gapMs, setGapMs] = useState<number>(IMPORT_DEFAULT_GAP_MS);
  const [toggled, setToggled] = useState<Set<string>>(() => new Set());
  /** SKU per lot, keyed by the lot's first photo so unrelated splits keep it. */
  const [skuByStart, setSkuByStart] = useState<Record<string, string>>({});
  const [picking, setPicking] = useState(false);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [note, setNote] = useState("");
  const importing = progress != null;

  useEffect(() => {
    void discardInterruptedImports().catch(() => 0);
  }, []);

  const dated = picks.length > 0 && picks.every((p) => p.takenAt != null);
  const effectiveMode: ImportGroupMode = mode === "gap" && dated ? "gap" : "one";
  const groups = useMemo(
    () => planImportGroups(picks, { mode: effectiveMode, gapMs, toggled }),
    [picks, effectiveMode, gapMs, toggled]
  );
  const cappedCount = groups.filter((g) => g.capped).length;

  const choose = useCallback(async () => {
    if (picking || importing) return;
    setPicking(true);
    setNote("");
    try {
      const res = await pickCameraRollPhotos();
      if (!res) return;
      setPicks(res.picks);
      setToggled(new Set());
      setSkuByStart({});
      const notes: string[] = [];
      if (res.truncated) notes.push(`Only the first ${IMPORT_MAX_PICKS} photos were kept — import the rest next.`);
      if (!res.picks.every((p) => p.takenAt != null)) {
        notes.push("Some photos have no capture time, so they stay in the order you picked them. Tap a photo to start a new lot there.");
      }
      setNote(notes.join(" "));
    } catch (e) {
      Alert.alert("Could not open Photos", e instanceof Error ? e.message : "Try again.");
    } finally {
      setPicking(false);
    }
  }, [picking, importing]);

  const toggleAt = useCallback((key: string) => {
    setToggled((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const runImport = useCallback(async () => {
    if (!selectedSaleId || importing || !groups.length) return;
    const lots = groups.map((g) => ({
      picks: g.picks,
      manualSku: skuByStart[g.picks[0]!.key]?.trim() || null
    }));
    setProgress({ photosDone: 0, photos: picks.length, lotsQueued: 0, lots: lots.length });
    try {
      const out = await importLots({
        saleId: selectedSaleId,
        saleName: saleLabel || selectedSaleName || null,
        lots,
        onProgress: setProgress,
        onLotQueued: requestUpload
      });
      const skipped = out.photosSkipped
        ? ` ${plural(out.photosSkipped, "photo")} could not be opened (an iCloud original that is not downloaded?) and ${out.photosSkipped === 1 ? "was" : "were"} left out.`
        : "";
      Alert.alert(
        `${plural(out.lotsQueued, "lot")} queued`,
        `They upload on their own when the connection is stable — a few at a time on big imports.${skipped}`
      );
      router.replace("/(tabs)/queue");
    } catch (e) {
      setProgress(null);
      Alert.alert(
        "Import stopped",
        `${e instanceof Error ? e.message : "Copying photos failed."} Lots already queued stay in Queue; pick the rest again.`
      );
    }
  }, [selectedSaleId, importing, groups, skuByStart, picks.length, saleLabel, selectedSaleName, requestUpload]);

  const renderGroup = useCallback(
    ({ item, index }: { item: ImportGroup; index: number }) => {
      const start = item.picks[0]!.key;
      return (
        <View style={styles.group}>
          <View style={styles.groupHeader}>
            <Text style={styles.groupTitle}>
              Lot {index + 1} · {plural(item.picks.length, "photo")}
            </Text>
            {item.capped ? (
              <Text style={styles.badge}>Split at {CTBIDS_MAX_PHOTOS_PER_LOT}</Text>
            ) : null}
          </View>
          <TextInput
            placeholder="SKU (optional)"
            placeholderTextColor={theme.textSubtle}
            style={styles.input}
            value={skuByStart[start] ?? ""}
            autoCapitalize="characters"
            autoCorrect={false}
            editable={!importing}
            onChangeText={(text) => setSkuByStart((prev) => ({ ...prev, [start]: text }))}
          />
          <FlatList
            horizontal
            data={item.picks}
            keyExtractor={(p) => p.key}
            initialNumToRender={6}
            windowSize={3}
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.thumbRow}
            renderItem={({ item: p, index: i }) => {
              const joinsPrevious = i === 0 && (item.start === "auto" || item.start === "manual");
              const label = joinsPrevious ? "Join lot above" : i > 0 ? "New lot here" : null;
              return (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={label ? `${label}, photo ${i + 1} of lot ${index + 1}` : `Photo ${i + 1} of lot ${index + 1}`}
                  disabled={importing || !label}
                  onPress={() => toggleAt(p.key)}
                  style={styles.thumbWrap}
                >
                  <StableUriImage uri={p.uri} style={styles.thumb} cachePolicy="none" />
                  {label ? <Text style={styles.thumbAction}>{joinsPrevious ? "Join ↑" : "Split"}</Text> : null}
                </Pressable>
              );
            }}
          />
        </View>
      );
    },
    [importing, skuByStart, toggleAt]
  );

  const header = (
    <View style={styles.headerBlock}>
      {gate.action === "capture" ? (
        <Text style={styles.lead}>
          Pick photos from the camera roll. Each group below becomes its own lot in{" "}
          <Text style={styles.saleName}>{saleLabel}</Text> and uploads from Queue like a lot you shot here.
        </Text>
      ) : (
        <Text style={styles.warn}>{gate.message}</Text>
      )}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: picking || importing || gate.action !== "capture" }}
        disabled={picking || importing || gate.action !== "capture"}
        style={[styles.secondary, (picking || importing || gate.action !== "capture") && styles.disabled]}
        onPress={() => void choose()}
      >
        <Text style={styles.secondaryText}>
          {picking ? "Opening Photos…" : picks.length ? "Choose different photos" : `Choose photos (up to ${IMPORT_MAX_PICKS})`}
        </Text>
      </Pressable>
      {picks.length ? (
        <>
          <View style={styles.segment}>
            {(["gap", "one"] as const).map((m) => (
              <Pressable
                key={m}
                accessibilityRole="button"
                accessibilityState={{ selected: mode === m, disabled: importing || (m === "gap" && !dated) }}
                disabled={importing || (m === "gap" && !dated)}
                style={[styles.segmentBtn, mode === m && styles.segmentOn, m === "gap" && !dated && styles.disabled]}
                onPress={() => {
                  setMode(m);
                  setToggled(new Set());
                }}
              >
                <Text style={[styles.segmentText, mode === m && styles.segmentTextOn]}>
                  {m === "gap" ? "Split by time gap" : "One lot (split by hand)"}
                </Text>
              </Pressable>
            ))}
          </View>
          {effectiveMode === "gap" ? (
            <View style={styles.chips}>
              <Text style={styles.meta}>New lot after a pause of</Text>
              {IMPORT_GAP_CHOICES_MS.map((ms) => (
                <Pressable
                  key={ms}
                  accessibilityRole="button"
                  accessibilityState={{ selected: gapMs === ms }}
                  disabled={importing}
                  style={[styles.chip, gapMs === ms && styles.chipOn]}
                  onPress={() => {
                    setGapMs(ms);
                    setToggled(new Set());
                  }}
                >
                  <Text style={[styles.chipText, gapMs === ms && styles.chipTextOn]}>{gapLabel(ms)}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}
          <Text style={styles.meta}>
            {plural(picks.length, "photo")} → {plural(groups.length, "lot")}. Tap Split on a photo to start a new lot
            there, or Join on a lot's first photo to merge it into the lot above.
            {cappedCount ? ` Lots over ${CTBIDS_MAX_PHOTOS_PER_LOT} photos (the CT Bids limit) are split for you.` : ""}
          </Text>
        </>
      ) : null}
      {note ? <Text style={styles.note}>{note}</Text> : null}
    </View>
  );

  return (
    <View style={styles.root}>
      <Stack.Screen
        options={{ title: "Import from Photos", headerBackVisible: !importing, gestureEnabled: !importing }}
      />
      <FlatList
        data={groups}
        keyExtractor={(g) => g.picks[0]!.key}
        renderItem={renderGroup}
        ListHeaderComponent={header}
        initialNumToRender={4}
        windowSize={5}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={styles.list}
      />
      {picks.length ? (
        <View style={[styles.footer, { paddingBottom: Math.max(insets.bottom, 12) }]}>
          {progress ? (
            <View style={styles.progressRow}>
              <ActivityIndicator color={theme.primary} />
              <Text style={styles.progressText}>
                Copying {progress.photosDone} of {progress.photos} photos · {progress.lotsQueued} of{" "}
                {progress.lots} lots queued. Keep the app open.
              </Text>
            </View>
          ) : (
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: !groups.length || gate.action !== "capture" }}
              disabled={!groups.length || gate.action !== "capture"}
              style={[styles.primary, gate.action !== "capture" && styles.disabled]}
              onPress={() => void runImport()}
            >
              <Text style={styles.primaryText}>Import {plural(groups.length, "lot")}</Text>
            </Pressable>
          )}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  list: { padding: 16, gap: 12 },
  headerBlock: { gap: 12, marginBottom: 4 },
  lead: { color: theme.textMuted, fontSize: 15, lineHeight: 22 },
  saleName: { color: theme.primary, fontWeight: "700" },
  warn: { color: theme.warning, fontSize: 14 },
  meta: { color: theme.textMuted, fontSize: 14, lineHeight: 20 },
  note: { color: theme.warning, fontSize: 14, lineHeight: 20 },
  segment: { flexDirection: "row", gap: 8 },
  segmentBtn: {
    flex: 1,
    minHeight: MIN_TAP_PX,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.borderStrong,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 8
  },
  segmentOn: { borderColor: theme.primary, backgroundColor: theme.primaryMuted },
  segmentText: { color: theme.textSoft, fontSize: 14, fontWeight: "600", textAlign: "center" },
  segmentTextOn: { color: theme.primary },
  chips: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  chip: {
    minHeight: 36,
    paddingHorizontal: 12,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: theme.borderStrong,
    justifyContent: "center"
  },
  chipOn: { borderColor: theme.primary, backgroundColor: theme.primaryMuted },
  chipText: { color: theme.textSoft, fontSize: 14 },
  chipTextOn: { color: theme.primary, fontWeight: "700" },
  group: {
    backgroundColor: theme.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: theme.border,
    padding: 12,
    gap: 10
  },
  groupHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  groupTitle: { color: theme.text, fontSize: 16, fontWeight: "700" },
  badge: { color: theme.warning, fontSize: 13, fontWeight: "600" },
  input: {
    minHeight: MIN_TAP_PX,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.borderStrong,
    backgroundColor: theme.bgElevated,
    color: theme.text,
    paddingHorizontal: 12,
    fontSize: 15
  },
  thumbRow: { gap: 8 },
  thumbWrap: { width: 84, alignItems: "center", gap: 4 },
  thumb: { width: 84, height: 84, borderRadius: 8, backgroundColor: theme.bgElevated },
  thumbAction: { color: theme.primary, fontSize: 12, fontWeight: "700" },
  footer: {
    borderTopWidth: 1,
    borderTopColor: theme.border,
    backgroundColor: theme.bgElevated,
    paddingHorizontal: 16,
    paddingTop: 12
  },
  progressRow: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: MIN_TAP_PX },
  progressText: { color: theme.textSoft, fontSize: 14, flex: 1, lineHeight: 20 },
  primary: {
    backgroundColor: theme.primary,
    borderRadius: 12,
    paddingVertical: 16,
    alignItems: "center"
  },
  primaryText: { color: theme.onPrimary, fontSize: 17, fontWeight: "700" },
  secondary: {
    minHeight: MIN_TAP_PX,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: theme.primary,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 12
  },
  secondaryText: { color: theme.primary, fontSize: 16, fontWeight: "700" },
  disabled: { opacity: 0.5 }
});
