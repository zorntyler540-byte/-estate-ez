const { checkApiBase } = require("./lib/apiBaseRule");

// Expo CLI forces NODE_ENV=production and loads .env before reading this file for release
// bundles: `expo export` (what `eas update` runs) and `expo export:embed` (EAS Build).
// Throwing here stops a bundle without a usable API URL from ever reaching phones.
module.exports = ({ config }) => {
  if (process.env.NODE_ENV === "production") {
    const { error } = checkApiBase(process.env.EXPO_PUBLIC_API_BASE, true);
    if (error) throw new Error(error);
  }
  return config;
};
