import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View
} from "react-native";

import { useAuth } from "@/context/AuthContext";
import { listSales } from "@/lib/api";
import type { Sale } from "@/lib/types";

export default function SaleScreen() {
  const { token, selectedSaleId, setSelectedSale } = useAuth();
  const [sales, setSales] = useState<Sale[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      const rows = await listSales(token);
      setSales(rows.filter((s) => !s.closed_at));
      // Backfill/refresh the stored sale name for installs that saved only the id.
      const current = selectedSaleId ? rows.find((s) => s.id === selectedSaleId) : undefined;
      if (current) {
        void setSelectedSale(current.id, current.name);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load sales");
    } finally {
      setLoading(false);
    }
  }, [token, selectedSaleId, setSelectedSale]);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load])
  );

  return (
    <View style={styles.root}>
      <Text style={styles.lead}>
        Open sales from your account. Capture uploads and the Lots tab use the selected sale.
      </Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading ? (
        <ActivityIndicator style={{ marginTop: 24 }} />
      ) : (
        <FlatList
          data={sales}
          keyExtractor={(item) => item.id}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void load()} />}
          ListEmptyComponent={<Text style={styles.empty}>No open sales.</Text>}
          renderItem={({ item }) => {
            const active = item.id === selectedSaleId;
            return (
              <Pressable
                style={[styles.row, active && styles.rowActive]}
                onPress={() => void setSelectedSale(item.id, item.name)}
              >
                <Text style={styles.rowTitle}>{item.name}</Text>
                <Text style={styles.rowMeta}>{active ? "Selected" : "Tap to select"}</Text>
              </Pressable>
            );
          }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 16, backgroundColor: "#020617" },
  lead: { color: "#94a3b8", fontSize: 14, marginBottom: 12 },
  error: { color: "#fca5a5", marginBottom: 8 },
  empty: { color: "#64748b", textAlign: "center", marginTop: 32 },
  row: {
    padding: 14,
    borderRadius: 10,
    backgroundColor: "#0f172a",
    borderWidth: 1,
    borderColor: "#1e293b",
    marginBottom: 8
  },
  rowActive: { borderColor: "#2dd4bf" },
  rowTitle: { color: "#f8fafc", fontSize: 16, fontWeight: "600" },
  rowMeta: { color: "#64748b", fontSize: 12, marginTop: 4 }
});
