import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode
} from "react";

import { login as apiLogin, getAuthMe } from "@/lib/api";
import { SELECTED_SALE_KEY, SELECTED_SALE_NAME_KEY } from "@/lib/config";
import { selectedSaleUnchanged } from "@/lib/lotsReload";
import { normalizeSaleName } from "@/lib/selectedSale";
import { secureDelete, secureGet, secureSet } from "@/lib/secureStorage";
import { registerSessionRefresher } from "@/lib/sessionBridge";

const TOKEN_KEY = "ct_mobile_access_token";
/** Email + password for silent re-login when the JWT expires (field sessions). */
const CREDENTIALS_KEY = "ct_mobile_login_credentials";
/** Refresh when fewer than this many ms remain on the JWT. */
const REFRESH_SKEW_MS = 12 * 60 * 60 * 1000; // 12 hours

type StoredCredentials = {
  email: string;
  password: string;
};

type AuthContextValue = {
  token: string | null;
  ready: boolean;
  /** Email used to sign in — shown on Capture so staff know whose session this is. */
  signedInEmail: string | null;
  /** Account role from ``GET /auth/me`` — owners see team activity on Sale. */
  signedInRole: string | null;
  selectedSaleId: string | null;
  /** Sale name matching ``selectedSaleId`` — shown on capture/queue so uploads go where the user expects. */
  selectedSaleName: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  setSelectedSale: (saleId: string | null, saleName?: string | null) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function decodeJwtExpMs(token: string): number | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const padded = part.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (part.length % 4)) % 4);
    const json = JSON.parse(globalThis.atob(padded)) as { exp?: unknown };
    return typeof json.exp === "number" ? json.exp * 1000 : null;
  } catch {
    return null;
  }
}

function tokenNeedsRefresh(token: string): boolean {
  const expMs = decodeJwtExpMs(token);
  if (expMs == null) return false;
  return expMs - Date.now() < REFRESH_SKEW_MS;
}

