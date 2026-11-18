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
import { useEffect, useRef, useState } from "react";
import "react-native-reanimated";
import { StatusBar } from "expo-status-bar";

import { ReleaseNoteModal } from "@/components/ReleaseNoteModal";
import { fetchLatestReleaseNote } from "@/lib/api";
import { getAppValue, setAppValue } from "@/lib/queue/db";
import { RELEASE_NOTE_SEEN_KEY, releaseNoteToShow, type ReleaseNote } from "@/lib/releaseNote";
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

function RootNav() {
  const { token, ready, sessionReady } = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const releaseNoteStarted = useRef(false);
  const [releaseNote, setReleaseNote] = useState<ReleaseNote | null>(null);
  const onLogin = segments[0] === "login";

  useEffect(() => {
    if (!token || !sessionReady || onLogin) return;
    if (releaseNoteStarted.current) return;
    releaseNoteStarted.current = true;
    const accessToken = token;
    void (async () => {
      try {
        const seen = await getAppValue(RELEASE_NOTE_SEEN_KEY);
        const latest = await fetchLatestReleaseNote(accessToken);
        const note = releaseNoteToShow(seen, latest);
        if (note) setReleaseNote(note);
      } catch {
        /* Offline or an older API: stay quiet. The next cold start tries again. */
      }
    })();
  }, [token, sessionReady, onLogin]);

  function dismissReleaseNote() {
    const note = releaseNote;
    setReleaseNote(null);
    if (note) void setAppValue(RELEASE_NOTE_SEEN_KEY, note.id).catch(() => undefined);
  }

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
      {releaseNote ? <ReleaseNoteModal note={releaseNote} onDismiss={dismissReleaseNote} /> : null}
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
