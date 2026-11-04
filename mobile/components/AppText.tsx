import { forwardRef, type ElementRef } from "react";
import {
  StyleSheet,
  Text as RNText,
  TextInput as RNTextInput,
  type TextInputProps,
  type TextProps,
  type TextStyle
} from "react-native";

import { withAppFont } from "@/lib/typography";

/** ``Text`` in Source Sans 3; ``fontWeight`` in styles maps to the matching face. */
export const Text = forwardRef<ElementRef<typeof RNText>, TextProps>(function AppText(
  { style, ...rest },
  ref
) {
  return <RNText ref={ref} {...rest} style={withAppFont(StyleSheet.flatten(style)) as TextStyle} />;
});

export const TextInput = forwardRef<ElementRef<typeof RNTextInput>, TextInputProps>(
  function AppTextInput({ style, ...rest }, ref) {
    return <RNTextInput ref={ref} {...rest} style={withAppFont(StyleSheet.flatten(style)) as TextStyle} />;
  }
);
