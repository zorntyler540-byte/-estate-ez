/** FastAPI base URL — ``EXPO_PUBLIC_API_BASE`` overrides this (see ``mobile/.env`` / EAS). */
const PRODUCTION_API_BASE = "https://caringtransitions-ai-production.up.railway.app";

export const API_BASE = (process.env.EXPO_PUBLIC_API_BASE || PRODUCTION_API_BASE).replace(
  /\/$/,
  ""
);

export const SELECTED_SALE_KEY = "ct_mobile_selected_sale_id";

export const SELECTED_SALE_NAME_KEY = "ct_mobile_selected_sale_name";
