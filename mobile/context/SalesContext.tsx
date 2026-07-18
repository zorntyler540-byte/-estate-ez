import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from "react";

import { useAuth } from "@/context/AuthContext";
import { listSales } from "@/lib/api";
import { backfillDraftSaleNames } from "@/lib/queue/db";
import type { Sale } from "@/lib/types";

type SalesContextValue = {
  sales: Sale[];
  loading: boolean;
  refreshSales: () => Promise<void>;
  getSaleName: (saleId: string, storedName?: string | null) => string;
};

const SalesContext = createContext<SalesContextValue | null>(null);

export function SalesProvider({ children }: { children: ReactNode }) {
  const { token, selectedSaleId, selectedSaleName } = useAuth();
  const [sales, setSales] = useState<Sale[]>([]);
  const [loading, setLoading] = useState(false);

  const refreshSales = useCallback(async () => {
    if (!token) {
      setSales([]);
      return;
    }
    setLoading(true);
    try {
      const rows = await listSales(token);
      const open = rows.filter((s) => !s.closed_at);
      setSales(open);

      const nameById = new Map(open.map((s) => [s.id, s.name]));
      await backfillDraftSaleNames(nameById);
    } catch {
      /* keep cached names for offline queue display */
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refreshSales();
  }, [refreshSales]);

  const nameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const sale of sales) {
      map.set(sale.id, sale.name);
    }
    return map;
  }, [sales]);

  const getSaleName = useCallback(
    (saleId: string, storedName?: string | null) => {
      if (storedName?.trim()) return storedName.trim();
      const fromApi = nameById.get(saleId);
      if (fromApi?.trim()) return fromApi.trim();
      if (saleId === selectedSaleId && selectedSaleName?.trim()) return selectedSaleName.trim();
      return "Unknown sale";
    },
    [nameById, selectedSaleId, selectedSaleName]
  );

  const value = useMemo(
    () => ({ sales, loading, refreshSales, getSaleName }),
    [sales, loading, refreshSales, getSaleName]
  );

  return <SalesContext.Provider value={value}>{children}</SalesContext.Provider>;
}

export function useSales(): SalesContextValue {
  const ctx = useContext(SalesContext);
  if (!ctx) throw new Error("useSales must be used within SalesProvider");
  return ctx;
}
