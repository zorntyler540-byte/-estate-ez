import { View as DefaultView, type TextProps as RNTextProps, type ViewProps as RNViewProps } from "react-native";

import { Text as DefaultText } from "@/components/AppText";
import { theme } from "@/constants/theme";

/** Dark-only app: these just default to the Estate-EZ text / background tokens. */
export type TextProps = RNTextProps;
export type ViewProps = RNViewProps;

export function Text({ style, ...otherProps }: TextProps) {
  return <DefaultText style={[{ color: theme.text }, style]} {...otherProps} />;
}

export function View({ style, ...otherProps }: ViewProps) {
  return <DefaultView style={[{ backgroundColor: theme.bg }, style]} {...otherProps} />;
}
