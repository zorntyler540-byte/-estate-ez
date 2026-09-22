import { Image } from "expo-image";
import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { theme } from "@/constants/theme";
import { getTeamActivity, lotThumbnailSource, type TeamActivity, type TeamActivityLot } from "@/lib/api";

type Days = 1 | 7 | 30;
const PHONE_LOT_CAP = 12;

function fmtHours(n: number | undefined): string {
  if (!n) return "—";
  return n.toFixed(n >= 10 ? 1 : 2);
}

function fmtMoney(n: number | undefined): string {
  if (!n) return "—";
  return `$${Math.round(n)}`;
}

function fmtMinutes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1) return `${Math.max(1, Math.round(n * 60))}s`;
  if (n < 10) return `${n.toFixed(1)} min`;
  return `${Math.round(n)} min`;
}

function LotChip({ lot, token }: { lot: TeamActivityLot; token: string }) {
  const source = lot.has_thumbnail === false ? null : lotThumbnailSource(lot.lot_id, token, 160);
  return (
    <Pressable style={styles.lotChip} onPress={() => router.push(`/lot/${lot.lot_id}`)}>
      {source ? (
        <Image source={source} style={styles.lotThumb} contentFit="cover" cachePolicy="memory-disk" />
      ) : (
        <View style={[styles.lotThumb, styles.lotThumbEmpty]} />
      )}
      <Text style={styles.lotSku} numberOfLines={1}>
        {lot.sku?.trim() || "No SKU"}
      </Text>
      <Text style={styles.lotMeta} numberOfLines={1}>
        {lot.photo_count}ph · {fmtMinutes(lot.capture_minutes)}
      </Text>
    </Pressable>
  );
}

export function TeamActivityPanel({ token }: { token: string }) {
  const [days, setDays] = useState<Days>(1);
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
      <Text style={styles.title}>Team activity</Text>
      <Text style={styles.hint}>
        Photos, lots, CT Bids posted/sold, and minutes per lot (first-to-last photo upload). Owners
        only. Credit is the upload login.
      </Text>
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
      {row ? (
        <>
          <Text style={styles.meta}>
            {row.totals.photos_uploaded} photos · {row.totals.lots_created} lots ·{" "}
            {row.totals.lots_posted ?? 0} posted · {row.totals.lots_sold ?? 0} sold
            {row.totals.sold_revenue ? ` · ${fmtMoney(row.totals.sold_revenue)} hammer` : ""}
            {row.totals.median_minutes_per_lot != null
              ? ` · median ${fmtMinutes(row.totals.median_minutes_per_lot)}/lot`
              : ""}
          </Text>
          {row.members.map((m) => (
            <View key={m.user_id} style={styles.person}>
              <Text style={styles.email} numberOfLines={1}>
                {m.email}
              </Text>
              <Text style={styles.counts}>
                {m.photos_uploaded} photos · {m.lots_created} lots · {m.lots_posted ?? 0} posted ·{" "}
                {m.lots_sold ?? 0} sold / {m.lots_unsold ?? 0} unsold / {m.lots_live ?? 0} live
              </Text>
              <Text style={styles.meta}>
                {fmtHours(m.active_hours)} active hrs · {m.photos_per_hour || 0} photos/hr
                {m.median_minutes_per_lot != null
                  ? ` · median ${fmtMinutes(m.median_minutes_per_lot)}/lot`
                  : ""}
                {m.sold_revenue ? ` · ${fmtMoney(m.sold_revenue)}` : ""}
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
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    marginBottom: 16,
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
  lotRow: { marginTop: 10, marginHorizontal: -4 },
  lotChip: { width: 88, marginRight: 8 },
  lotThumb: {
    width: 88,
    height: 88,
    borderRadius: 8,
    backgroundColor: "#1c1917"
  },
  lotThumbEmpty: { borderWidth: 1, borderColor: "#292524" },
  lotSku: { color: "#f5f5f4", fontSize: 12, fontWeight: "700", marginTop: 4 },
  lotMeta: { color: "#78716c", fontSize: 11, marginTop: 2 }
});
