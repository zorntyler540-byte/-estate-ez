/** Lot editor gallery: stills first (listing order), then clips. Never treat a clip as a photo tile. */

const VIDEO_NAME = /\.(mp4|mov|m4v|webm)$/i;

export function isVideoLikePhotoName(name: string | null | undefined): boolean {
  return VIDEO_NAME.test(String(name || "").trim());
}

export function photosForEditorGrid<T extends { original_name?: string | null }>(photos: T[]): T[] {
  return photos.filter((photo) => !isVideoLikePhotoName(photo.original_name));
}

export type EditorGalleryPhoto<P> = { kind: "photo"; photo: P };
export type EditorGalleryVideo<V> = { kind: "video"; video: V };
export type EditorGalleryItem<P, V> = EditorGalleryPhoto<P> | EditorGalleryVideo<V>;

export function lotEditorGallery<
  P extends { original_name?: string | null },
  V
>(photos: P[], videos: V[]): EditorGalleryItem<P, V>[] {
  const stills: EditorGalleryItem<P, V>[] = photosForEditorGrid(photos).map((photo) => ({
    kind: "photo",
    photo
  }));
  const clips: EditorGalleryItem<P, V>[] = videos.map((video) => ({ kind: "video", video }));
  return [...stills, ...clips];
}
