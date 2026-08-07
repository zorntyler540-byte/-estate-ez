import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, View } from "react-native";

import { BrandLogo } from "@/components/BrandLogo";
import { theme } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { countPendingDrafts } from "@/lib/queue/db";

export default function CaptureScreen() {
  const { selectedSaleId, selectedSaleName, signOut } = useAuth();
  const { getSaleName } = useSales();
  const [pending, setPending] = useState(0);
  const [signingOut, setSigningOut] = useState(false);

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

  function confirmSignOut() {
    if (signingOut) return;
    Alert.alert(
      "Sign out?",
      "You'll need your email and password to sign back in. Local queued photos on this phone stay until you delete them.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Sign out",
          style: "destructive",
          onPress: () => {
            void (async () => {
              setSigningOut(true);
              try {
                await signOut();
                router.replace("/login");
              } finally {
                setSigningOut(false);
              }
            })();
          }
        }
      ]
    );
  }

  return (
    <View style={styles.root}>
      <View style={styles.brand}>
        <BrandLogo size={96} />
      </View>

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
        {pending} lot{pending === 1 ? "" : "s"} waiting locally (draft / queued / failed)
      </Text>

      <Pressable
        style={[styles.signOut, signingOut && styles.signOutDisabled]}
        disabled={signingOut}
        onPress={confirmSignOut}
      >
        <Text style={styles.signOutText}>{signingOut ? "Signing out…" : "Sign out"}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 20, gap: 16, backgroundColor: theme.bg },
  brand: { alignItems: "center", marginBottom: 4 },
  lead: { fontSize: 15, lineHeight: 22, color: theme.textMuted },
  warn: { color: theme.warning, fontSize: 14 },
  meta: { color: theme.textMuted, fontSize: 14 },
  saleBanner: { color: theme.textMuted, fontSize: 15, lineHeight: 22 },
  saleName: { color: theme.primary, fontWeight: "700" },
  primary: {
    backgroundColor: theme.primary,
    borderRadius: 12,
    paddingVertical: 16,
    alignItems: "center",
    marginTop: 8
  },
  primaryText: { color: theme.onPrimary, fontSize: 17, fontWeight: "700" },
  signOut: {
    marginTop: 24,
    alignSelf: "flex-start",
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.borderStrong,
    backgroundColor: theme.bgElevated
  },
  signOutDisabled: { opacity: 0.55 },
  signOutText: { color: theme.text, fontSize: 15, fontWeight: "600" }
});
