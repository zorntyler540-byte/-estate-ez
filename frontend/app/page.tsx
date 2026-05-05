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
    <main className="ct-auth-screen">
      <div className="ct-auth-card ct-card ct-card--padded ct-card--interactive ct-stack">
        <div>
          <div className="ct-hero-brand">Caring Transitions</div>
          <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
            Estate inventory
          </h1>
          <p className="ct-subtitle" style={{ marginBottom: 0 }}>
            Sign in to manage lots, photos, and AI-assisted cataloging.
          </p>
        </div>
        <div className="ct-seg" role="tablist" aria-label="Sign in or register">
          <button type="button" onClick={() => setMode("login")} disabled={mode === "login"}>
            Login
          </button>
          <button type="button" onClick={() => setMode("register")} disabled={mode === "register"}>
            Register
          </button>
        </div>
        <form
          className="ct-stack"
          style={{ gap: "0.85rem" }}
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy && email && password) void submit();
          }}
        >
          {mode === "register" && (
            <div>
              <label className="ct-label" htmlFor="account-name">
                Account name
              </label>
              <input
                id="account-name"
                className="ct-input"
                value={accountName}
                onChange={(e) => setAccountName(e.target.value)}
                placeholder="My Estate Team"
              />
            </div>
          )}
          <div>
            <label className="ct-label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              className="ct-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              inputMode="email"
            />
          </div>
          <div>
            <label className="ct-label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              className="ct-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              type="password"
              autoComplete={mode === "register" ? "new-password" : "current-password"}
            />
          </div>
          <button type="submit" className="ct-btn ct-btn--primary" disabled={busy || !email || !password}>
            {busy ? "Working…" : mode === "register" ? "Create account" : "Sign in"}
          </button>
        </form>
        {error ? <p className="ct-banner ct-banner--error" style={{ marginBottom: 0 }}>{error}</p> : null}
      </div>
    </main>
  );
}

