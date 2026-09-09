import { Image, type ImageStyle } from "expo-image";
import { memo, useMemo } from "react";
import type { StyleProp } from "react-native";

/** Same URI must keep the same source object so expo-image does not fade-reload on parent re-renders. */
export const StableUriImage = memo(function StableUriImage({
  uri,
  style,
  recyclingKey,
  contentFit = "cover"
}: {
  uri: string;
  style: StyleProp<ImageStyle>;
  recyclingKey?: string;
  contentFit?: "cover" | "contain";
}) {
  const source = useMemo(() => ({ uri }), [uri]);
  return (
    <Image
      source={source}
      style={style}
      contentFit={contentFit}
      cachePolicy="memory-disk"
      recyclingKey={recyclingKey ?? uri}
      transition={0}
    />
  );
});
