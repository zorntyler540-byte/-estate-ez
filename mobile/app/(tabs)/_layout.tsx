import { Ionicons } from "@expo/vector-icons";
import { Tabs, router, usePathname } from "expo-router";
import { useEffect, useState } from "react";
import { AppState } from "react-native";

import { theme } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { useSyncRefreshKey } from "@/context/SyncContext";
import { captureGate } from "@/lib/clientCapture";
import {
  captureTabOpensCamera,
  formatTabBadge,
  isTabHidden,
  queueTabAccessibilityLabel
} from "@/lib/navVisibility";
import { countQueueBadge } from "@/lib/queue/db";
import { FONT_FAMILY } from "@/lib/typography";

/** Lots still on this phone (shooting, queued, uploading, failed). */
function useQueueBadgeCount(): number {
  const refreshKey = useSyncRefreshKey();
  const pathname = usePathname();
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void countQueueBadge()
        .then((n) => {
          if (!cancelled) setCount((prev) => (prev === n ? prev : n));
        })
        .catch(() => undefined);
    };
    load();
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "active") load();
    });
    return () => {
      cancelled = true;
      sub.remove();
    };
  }, [refreshKey, pathname]);

  return count;
}

export default function TabLayout() {
  const { signedInRole, selectedSaleId, selectedSaleName } = useAuth();
  const { getSaleName } = useSales();
  const role = (signedInRole || "").toLowerCase();
  const isOwner = role === "owner";
  const isClient = role === "client";
  const queueCount = useQueueBadgeCount();
  const saleLabel = selectedSaleId ? getSaleName(selectedSaleId, selectedSaleName) : "";
  const gate = captureGate({ role: signedInRole, saleId: selectedSaleId, saleName: saleLabel });
  const hrefFor = (route: string, path: `/(tabs)/${string}`, allowed = true) =>
    allowed && !isTabHidden(route) ? path : null;
  const captureTitle = isClient ? "Photos" : "Capture";
  const opensCamera = captureTabOpensCamera(gate.action);

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textSubtle,
        tabBarStyle: {
          backgroundColor: theme.bgElevated,
          borderTopColor: theme.border
        },
        tabBarLabelStyle: { fontFamily: FONT_FAMILY.semibold, fontSize: 11 },
        tabBarBadgeStyle: {
          backgroundColor: theme.primary,
          color: theme.onPrimary,
          fontFamily: FONT_FAMILY.bold
        },
        headerStyle: { backgroundColor: theme.bgElevated },
        headerTintColor: theme.text,
        headerTitleStyle: { fontFamily: FONT_FAMILY.semibold },
        headerShadowVisible: false,
        sceneStyle: { backgroundColor: theme.bg }
      }}
    >
      <Tabs.Screen
        name="capture"
        options={{
          title: captureTitle,
          tabBarAccessibilityLabel: opensCamera ? `${captureTitle}, opens the camera` : captureTitle,
          tabBarIcon: ({ color }) => <Ionicons name="camera" size={26} color={color} />
        }}
        listeners={{
          tabPress: (e) => {
            // Straight to the camera when this login can shoot; otherwise show why not.
            if (!opensCamera) return;
            e.preventDefault();
            router.push("/capture-session");
          }
        }}
      />
      <Tabs.Screen
        name="lots"
        options={{
          title: "Lots",
          href: hrefFor("lots", "/(tabs)/lots", !isClient),
          tabBarIcon: ({ color }) => <Ionicons name="list" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="queue"
        options={{
          title: "Queue",
          freezeOnBlur: true,
          tabBarBadge: formatTabBadge(queueCount),
          tabBarAccessibilityLabel: queueTabAccessibilityLabel(queueCount),
          tabBarIcon: ({ color }) => <Ionicons name="cloud-upload-outline" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="facebook"
        options={{
          title: "Facebook",
          href: hrefFor("facebook", "/(tabs)/facebook", !isClient),
          tabBarIcon: ({ color }) => <Ionicons name="storefront-outline" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="sale"
        options={{
          title: "Sale",
          href: hrefFor("sale", "/(tabs)/sale", !isClient),
          tabBarIcon: ({ color }) => <Ionicons name="pricetag" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="activity"
        options={{
          title: "Team",
          href: hrefFor("activity", "/(tabs)/activity", isOwner),
          tabBarIcon: ({ color }) => <Ionicons name="stats-chart" size={26} color={color} />
        }}
      />
    </Tabs>
  );
}
