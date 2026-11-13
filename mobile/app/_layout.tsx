import { AuthProvider, useAuth } from "@/context/AuthContext";
import { SalesProvider } from "@/context/SalesContext";
import { SyncProvider } from "@/context/SyncContext";
import { theme } from "@/constants/theme";
import { SourceSans3_400Regular } from "@expo-google-fonts/source-sans-3/400Regular";
import { SourceSans3_600SemiBold } from "@expo-google-fonts/source-sans-3/600SemiBold";
import { SourceSans3_700Bold } from "@expo-google-fonts/source-sans-3/700Bold";
import { SourceSans3_800ExtraBold } from "@expo-google-fonts/source-sans-3/800ExtraBold";
import { DarkTheme, ThemeProvider } from "@react-navigation/native";
import { useFonts } from "expo-font";
import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect, useRef } from "react";
import "react-native-reanimated";
import { Alert } from "react-native";
import { StatusBar } from "expo-status-bar";

import { getAppValue, setAppValue } from "@/lib/queue/db";
import { RELEASE_NOTE, releaseNoteToShow } from "@/lib/releaseNote";
import { FONT_FAMILY } from "@/lib/typography";
// Defines the background upload task at startup — the OS can launch the app just to run it.
import "@/lib/backgroundUpload";

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
  },
  fonts: {
    regular: { fontFamily: FONT_FAMILY.regular, fontWeight: "normal" as const },
    medium: { fontFamily: FONT_FAMILY.semibold, fontWeight: "normal" as const },
    bold: { fontFamily: FONT_FAMILY.bold, fontWeight: "normal" as const },
    heavy: { fontFamily: FONT_FAMILY.extrabold, fontWeight: "normal" as const }
  }
};

export default function RootLayout() {
  const [loaded, error] = useFonts({
    [FONT_FAMILY.regular]: SourceSans3_400Regular,
    [FONT_FAMILY.semibold]: SourceSans3_600SemiBold,
    [FONT_FAMILY.bold]: SourceSans3_700Bold,
    [FONT_FAMILY.extrabold]: SourceSans3_800ExtraBold
  });
  // A font that fails to load falls back to the system face — never block sign-in on it.
  const ready = loaded || Boolean(error);

  useEffect(() => {
    if (ready) SplashScreen.hideAsync();
  }, [ready]);

  if (!ready) return null;

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

const RELEASE_NOTE_SEEN_KEY = "release_note_seen";

function RootNav() {
  const { token, ready, sessionReady } = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const releaseNoteStarted = useRef(false);

  useEffect(() => {
    if (releaseNoteStarted.current) return;
    releaseNoteStarted.current = true;
    void (async () => {
      try {
        const seen = await getAppValue(RELEASE_NOTE_SEEN_KEY);
        const note = releaseNoteToShow(seen, RELEASE_NOTE);
        if (!note) return;
        Alert.alert("What's new", note.text, [
          { text: "OK", onPress: () => void setAppValue(RELEASE_NOTE_SEEN_KEY, note.key) }
        ]);
      } catch {
        /* The next cold start tries again. */
      }
    })();
  }, []);

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
          headerTitleStyle: { fontFamily: FONT_FAMILY.semibold },
          headerLargeTitleStyle: { fontFamily: FONT_FAMILY.bold },
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
