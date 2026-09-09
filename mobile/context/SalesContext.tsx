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

import { useAuth } from "@/context/AuthContext";
import { listSales } from "@/lib/api";
import { saleNameNeedsUpdate } from "@/lib/lotsReload";
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
  const { token, selectedSaleId, selectedSaleName, setSelectedSale } = useAuth();
  const [sales, setSales] = useState<Sale[]>([]);
  const [loading, setLoading] = useState(false);
  const selectedSaleNameRef = useRef(selectedSaleName);
  selectedSaleNameRef.current = selectedSaleName;

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

      if (selectedSaleId) {
        const current = open.find((s) => s.id === selectedSaleId);
        if (current && saleNameNeedsUpdate(selectedSaleNameRef.current, current.name)) {
          await setSelectedSale(current.id, current.name);
        }
      }
    } catch {
      /* keep cached names for offline queue display */
    } finally {
      setLoading(false);
    }
  }, [token, selectedSaleId, setSelectedSale]);

  // Load once per token/sale — do not use useFocusEffect here. That hook re-ran on every
  // screen focus (including the add-photos camera) and, when the stored sale name differed
  // from the API, retriggered itself about once per fetch (~1s flicker on some phones).
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
