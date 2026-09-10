import * as FileSystem from "expo-file-system/legacy";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import { Platform, Share } from "react-native";

import { localVideoPlayMethod, videoPlayMode } from "./playVideoMode";

export { localVideoPlayMethod, videoPlayMode };

function asFileUri(uri: string): string {
  const raw = uri.trim();
  if (raw.startsWith("file://") || raw.startsWith("content://")) return raw;
  if (raw.startsWith("/")) return `file://${raw}`;
  return raw;
}

function videoSuffix(uri: string): string {
  const match = uri.toLowerCase().match(/\.(mp4|mov|m4v|webm)(?:\?|$)/);
  return match?.[1] ? `.${match[1]}` : ".mp4";
}

async function copyForPlay(uri: string): Promise<string> {
  const from = asFileUri(uri);
  const root = FileSystem.cacheDirectory;
  if (!root) return from;
  const dest = `${root}ct-play-${Date.now()}${videoSuffix(from)}`;
  try {
    await FileSystem.copyAsync({ from, to: dest });
    return dest;
  } catch {
    return from;
  }
}

/** Play a signed website URL or a clip still on this phone. */
export async function playVideoUri(uri: string): Promise<void> {
  const mode = videoPlayMode(uri);
  if (mode === "none") throw new Error("No video to play");
  if (mode === "remote") {
    await WebBrowser.openBrowserAsync(uri.trim());
    return;
  }

  const info = await FileSystem.getInfoAsync(asFileUri(uri)).catch(() => ({ exists: false }));
  if (!info.exists) {
    throw new Error("This video file is missing on the phone — record it again.");
  }

  const playUri = await copyForPlay(uri);
  if (localVideoPlayMethod(Platform.OS) === "share") {
    try {
      await Share.share({ url: playUri, title: "Lot video" });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (/cancel|dismiss/i.test(msg)) return;
      throw new Error("Could not open this video — try Sync, then play it from the lot on the website.");
    }
    return;
  }

  let androidUri = playUri;
  try {
    const content = await FileSystem.getContentUriAsync(playUri);
    if (content) androidUri = content;
  } catch {
    /* fall through */
  }
  await Linking.openURL(androidUri);
}
