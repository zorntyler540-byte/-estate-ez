import { useFocusEffect } from "expo-router";
import { useCallback } from "react";
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
import { useSales } from "@/context/SalesContext";

export default function SaleScreen() {
  const { selectedSaleId, setSelectedSale } = useAuth();
  const { sales, loading, refreshSales } = useSales();

  useFocusEffect(
    useCallback(() => {
      void refreshSales();
    }, [refreshSales])
  );

  const showInitialSpinner = loading && sales.length === 0;

  return (
    <View style={styles.root}>
      <Text style={styles.lead}>
        Open sales from your account. Capture uploads and the Lots tab use the selected sale.
      </Text>
      {showInitialSpinner ? (
        <ActivityIndicator style={{ marginTop: 24 }} />
      ) : (
        <FlatList
          data={sales}
          keyExtractor={(item) => item.id}
          refreshControl={
            <RefreshControl refreshing={loading} onRefresh={() => void refreshSales()} tintColor="#ff4d1a" />
          }
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
  root: { flex: 1, padding: 16, backgroundColor: "#070708" },
  lead: { color: "#a8a29e", fontSize: 14, marginBottom: 12 },
  empty: { color: "#78716c", textAlign: "center", marginTop: 32 },
  row: {
    padding: 14,
    borderRadius: 10,
    backgroundColor: "#0e0e10",
    borderWidth: 1,
    borderColor: "#141416",
    marginBottom: 8
  },
  rowActive: { borderColor: "#ff4d1a" },
  rowTitle: { color: "#f5f5f4", fontSize: 16, fontWeight: "600" },
  rowMeta: { color: "#78716c", fontSize: 12, marginTop: 4 }
});
