import { clampPreviewZoom, pinchDistance, scaleFromPinch, scaleFromWheel } from "./previewZoom";

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

assert(clampPreviewZoom(0.5) === 1, "zoom floor");
assert(clampPreviewZoom(9) === 4, "zoom ceiling");
assert(clampPreviewZoom(2) === 2, "zoom mid");

assert(scaleFromPinch(1, 100, 200) === 2, "pinch out doubles");
assert(scaleFromPinch(2, 200, 100) === 1, "pinch in floors at 1");
assert(scaleFromPinch(1, 0, 50) === 1, "zero start dist is safe");

assert(scaleFromWheel(1, -100) > 1, "wheel up zooms in");
assert(scaleFromWheel(1.05, 100) === 1, "small wheel out snaps to 1");
assert(scaleFromWheel(2, 0) === 2, "zero delta is a no-op");

assert(Math.round(pinchDistance({ clientX: 0, clientY: 0 }, { clientX: 3, clientY: 4 })) === 5, "pinch hypot");

console.log("frontend previewZoom.selftest ok");
