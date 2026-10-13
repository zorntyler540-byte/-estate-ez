import { AuthProvider, useAuth } from "@/context/AuthContext";
import { SalesProvider } from "@/context/SalesContext";
import { SyncProvider } from "@/context/SyncContext";
import { theme } from "@/constants/theme";
import { DarkTheme, ThemeProvider } from "@react-navigation/native";
import { useFonts } from "expo-font";
import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import "react-native-reanimated";
import { StatusBar } from "expo-status-bar";

export { ErrorBoundary } from "expo-router";

SplashScreen.preventAutoHideAsync();

const EstateTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: theme.primary,
    background: theme.bg,
    card: theme.bgElevated,
    text: theme.text,
    border: theme.border,
    notification: theme.primary
  }
};

export default function RootLayout() {
  const [loaded, error] = useFonts({
    SpaceMono: require("../assets/fonts/SpaceMono-Regular.ttf")
  });

  useEffect(() => {
    if (error) throw error;
  }, [error]);

  useEffect(() => {
    if (loaded) SplashScreen.hideAsync();
  }, [loaded]);

  if (!loaded) return null;

  return (
    <AuthProvider>
      <SalesProvider>
        <SyncProvider>
          <RootNav />
        </SyncProvider>
      </SalesProvider>
    </AuthProvider>
  );
}

function RootNav() {
  const { token, ready, sessionReady } = useAuth();
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (!ready) return;
    const root = segments[0];
    const onAuthScreen = root === "login";
    if (!token && !onAuthScreen) {
      router.replace("/login");
      return;
    }
    if (token && sessionReady && onAuthScreen) {
      router.replace("/(tabs)/capture");
    }
  }, [token, ready, sessionReady, segments, router]);

  return (
    <ThemeProvider value={EstateTheme}>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: theme.bgElevated },
          headerTintColor: theme.text,
          contentStyle: { backgroundColor: theme.bg },
          headerShadowVisible: false
        }}
      >
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="login" options={{ title: "Sign in", headerLargeTitle: true }} />
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen
          name="capture-session"
          options={{ headerShown: false, presentation: "fullScreenModal", animation: "fade" }}
        />
        <Stack.Screen name="lot/[id]" options={{ title: "Lot" }} />
      </Stack>
    </ThemeProvider>
  );
}
