import { ScrollView, StyleSheet, View } from "react-native";
import { Text } from "@/components/AppText";

import { TeamActivityPanel } from "@/components/TeamActivityPanel";
import { useAuth } from "@/context/AuthContext";
import { theme } from "@/constants/theme";

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
  root: { flex: 1, backgroundColor: theme.bg },
  content: { padding: 16, paddingBottom: 32 },
  lead: { color: theme.textMuted, fontSize: 14, padding: 16 }
});
