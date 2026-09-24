import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";

import { theme } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";

export default function TabLayout() {
  const { signedInRole } = useAuth();
  const isOwner = (signedInRole || "").toLowerCase() === "owner";

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textSubtle,
        tabBarStyle: {
          backgroundColor: theme.bgElevated,
          borderTopColor: theme.border
        },
        headerStyle: { backgroundColor: theme.bgElevated },
        headerTintColor: theme.text,
        headerShadowVisible: false,
        sceneContainerStyle: { backgroundColor: theme.bg }
      }}
    >
      <Tabs.Screen
        name="capture"
        options={{
          title: "Capture",
          tabBarIcon: ({ color }) => <Ionicons name="camera" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="lots"
        options={{
          title: "Lots",
          tabBarIcon: ({ color }) => <Ionicons name="list" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="queue"
        options={{
          title: "Queue",
          freezeOnBlur: true,
          tabBarIcon: ({ color }) => <Ionicons name="cloud-upload-outline" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="facebook"
        options={{
          title: "Facebook",
          tabBarIcon: ({ color }) => <Ionicons name="storefront-outline" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="sale"
        options={{
          title: "Sale",
          tabBarIcon: ({ color }) => <Ionicons name="pricetag" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="activity"
        options={{
          title: "Team",
          href: isOwner ? "/(tabs)/activity" : null,
          tabBarIcon: ({ color }) => <Ionicons name="stats-chart" size={26} color={color} />
        }}
      />
    </Tabs>
  );
}
