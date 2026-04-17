"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "../lib/api";

export default function HomePage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [accountName, setAccountName] = useState("My Estate Team");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const payload =
        mode === "register"
          ? { account_name: accountName, email, password }
          : { email, password };
      const data = await apiFetch(`/auth/${mode}`, undefined, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      localStorage.setItem("token", data.access_token);
      router.push("/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 420, margin: "20px auto" }}>
      <h1>CaringTransitions SaaS</h1>
      <p>Sign in to manage lots and photo processing.</p>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <button onClick={() => setMode("login")} disabled={mode === "login"}>
          Login
        </button>
        <button onClick={() => setMode("register")} disabled={mode === "register"}>
          Register
        </button>
      </div>
      {mode === "register" && (
        <input
          style={{ width: "100%", marginBottom: 8 }}
          value={accountName}
          onChange={(e) => setAccountName(e.target.value)}
          placeholder="Account name"
        />
      )}
      <input
        style={{ width: "100%", marginBottom: 8 }}
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email"
      />
      <input
        style={{ width: "100%", marginBottom: 8 }}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
        type="password"
      />
      <button onClick={submit} disabled={busy || !email || !password}>
        {busy ? "Working..." : mode === "register" ? "Create account" : "Sign in"}
      </button>
      {error && <p style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{error}</p>}
    </main>
  );
}

