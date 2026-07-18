import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { countPendingDrafts } from "@/lib/queue/db";

export default function CaptureScreen() {
  const { selectedSaleId, selectedSaleName, signOut } = useAuth();
  const { getSaleName } = useSales();
  const [pending, setPending] = useState(0);

  useFocusEffect(
    useCallback(() => {
      void countPendingDrafts().then(setPending);
    }, [])
  );

  function startCapture() {
    if (!selectedSaleId) {
      router.push("/(tabs)/sale");
      return;
    }
    router.push("/capture-session");
  }

  return (
    <View style={styles.root}>
      <Text style={styles.lead}>
        Native camera capture with offline storage. Queued lots upload automatically when you're online.
      </Text>

      {!selectedSaleId ? (
        <Text style={styles.warn}>Choose a sale on the Sale tab before capturing.</Text>
      ) : (
        <Text style={styles.saleBanner}>
          Uploading to: <Text style={styles.saleName}>{getSaleName(selectedSaleId, selectedSaleName)}</Text>
          {"\n"}
          <Text style={styles.meta}>Wrong sale? Change it on the Sale tab before capturing.</Text>
        </Text>
      )}

      <Pressable style={styles.primary} onPress={startCapture}>
        <Text style={styles.primaryText}>Start lot capture</Text>
      </Pressable>

      <Text style={styles.meta}>
        {pending} lot{pending === 1 ? "" : "s"} waiting locally (draft / queued / uploading / failed)
      </Text>

      <Pressable style={styles.link} onPress={() => void signOut()}>
        <Text style={styles.linkText}>Sign out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 20, gap: 16, backgroundColor: "#020617" },
  lead: { fontSize: 15, lineHeight: 22, color: "#cbd5e1" },
  warn: { color: "#fcd34d", fontSize: 14 },
  meta: { color: "#94a3b8", fontSize: 14 },
  saleBanner: { color: "#cbd5e1", fontSize: 15, lineHeight: 22 },
  saleName: { color: "#2dd4bf", fontWeight: "700" },
  primary: {
    backgroundColor: "#0d9488",
    borderRadius: 12,
    paddingVertical: 16,
    alignItems: "center",
    marginTop: 8
  },
  primaryText: { color: "#fff", fontSize: 17, fontWeight: "600" },
  link: { marginTop: 24, alignSelf: "flex-start" },
  linkText: { color: "#64748b", fontSize: 14 }
});
