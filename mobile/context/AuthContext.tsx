import * as SecureStore from "expo-secure-store";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from "react";

import { login as apiLogin } from "@/lib/api";
import { SELECTED_SALE_KEY } from "@/lib/config";

const TOKEN_KEY = "ct_mobile_access_token";

type AuthContextValue = {
  token: string | null;
  ready: boolean;
  selectedSaleId: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  setSelectedSaleId: (saleId: string | null) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [selectedSaleId, setSelectedSaleIdState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [storedToken, storedSale] = await Promise.all([
          SecureStore.getItemAsync(TOKEN_KEY),
          SecureStore.getItemAsync(SELECTED_SALE_KEY)
        ]);
        setToken(storedToken);
        setSelectedSaleIdState(storedSale);
      } finally {
        setReady(true);
      }
    })();
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password);
    await SecureStore.setItemAsync(TOKEN_KEY, res.access_token);
    setToken(res.access_token);
  }, []);

  const signOut = useCallback(async () => {
    await Promise.all([
      SecureStore.deleteItemAsync(TOKEN_KEY),
      SecureStore.deleteItemAsync(SELECTED_SALE_KEY)
    ]);
    setToken(null);
    setSelectedSaleIdState(null);
  }, []);

  const setSelectedSaleId = useCallback(async (saleId: string | null) => {
    if (saleId) {
      await SecureStore.setItemAsync(SELECTED_SALE_KEY, saleId);
    } else {
      await SecureStore.deleteItemAsync(SELECTED_SALE_KEY);
    }
    setSelectedSaleIdState(saleId);
  }, []);

  const value = useMemo(
    () => ({ token, ready, selectedSaleId, signIn, signOut, setSelectedSaleId }),
    [token, ready, selectedSaleId, signIn, signOut, setSelectedSaleId]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
