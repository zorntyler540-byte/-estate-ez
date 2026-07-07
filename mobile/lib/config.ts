/** FastAPI base URL — set ``EXPO_PUBLIC_API_BASE`` in ``mobile/.env`` (see ``.env.example``). */
export const API_BASE = (process.env.EXPO_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");

export const SELECTED_SALE_KEY = "ct_mobile_selected_sale_id";
