const { withAndroidManifest, withAndroidStyles } = require("expo/config-plugins");

/**
 * Android manifest tweaks for the Play build of the capture app.
 *
 * Gallery backup still uses WRITE_EXTERNAL_STORAGE on Android 10 and earlier.
 * API 30+ inserts through MediaStore and ignores this permission, so cap it
 * the way Android's own storage guide does.
 *
 * The UI is dark on every phone. DayNight would draw dark status-bar icons
 * when the phone is in light mode, which disappear on this background.
 */
function withAndroidCapture(config) {
  config = withAndroidManifest(config, (config) => {
    const permissions = config.modResults.manifest["uses-permission"] || [];
    for (const permission of permissions) {
      if (permission.$?.["android:name"] === "android.permission.WRITE_EXTERNAL_STORAGE") {
        permission.$["android:maxSdkVersion"] = "29";
      }
    }
    return config;
  });

  return withAndroidStyles(config, (config) => {
    const styles = config.modResults.resources.style || [];
    const app = styles.find((style) => style.$?.name === "AppTheme");
    if (!app) return config;
    const items = Array.isArray(app.item) ? app.item : app.item ? [app.item] : [];
    const setItem = (name, value, targetApi) => {
      const attrs = { name };
      if (targetApi) attrs["tools:targetApi"] = targetApi;
      const existing = items.find((item) => item.$?.name === name);
      if (existing) {
        existing._ = value;
        existing.$ = { ...existing.$, ...attrs };
        return;
      }
      items.push({ $: attrs, _: value });
    };
    setItem("android:windowLightStatusBar", "false");
    setItem("android:windowLightNavigationBar", "false", "27");
    setItem("android:forceDarkAllowed", "false", "29");
    app.item = items;
    return config;
  });
}

module.exports = withAndroidCapture;
