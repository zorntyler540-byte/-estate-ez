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
import { SELECTED_SALE_KEY, SELECTED_SALE_NAME_KEY } from "@/lib/config";

const TOKEN_KEY = "ct_mobile_access_token";

type AuthContextValue = {
  token: string | null;
  ready: boolean;
  selectedSaleId: string | null;
  /** Sale name matching ``selectedSaleId`` — shown on capture/queue so uploads go where the user expects. */
  selectedSaleName: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  setSelectedSale: (saleId: string | null, saleName?: string | null) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [selectedSaleId, setSelectedSaleIdState] = useState<string | null>(null);
  const [selectedSaleName, setSelectedSaleNameState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [storedToken, storedSale, storedSaleName] = await Promise.all([
          SecureStore.getItemAsync(TOKEN_KEY),
          SecureStore.getItemAsync(SELECTED_SALE_KEY),
          SecureStore.getItemAsync(SELECTED_SALE_NAME_KEY)
        ]);
        setToken(storedToken);
        setSelectedSaleIdState(storedSale);
        setSelectedSaleNameState(storedSaleName);
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
      SecureStore.deleteItemAsync(SELECTED_SALE_KEY),
      SecureStore.deleteItemAsync(SELECTED_SALE_NAME_KEY)
    ]);
    setToken(null);
    setSelectedSaleIdState(null);
    setSelectedSaleNameState(null);
  }, []);

  const setSelectedSale = useCallback(async (saleId: string | null, saleName?: string | null) => {
    const name = saleName?.trim() || null;
    if (saleId) {
      await SecureStore.setItemAsync(SELECTED_SALE_KEY, saleId);
      if (name) {
        await SecureStore.setItemAsync(SELECTED_SALE_NAME_KEY, name);
      } else {
        await SecureStore.deleteItemAsync(SELECTED_SALE_NAME_KEY);
      }
    } else {
      await SecureStore.deleteItemAsync(SELECTED_SALE_KEY);
      await SecureStore.deleteItemAsync(SELECTED_SALE_NAME_KEY);
    }
    setSelectedSaleIdState(saleId);
    setSelectedSaleNameState(saleId ? name : null);
  }, []);

  const value = useMemo(
    () => ({ token, ready, selectedSaleId, selectedSaleName, signIn, signOut, setSelectedSale }),
    [token, ready, selectedSaleId, selectedSaleName, signIn, signOut, setSelectedSale]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
