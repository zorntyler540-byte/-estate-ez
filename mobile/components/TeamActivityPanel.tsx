import { Image } from "expo-image";
import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import {
  getTeamActivity,
  lotThumbnailSource,
  type TeamActivity,
  type TeamActivityLot
} from "@/lib/api";

type Days = 1 | 7 | 30;
type Panel = "catalog" | "sales";
const PHONE_LOT_CAP = 12;

function fmtMinutes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1) return `${Math.max(1, Math.round(n * 60))}s`;
  if (n < 10) return `${n.toFixed(1)} min`;
  return `${Math.round(n)} min`;
}

function fmtHours(n: number | undefined): string {
  if (!n) return "—";
  return n.toFixed(n >= 10 ? 1 : 2);
}

function fmtHammer(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n) || n <= 0) return "—";
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function fmtClock(iso: string, withSeconds: boolean): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {})
  });
}

function fmtGap(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const min = sec / 60;
  if (min < 10) return `${min.toFixed(1)} min`;
  return `${Math.round(min)} min`;
}

function fmtLotTime(lot: TeamActivityLot): string {
  const n = lot.photo_count || 0;
  const photos = `${n} ${n === 1 ? "photo" : "photos"}`;
  if (lot.timing_source !== "camera") return `${photos} · no shutter time`;
  const first = lot.first_photo_at;
  const last = lot.last_photo_at;
  let range = "";
  if (first) {
    const a = new Date(first).getTime();
    const b = new Date(last || first).getTime();
    const withSeconds = Number.isFinite(a) && Number.isFinite(b) && Math.abs(b - a) < 2 * 60 * 1000;
    const start = fmtClock(first, withSeconds);
    const end = fmtClock(last || first, withSeconds);
    range = start && end && start !== end ? `${start}–${end}` : start;
  }
  const dur = lot.capture_minutes == null ? "" : `${fmtMinutes(lot.capture_minutes)} shoot`;
  return [photos, dur, range].filter(Boolean).join(" · ");
}

function fmtPhotoTimes(lot: TeamActivityLot): string {
  const times = (lot.photo_times || []).filter((t) => (t.source || "camera") === "camera");
  if (lot.timing_source !== "camera" || !times.length) return "";
  if (times.length === 1) return `Shutter ${fmtClock(times[0].at, true)}`;
  const gaps: string[] = [];
  for (let i = 1; i < times.length; i += 1) {
    const a = new Date(times[i - 1].at).getTime();
    const b = new Date(times[i].at).getTime();
    if (Number.isNaN(a) || Number.isNaN(b)) continue;
    gaps.push(fmtGap(b - a));
  }
  if (!gaps.length) return "";
  return `Between photos: ${gaps.join(" · ")}`;
}

function LotChip({ lot, token }: { lot: TeamActivityLot; token: string }) {
  const source = lot.has_thumbnail === false ? null : lotThumbnailSource(lot.lot_id, token, 160);
  function openLot() {
    router.push(`/lot/${lot.lot_id}`);
  }
  return (
    <View style={styles.lotChip}>
      <Pressable onPress={openLot}>
        {source ? (
          <Image source={source} style={styles.lotThumb} contentFit="cover" cachePolicy="memory-disk" />
        ) : (
          <View style={[styles.lotThumb, styles.lotThumbEmpty]} />
        )}
        <Text style={styles.lotSku} numberOfLines={1}>
          {lot.sku?.trim() || "No SKU"}
        </Text>
        <Text style={styles.lotMeta} numberOfLines={3}>
          {fmtLotTime(lot)}
        </Text>
        {fmtPhotoTimes(lot) ? (
          <Text style={styles.lotTimes} numberOfLines={6}>
            {fmtPhotoTimes(lot)}
          </Text>
        ) : null}
      </Pressable>
      <Pressable style={styles.lotOpen} onPress={openLot} accessibilityRole="button" accessibilityLabel="Open lot">
        <Text style={styles.lotOpenText}>Open lot</Text>
      </Pressable>
    </View>
  );
}

