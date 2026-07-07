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
      <Text style={styles.kicker}>Caring Transitions</Text>
      <Text style={styles.title}>Field capture</Text>
      <Text style={styles.sub}>Sign in with the same account as the web dashboard.</Text>
      <Text style={styles.api} numberOfLines={2}>
        API: {API_BASE}
      </Text>

      <TextInput
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        placeholder="Email"
        style={styles.input}
        value={email}
        onChangeText={setEmail}
      />
      <TextInput
        secureTextEntry
        placeholder="Password"
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
        {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Sign in</Text>}
      </Pressable>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 24, justifyContent: "center", gap: 12 },
  kicker: { fontSize: 13, fontWeight: "600", color: "#2dd4bf", textTransform: "uppercase" },
  title: { fontSize: 28, fontWeight: "700", color: "#f8fafc" },
  sub: { fontSize: 15, color: "#94a3b8", marginBottom: 8 },
  api: { fontSize: 11, color: "#64748b", marginBottom: 12 },
  input: {
    borderWidth: 1,
    borderColor: "#334155",
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    color: "#f8fafc",
    backgroundColor: "#0f172a"
  },
  button: {
    marginTop: 8,
    backgroundColor: "#0d9488",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  error: { color: "#fca5a5", fontSize: 14 }
});