async function readStoredCredentials(): Promise<StoredCredentials | null> {
  const raw = await secureGet(CREDENTIALS_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredCredentials>;
    const email = typeof parsed.email === "string" ? parsed.email.trim() : "";
    const password = typeof parsed.password === "string" ? parsed.password : "";
    if (!email || !password) return null;
    return { email, password };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [signedInEmail, setSignedInEmail] = useState<string | null>(null);
  const [signedInRole, setSignedInRole] = useState<string | null>(null);
  const [selectedSaleId, setSelectedSaleIdState] = useState<string | null>(null);
  const [selectedSaleName, setSelectedSaleNameState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const tokenRef = useRef<string | null>(null);
  const selectedSaleIdRef = useRef<string | null>(null);
  const selectedSaleNameRef = useRef<string | null>(null);
  const refreshInFlight = useRef<Promise<string | null> | null>(null);
  selectedSaleIdRef.current = selectedSaleId;
  selectedSaleNameRef.current = selectedSaleName;

  const persistToken = useCallback(async (next: string | null) => {
    tokenRef.current = next;
    setToken(next);
    if (next) await secureSet(TOKEN_KEY, next);
    else await secureDelete(TOKEN_KEY);
  }, []);

  const loginWithPassword = useCallback(
    async (email: string, password: string): Promise<string> => {
      const res = await apiLogin(email, password);
      await secureSet(
        CREDENTIALS_KEY,
        JSON.stringify({ email: email.trim(), password } satisfies StoredCredentials)
      );
      await persistToken(res.access_token);
      setSignedInEmail(email.trim());
      return res.access_token;
    },
    [persistToken]
  );

  const silentRefresh = useCallback(async (): Promise<string | null> => {
    if (refreshInFlight.current) return refreshInFlight.current;
    refreshInFlight.current = (async () => {
      const creds = await readStoredCredentials();
      if (!creds) return null;
      try {
        return await loginWithPassword(creds.email, creds.password);
      } catch {
        return null;
      }
    })();
    try {
      return await refreshInFlight.current;
    } finally {
      refreshInFlight.current = null;
    }
  }, [loginWithPassword]);

  useEffect(() => {
    void (async () => {
      try {
        const [storedToken, storedSale, storedSaleName] = await Promise.all([
          secureGet(TOKEN_KEY),
          secureGet(SELECTED_SALE_KEY),
          secureGet(SELECTED_SALE_NAME_KEY)
        ]);
        setSelectedSaleIdState(storedSale);
        setSelectedSaleNameState(storedSaleName);
        const creds = await readStoredCredentials();
        if (creds?.email) setSignedInEmail(creds.email);

        if (storedToken) {
          tokenRef.current = storedToken;
          setToken(storedToken);
          if (tokenNeedsRefresh(storedToken)) {
            const refreshed = await silentRefresh();
            if (!refreshed) {
              // Keep the existing token if still valid; only wipe when already expired.
              if (tokenNeedsRefresh(storedToken)) {
                const expMs = decodeJwtExpMs(storedToken);
                if (expMs != null && expMs <= Date.now()) {
                  await persistToken(null);
                }
              }
            }
          }
        } else {
          // No token yet — try remembered credentials (long-lived field sessions).
          const refreshed = await silentRefresh();
          if (!refreshed) {
            tokenRef.current = null;
            setToken(null);
          }
        }
      } finally {
        setReady(true);
      }
    })();
  }, [persistToken, silentRefresh]);

  useEffect(() => {
    if (!token) {
      setSignedInRole(null);
      return;
    }
    let cancelled = false;
    void getAuthMe(token)
      .then((me) => {
        if (cancelled) return;
        setSignedInRole(me.role || null);
        if (me.email) setSignedInEmail(me.email);
      })
      .catch(() => {
        if (!cancelled) setSignedInRole(null);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    registerSessionRefresher(async () => silentRefresh());
    return () => registerSessionRefresher(null);
  }, [silentRefresh]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      await loginWithPassword(email, password);
    },
    [loginWithPassword]
  );

  const signOut = useCallback(async () => {
    await Promise.all([
      secureDelete(TOKEN_KEY),
      secureDelete(CREDENTIALS_KEY),
      secureDelete(SELECTED_SALE_KEY),
      secureDelete(SELECTED_SALE_NAME_KEY)
    ]);
    tokenRef.current = null;
    setToken(null);
    setSignedInEmail(null);
    setSignedInRole(null);
    setSelectedSaleIdState(null);
    setSelectedSaleNameState(null);
  }, []);

  const setSelectedSale = useCallback(async (saleId: string | null, saleName?: string | null) => {
    const name = saleId ? normalizeSaleName(saleName) : null;
    // Same sale + same name must not broadcast a new auth value — that re-rendered every screen
    // and looked like a 1s refresh loop on lot details / Lots.
    if (selectedSaleUnchanged(selectedSaleIdRef.current, selectedSaleNameRef.current, saleId, name)) {
      return;
    }
    if (saleId) {
      await secureSet(SELECTED_SALE_KEY, saleId);
      if (name) {
        await secureSet(SELECTED_SALE_NAME_KEY, name);
      } else {
        await secureDelete(SELECTED_SALE_NAME_KEY);
      }
    } else {
      await secureDelete(SELECTED_SALE_KEY);
      await secureDelete(SELECTED_SALE_NAME_KEY);
    }
    selectedSaleIdRef.current = saleId;
    selectedSaleNameRef.current = name;
    setSelectedSaleIdState(saleId);
    setSelectedSaleNameState(name);
  }, []);

  const value = useMemo(
    () => ({
      token,
      ready,
      signedInEmail,
      signedInRole,
      selectedSaleId,
      selectedSaleName,
      signIn,
      signOut,
      setSelectedSale
    }),
    [token, ready, signedInEmail, signedInRole, selectedSaleId, selectedSaleName, signIn, signOut, setSelectedSale]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
