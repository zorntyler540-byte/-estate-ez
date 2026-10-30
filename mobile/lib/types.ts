export type TokenResponse = {
  access_token: string;
  token_type?: string;
};

export type Sale = {
  id: string;
  account_id: string;
  user_id: string;
  name: string;
  closed_at: string | null;
  created_at: string;
};

/** Catalog lot from ``GET /lots`` / ``GET /lots/{id}``. */
export type Lot = {
  id: string;
  account_id: string;
  user_id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  professional_description: string;
  specification_lines: string[];
  brands: string[];
  labels_and_tags: string[];
  ct_category: string | null;
  ct_subcategory: string | null;
  is_completed: boolean;
  completed_at: string | null;
  starting_price: number | null;
  condition_text: string;
  photo_count: number;
  video_count?: number;
  has_thumbnail: boolean;
  created_at: string;
  sku_needs_review: boolean;
  sku_duplicate_in_sale: boolean;
  sku_text_mismatch: boolean;
  ctseller_item_id: number | null;
  ctseller_posted_at: string | null;
  listing_channel?: string | null;
};

export type LotPhoto = {
  id: string;
  lot_id: string;
  original_name: string;
  created_at: string;
  sort_order: number;
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
  client_draft_id?: string | null;
};

export type LotDraftStatus = "draft" | "queued" | "uploading" | "uploaded" | "failed";

export type ListingChannel = "ctseller" | "facebook";

export type LotDraft = {
  id: string;
  sale_id: string;
  /** Sale name at capture time, so the queue can show where each lot will upload. */
  sale_name: string | null;
  manual_sku: string | null;
  listing_channel: ListingChannel;
  status: LotDraftStatus;
  error_message: string | null;
  /** Server ProcessingJob id while a multipart upload is in progress (cleared when done). */
  server_job_id: string | null;
  /** Catalog lot id after this draft is on the website (used to add forgotten photos). */
  server_lot_id: string | null;
  created_at: number;
  updated_at: number;
  photo_count: number;
  /** Set after the server confirmed catalog — local JPEGs were removed. */
  archived_at: number | null;
};

export type LotMediaKind = "image" | "video";

export type LotPhotoRow = {
  id: string;
  draft_id: string;
  file_uri: string;
  sort_order: number;
  media_kind: LotMediaKind;
  uploaded: boolean;
  captured_at?: number;
};

export type LotVideo = {
  id: string;
  lot_id: string;
  original_name: string;
  created_at: string;
  sort_order: number;
  content_type: string;
  byte_size: number;
};
