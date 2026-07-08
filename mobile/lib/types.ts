export type TokenResponse = {
  access_token: string;
  token_type?: string;
};

export type AccessTokenClaims = {
  sub?: string;
  account_id?: string;
  role?: string;
  exp?: number;
};

export type Sale = {
  id: string;
  account_id: string;
  user_id: string;
  name: string;
  closed_at: string | null;
  created_at: string;
};

export type Job = {
  id: string;
  account_id: string;
  user_id: string;
  sale_id: string;
  status: string;
  input_folder: string;
  error_message: string | null;
  created_at: string;
  inherit_sku_from_subfolder?: boolean;
  single_lot_mode?: boolean;
  single_lot_manual_sku?: string | null;
};

export type LotDraftStatus = "draft" | "queued" | "uploading" | "uploaded" | "failed";

export type LotDraft = {
  id: string;
  account_id: string;
  sale_id: string;
  /** Sale name at capture time, so the queue can show where each lot will upload. */
  sale_name: string | null;
  manual_sku: string | null;
  server_job_id: string | null;
  status: LotDraftStatus;
  error_message: string | null;
  created_at: number;
  updated_at: number;
  photo_count: number;
};

export type LotPhotoRow = {
  id: string;
  draft_id: string;
  file_uri: string;
  sort_order: number;
};
