import {
  clampPreviewZoom,
  pinchDistance,
  scaleFromPinch,
  shouldSwipePreview
} from "./previewZoom";

function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

assert(clampPreviewZoom(0.5) === 1, "zoom floor");
assert(clampPreviewZoom(9) === 4, "zoom ceiling");
assert(clampPreviewZoom(2) === 2, "zoom mid");

assert(scaleFromPinch(1, 100, 200) === 2, "pinch out doubles");
assert(scaleFromPinch(2, 200, 100) === 1, "pinch in floors at 1");
assert(scaleFromPinch(1, 0, 50) === 1, "zero start dist is safe");

assert(shouldSwipePreview(1, 80) === -1, "swipe prev when not zoomed");
assert(shouldSwipePreview(1, -80) === 1, "swipe next when not zoomed");
assert(shouldSwipePreview(2, 80) === 0, "no swipe while zoomed");
assert(shouldSwipePreview(1, 10) === 0, "small move is not a swipe");

assert(Math.round(pinchDistance({ pageX: 0, pageY: 0 }, { pageX: 3, pageY: 4 })) === 5, "pinch hypot");

console.log("previewZoom.selftest ok");
