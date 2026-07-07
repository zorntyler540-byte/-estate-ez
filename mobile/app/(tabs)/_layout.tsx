import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";

import Colors from "@/constants/Colors";
import { useColorScheme } from "@/components/useColorScheme";

export default function TabLayout() {
  const colorScheme = useColorScheme();

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: Colors[colorScheme ?? "light"].tint,
        headerStyle: { backgroundColor: colorScheme === "dark" ? "#0f172a" : "#fff" },
        headerTintColor: colorScheme === "dark" ? "#f8fafc" : "#0f172a"
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
