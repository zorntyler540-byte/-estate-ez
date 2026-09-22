import { ScrollView, StyleSheet, Text, View } from "react-native";

import { TeamActivityPanel } from "@/components/TeamActivityPanel";
import { useAuth } from "@/context/AuthContext";

export default function TeamActivityScreen() {
  const { token, signedInRole } = useAuth();
  const isOwner = (signedInRole || "").toLowerCase() === "owner";

  if (!isOwner || !token) {
    return (
      <View style={styles.root}>
        <Text style={styles.lead}>Team activity is for account owners.</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <TeamActivityPanel token={token} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#070708" },
  content: { padding: 16, paddingBottom: 32 },
  lead: { color: "#a8a29e", fontSize: 14, padding: 16 }
});
