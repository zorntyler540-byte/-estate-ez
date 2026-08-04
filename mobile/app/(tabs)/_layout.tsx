import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";

import { theme } from "@/constants/theme";

export default function TabLayout() {
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
          tabBarIcon: ({ color }) => <Ionicons name="cloud-upload-outline" size={26} color={color} />
        }}
      />
      <Tabs.Screen
        name="sale"
        options={{
          title: "Sale",
          tabBarIcon: ({ color }) => <Ionicons name="pricetag" size={26} color={color} />
        }}
      />
    </Tabs>
  );
}
