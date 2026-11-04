import { useFocusEffect } from "expo-router";
import { useCallback, useRef } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  View
} from "react-native";
import { Text } from "@/components/AppText";

import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { shouldSkipLotsFocusReload } from "@/lib/lotsReload";
import { theme } from "@/constants/theme";

export default function SaleScreen() {
  const { selectedSaleId, setSelectedSale } = useAuth();
  const { sales, loading, refreshSales } = useSales();
  const lastFocusRefreshAtRef = useRef(0);
  const salesLenRef = useRef(0);
  salesLenRef.current = sales.length;

  useFocusEffect(
    useCallback(() => {
      if (shouldSkipLotsFocusReload(lastFocusRefreshAtRef.current, Date.now(), salesLenRef.current > 0)) {
        return;
      }
      lastFocusRefreshAtRef.current = Date.now();
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
            <RefreshControl refreshing={loading} onRefresh={() => void refreshSales()} tintColor={theme.primary} />
          }
          ListEmptyComponent={<Text style={styles.empty}>No open sales.</Text>}
          renderItem={({ item }) => {
            const active = item.id === selectedSaleId;
            return (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${item.name}${active ? ", selected" : ""}`}
                accessibilityState={{ selected: active }}
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
  root: { flex: 1, padding: 16, backgroundColor: theme.bg },
  lead: { color: theme.textMuted, fontSize: 14, marginBottom: 12 },
  empty: { color: theme.textSubtle, textAlign: "center", marginTop: 32 },
  row: {
    padding: 14,
    borderRadius: 10,
    backgroundColor: theme.bgElevated,
    borderWidth: 1,
    borderColor: theme.border,
    marginBottom: 8
  },
  rowActive: { borderColor: theme.primary },
  rowTitle: { color: theme.text, fontSize: 16, fontWeight: "600" },
  rowMeta: { color: theme.textSubtle, fontSize: 12, marginTop: 4 }
});
