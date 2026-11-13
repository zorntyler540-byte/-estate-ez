import * as SecureStore from "expo-secure-store";

/** Survive background / lock better than the iOS default (WHEN_UNLOCKED). Android ignores this field. */
function storeOptions(): SecureStore.SecureStoreOptions | undefined {
  try {
    const os = (require("react-native") as { Platform?: { OS?: string } }).Platform?.OS;
    if (os === "ios") return { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK };
  } catch {
    /* node */
  }
  return undefined;
}

export async function secureGet(key: string): Promise<string | null> {
  return SecureStore.getItemAsync(key);
}

export async function secureSet(key: string, value: string): Promise<void> {
  await SecureStore.setItemAsync(key, value, storeOptions());
}

export async function secureDelete(key: string): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(key);
  } catch {
    /* already missing */
  }
}
