import { Image, StyleSheet, type ImageStyle, type StyleProp } from "react-native";

type BrandLogoProps = {
  size?: number;
  style?: StyleProp<ImageStyle>;
};

export function BrandLogo({ size = 120, style }: BrandLogoProps) {
  return (
    <Image
      source={require("../assets/images/zortron-logo.jpg")}
      accessibilityLabel="Zortron Tech Solutions"
      style={[styles.logo, { width: size, height: size }, style]}
    />
  );
}

const styles = StyleSheet.create({
  logo: {
    borderRadius: 12,
    backgroundColor: "#000"
  }
});
