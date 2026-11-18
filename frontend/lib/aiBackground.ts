/** Which gallery frame the AI background picker selects, and which frames can be the cover. */

export type AiBackgroundPhoto = {
  id: string;
  photo_role?: string | null;
  shows_measuring_scale?: boolean;
  original_name?: string | null;
};

const VIDEO_NAME = /\.(mp4|mov|m4v|webm)$/i;

export function photoCanBeLotCover(photo: AiBackgroundPhoto): boolean {
  const role = (photo.photo_role || "").toLowerCase();
  if (role === "tape" || role === "unusable" || photo.shows_measuring_scale) return false;
  if (VIDEO_NAME.test(String(photo.original_name || "").trim())) return false;
  return true;
}

/** Listing cover: a hero role when one exists, otherwise the first still that can be a cover. */
export function defaultAiBackgroundPhoto<T extends AiBackgroundPhoto>(photos: T[]): T | null {
  const eligible = photos.filter((photo) => photoCanBeLotCover(photo));
  return eligible.find((photo) => (photo.photo_role || "").toLowerCase() === "hero") || eligible[0] || null;
}