export function TeamActivityPanel({ token }: { token: string }) {
  const [days, setDays] = useState<Days>(1);
  const [panel, setPanel] = useState<Panel>("catalog");
  const [row, setRow] = useState<TeamActivity | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(
    async (windowDays: Days) => {
      setLoading(true);
      setError("");
      try {
        const next = await getTeamActivity(token, windowDays);
        setRow(next);
      } catch (e) {
        setRow(null);
        setError(e instanceof Error ? e.message : "Could not load activity");
      } finally {
        setLoading(false);
      }
    },
    [token]
  );

  useEffect(() => {
    void load(days);
  }, [load, days]);

  const selectDays = (windowDays: Days) => {
    setDays(windowDays);
  };

  return (
    <View style={styles.box}>
      <Text style={styles.title}>{panel === "sales" ? "Sale results" : "Cataloging"}</Text>
      <Text style={styles.hint}>
        {panel === "sales"
          ? "Posted lots in this window: sold, unsold, or still live, with hammer. Today is often empty until a sale closes."
          : "Photos, lots, and shutter time between pictures. Upload clock is not used here."}
      </Text>
      <View style={styles.chips}>
        {(
          [
            ["catalog", "Cataloging"],
            ["sales", "Sale results"]
          ] as const
        ).map(([id, label]) => (
          <Pressable
            key={id}
            style={[styles.chip, panel === id && styles.chipOn]}
            onPress={() => setPanel(id)}
          >
            <Text style={[styles.chipText, panel === id && styles.chipTextOn]}>{label}</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.chips}>
        {(
          [
            [1, "Today"],
            [7, "7 days"],
            [30, "30 days"]
          ] as const
        ).map(([n, label]) => (
          <Pressable
            key={n}
            style={[styles.chip, days === n && styles.chipOn]}
            onPress={() => selectDays(n)}
          >
            <Text style={[styles.chipText, days === n && styles.chipTextOn]}>{label}</Text>
          </Pressable>
        ))}
        <Pressable style={styles.chip} onPress={() => void load(days)} disabled={loading}>
          <Text style={styles.chipText}>{loading ? "…" : "Refresh"}</Text>
        </Pressable>
      </View>
      {error ? <Text style={styles.err}>{error}</Text> : null}
      {loading && !row ? <ActivityIndicator color="#ff4d1a" style={{ marginTop: 8 }} /> : null}
      {!row && !loading && !error ? (
        <Text style={styles.meta}>No activity in this window.</Text>
      ) : null}
      {row && panel === "catalog" ? (
        <>
          <Text style={styles.meta}>
            {row.totals.photos_uploaded} photos · {row.totals.lots_photographed ?? 0} lots shot
            {row.totals.median_minutes_per_lot != null
              ? ` · median ${fmtMinutes(row.totals.median_minutes_per_lot)} shutter/lot`
              : ""}
          </Text>
          {row.members.map((m) => (
            <View key={m.user_id} style={styles.person}>
              <Text style={styles.email} numberOfLines={1}>
                {m.email}
              </Text>
              <Text style={styles.counts}>
                {m.photos_uploaded} photos · {m.lots_created} lots · {m.lots_photographed ?? 0} shot
              </Text>
              <Text style={styles.meta}>
                {fmtHours(m.active_hours)} active hrs · {m.photos_per_hour || 0} photos/hr
                {m.median_minutes_per_lot != null
                  ? ` · median ${fmtMinutes(m.median_minutes_per_lot)} shutter/lot`
                  : ""}
              </Text>
              {(m.lots || []).length > 0 ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.lotRow}>
                  {(m.lots || []).slice(0, PHONE_LOT_CAP).map((lot) => (
                    <LotChip key={lot.lot_id} lot={lot} token={token} />
                  ))}
                </ScrollView>
              ) : null}
            </View>
          ))}
        </>
      ) : null}
      {row && panel === "sales" ? (
        <>
          <Text style={styles.meta}>
            {row.totals.lots_posted ?? 0} posted · {row.totals.lots_sold ?? 0} sold ·{" "}
            {row.totals.lots_unsold ?? 0} unsold · {row.totals.lots_live ?? 0} live
            {row.totals.sold_revenue ? ` · ${fmtHammer(row.totals.sold_revenue)} hammer` : ""}
          </Text>
          {(row.sold_items || []).length === 0 ? (
            <Text style={styles.meta}>
              No posted lots in this date range yet. Try 7 or 30 days after a sale closes, then
              Refresh.
            </Text>
          ) : (
            <>
              {(row.sold_by_category || []).map((cat) => (
                <Text key={cat.category} style={styles.counts}>
                  {cat.category} · {cat.posted_count ?? cat.sold_count} posted · {cat.sold_count}{" "}
                  sold · {cat.unsold_count ?? 0} unsold
                  {cat.revenue ? ` · ${fmtHammer(cat.revenue)}` : ""}
                </Text>
              ))}
              {(row.sold_items || []).slice(0, 40).map((item) => (
                <Pressable
                  key={item.lot_id}
                  onPress={() => router.push(`/lot/${item.lot_id}`)}
                  style={styles.saleRow}
                >
                  <Text style={styles.email} numberOfLines={1}>
                    {item.sku?.trim() || "No SKU"} · {item.outcome || "—"}
                    {item.outcome === "sold" ? ` · ${fmtHammer(item.sold_price)}` : ""}
                  </Text>
                  <Text style={styles.meta} numberOfLines={2}>
                    {item.category}
                    {item.auction_title ? ` · ${item.auction_title}` : ""}
                  </Text>
                </Pressable>
              ))}
            </>
          )}
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    marginBottom: 0,
    padding: 12,
    borderRadius: 10,
    backgroundColor: "#0e0e10",
    borderWidth: 1,
    borderColor: "#141416"
  },
  title: { color: "#f5f5f4", fontSize: 16, fontWeight: "700" },
  hint: { color: "#a8a29e", fontSize: 12, marginTop: 6, lineHeight: 17 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 10 },
  chip: {
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#292524"
  },
  chipOn: { borderColor: "#ff4d1a", backgroundColor: "#2a120c" },
  chipText: { color: "#a8a29e", fontSize: 13, fontWeight: "600" },
  chipTextOn: { color: "#ff4d1a" },
  err: { color: "#f87171", fontSize: 13, marginTop: 8 },
  meta: { color: "#78716c", fontSize: 12, marginTop: 8 },
  person: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#1c1917"
  },
  email: { color: "#f5f5f4", fontSize: 14, fontWeight: "600" },
  counts: { color: "#d6d3d1", fontSize: 13, marginTop: 4 },
  saleRow: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#1c1917"
  },
  lotRow: { marginTop: 10, marginHorizontal: -4 },
  lotChip: { width: 120, marginRight: 8 },
  lotThumb: {
    width: 120,
    height: 120,
    borderRadius: 8,
    backgroundColor: "#1c1917"
  },
  lotThumbEmpty: { borderWidth: 1, borderColor: "#292524" },
  lotSku: { color: "#f5f5f4", fontSize: 12, fontWeight: "700", marginTop: 4 },
  lotMeta: { color: "#78716c", fontSize: 11, marginTop: 2 },
  lotTimes: { color: "#78716c", fontSize: 10, marginTop: 2, lineHeight: 13 },
  lotOpen: {
    marginTop: 6,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#292524",
    backgroundColor: "#141416",
    alignItems: "center"
  },
  lotOpenText: { color: "#f5f5f4", fontSize: 12, fontWeight: "700" }
});
