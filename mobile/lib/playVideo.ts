import * as FileSystem from "expo-file-system/legacy";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import { Platform } from "react-native";

import { videoPlayMode } from "./playVideoMode";

export { videoPlayMode };

function asFileUri(uri: string): string {
  const raw = uri.trim();
  if (raw.startsWith("file://") || raw.startsWith("content://")) return raw;
  if (raw.startsWith("/")) return `file://${raw}`;
  return raw;
}

/** Play a signed website URL or a clip still on this phone. */
export async function playVideoUri(uri: string): Promise<void> {
  const mode = videoPlayMode(uri);
  if (mode === "none") throw new Error("No video to play");
  if (mode === "remote") {
    await WebBrowser.openBrowserAsync(uri.trim());
    return;
  }
  let playUri = asFileUri(uri);
  if (Platform.OS === "android") {
    try {
      const content = await FileSystem.getContentUriAsync(playUri);
      if (content) playUri = content;
    } catch {
      /* fall through to file:// */
    }
  }
  await Linking.openURL(playUri);
}
