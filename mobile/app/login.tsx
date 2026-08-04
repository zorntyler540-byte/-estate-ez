import { router } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import { BrandLogo } from "@/components/BrandLogo";
import { theme } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";
import { API_BASE } from "@/lib/config";

export default function LoginScreen() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit() {
    setError("");
    setBusy(true);
    try {
      await signIn(email.trim(), password);
      router.replace("/(tabs)/capture");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.brand}>
        <BrandLogo size={140} />
      </View>
      <Text style={styles.kicker}>Estate-EZ</Text>
      <Text style={styles.title}>Sign in</Text>
      <Text style={styles.sub}>Manage lots, photos, and AI-assisted cataloging.</Text>
      <Text style={styles.api} numberOfLines={2}>
        API: {API_BASE}
      </Text>

      <TextInput
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        placeholder="Email"
        placeholderTextColor={theme.textSubtle}
        style={styles.input}
        value={email}
        onChangeText={setEmail}
      />
      <TextInput
        secureTextEntry
        placeholder="Password"
        placeholderTextColor={theme.textSubtle}
        style={styles.input}
        value={password}
        onChangeText={setPassword}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable
        style={[styles.button, busy && styles.buttonDisabled]}
        disabled={busy || !email.trim() || !password}
        onPress={() => void onSubmit()}
      >
        {busy ? (
          <ActivityIndicator color={theme.onPrimary} />
        ) : (
          <Text style={styles.buttonText}>Sign in</Text>
        )}
      </Pressable>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 24, justifyContent: "center", gap: 12, backgroundColor: theme.bg },
  brand: { alignItems: "center", marginBottom: 8 },
  kicker: {
    fontSize: 32,
    fontWeight: "800",
    color: theme.primary,
    letterSpacing: -0.5,
    textAlign: "center"
  },
  title: { fontSize: 17, fontWeight: "600", color: theme.text, textAlign: "center" },
  sub: { fontSize: 14, color: theme.textMuted, marginBottom: 8, textAlign: "center" },
  api: { fontSize: 11, color: theme.textSubtle, marginBottom: 12 },
  input: {
    borderWidth: 1,
    borderColor: theme.borderStrong,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    color: theme.text,
    backgroundColor: theme.bgElevated
  },
  button: {
    marginTop: 8,
    backgroundColor: theme.primary,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: theme.onPrimary, fontSize: 16, fontWeight: "700" },
  error: { color: theme.error, fontSize: 14 }
});
