export type OpticalZoomRange = {
  min: number;
  max: number;
  step: number;
};

type ZoomCaps = MediaTrackCapabilities & {
  zoom?: { min?: number; max?: number; step?: number };
};

/** Device lens zoom range from ``getCapabilities().zoom`` (Safari 17+ / Chrome Android). */
export function getOpticalZoomRange(track: MediaStreamTrack | null | undefined): OpticalZoomRange | null {
  if (!track?.getCapabilities) return null;
  const zoom = (track.getCapabilities() as ZoomCaps).zoom;
  if (!zoom || typeof zoom.max !== "number" || zoom.max < 1.05) return null;
  return {
    min: typeof zoom.min === "number" ? zoom.min : 1,
    max: zoom.max,
    step: typeof zoom.step === "number" && zoom.step > 0 ? zoom.step : 0.1
  };
}

export function readAppliedOpticalZoom(track: MediaStreamTrack | null | undefined): number {
  if (!track?.getSettings) return 1;
  const zoom = (track.getSettings() as { zoom?: number }).zoom;
  return typeof zoom === "number" && zoom >= 1 ? zoom : 1;
}

/** Use hardware zoom up to ``opticalMax``; anything above is digital crop on the preview/capture. */
export function splitTotalZoom(
  total: number,
  opticalMax: number
): { optical: number; digital: number; total: number } {
  const t = Math.max(1, total);
  if (opticalMax <= 1.001) {
    return { optical: 1, digital: t, total: t };
  }
  const optical = Math.min(t, opticalMax);
  return { optical, digital: t / optical, total: t };
}

export function totalZoomMaxForDevice(opticalMax: number, uiCap = 4, hardCap = 10): number {
  if (opticalMax <= 1.001) return uiCap;
  return Math.min(hardCap, Math.max(uiCap, opticalMax));
}

export async function applyOpticalZoom(track: MediaStreamTrack, zoom: number): Promise<number> {
  const range = getOpticalZoomRange(track);
  if (!range || !track.applyConstraints) return 1;

  const step = range.step > 0 ? range.step : 0.1;
  const snapped =
    Math.round(Math.min(range.max, Math.max(range.min, zoom)) / step) * step;
  const target = Math.round(snapped * 100) / 100;

  try {
    await track.applyConstraints({ zoom: target } as MediaTrackConstraints);
    return readAppliedOpticalZoom(track);
  } catch {
    try {
      await track.applyConstraints({ advanced: [{ zoom: target }] } as unknown as MediaTrackConstraints);
      return readAppliedOpticalZoom(track);
    } catch {
      return readAppliedOpticalZoom(track);
    }
  }
}

export function lotCameraZoomModeLabel(
  total: number,
  opticalApplied: number,
  opticalMax: number
): string {
  if (total <= 1.001) {
    return opticalMax > 1.05 ? `1× wide (lens to ${opticalMax.toFixed(1)}×)` : "1× wide";
  }
  const digital = total / Math.max(1, opticalApplied);
  if (opticalApplied > 1.05 && digital <= 1.05) {
    return `Optical ${opticalApplied.toFixed(1)}× lens`;
  }
  if (opticalApplied > 1.05) {
    return `Optical ${opticalApplied.toFixed(1)}× + digital crop`;
  }
  return "Digital crop — move closer for sharpest detail";
}
