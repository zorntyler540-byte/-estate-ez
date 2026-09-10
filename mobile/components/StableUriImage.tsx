import { Image, type ImageStyle } from "expo-image";
import { memo, useMemo } from "react";
import type { StyleProp } from "react-native";

/** Same URI must keep the same source object so expo-image does not fade-reload on parent re-renders. */
export const StableUriImage = memo(function StableUriImage({
  uri,
  style,
  recyclingKey,
  contentFit = "cover",
  cachePolicy = "memory-disk"
}: {
  uri: string;
  style: StyleProp<ImageStyle>;
  recyclingKey?: string;
  contentFit?: "cover" | "contain";
  /** Queue uses disk-only so opening a long list does not keep every JPEG in RAM. */
  cachePolicy?: "none" | "disk" | "memory" | "memory-disk";
}) {
  const source = useMemo(() => ({ uri }), [uri]);
  if (!uri.trim()) return null;
  return (
    <Image
      source={source}
      style={style}
      contentFit={contentFit}
      cachePolicy={cachePolicy}
      recyclingKey={recyclingKey ?? uri}
      transition={0}
    />
  );
});
