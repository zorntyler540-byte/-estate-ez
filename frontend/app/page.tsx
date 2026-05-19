"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE, apiFetch, isApiBaseMisconfiguredForHost } from "../lib/api";

export default function HomePage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [accountName, setAccountName] = useState("My Estate Team");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [configWarning, setConfigWarning] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (isApiBaseMisconfiguredForHost(window.location.hostname)) {
      setConfigWarning(
        `This page is on ${window.location.origin} but the app is configured to call ${API_BASE}. Sign-in will fail on phones and other devices until the web app is rebuilt with NEXT_PUBLIC_API_BASE set to your public API URL (see DEPLOY.md).`
      );
    }
  }, []);

  async function submit() {
    setBusy(true);
    setError("");
    const trimmedEmail = email.trim().toLowerCase();
    try {
      const payload =
        mode === "register"
          ? { account_name: accountName, email: trimmedEmail, password }
          : { email: trimmedEmail, password };
      const data = (await apiFetch(`/auth/${mode}`, undefined, {
        method: "POST",
        body: JSON.stringify(payload)
      })) as { access_token?: string };
      const token = data?.access_token;
      if (!token || typeof token !== "string") {
        setError("Server did not return a sign-in token. Check API configuration.");
        return;
      }
      try {
        localStorage.setItem("token", token);
      } catch {
        setError(
          "Could not save your session in this browser (private mode or storage blocked). Try a normal browser tab or turn off strict tracking prevention for this site."
        );
        return;
      }
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
          <button
            type="submit"
            className="ct-btn ct-btn--primary"
            disabled={busy || !email.trim() || !password}
          >
            {busy ? "Working…" : mode === "register" ? "Create account" : "Sign in"}
          </button>
        </form>
        {configWarning ? (
          <p className="ct-banner ct-banner--error" style={{ marginBottom: 0, fontSize: "0.88rem" }}>
            {configWarning}
          </p>
        ) : null}
        {error ? (
          <p className="ct-banner ct-banner--error" style={{ marginBottom: 0, fontSize: "0.88rem" }}>
            {error}
          </p>
        ) : null}
      </div>
    </main>
  );
}

