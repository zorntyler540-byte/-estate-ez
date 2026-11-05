import assert from "node:assert/strict";
import test from "node:test";
import { extractExifSegment, insertExifSegment, scaledSize, withOrientationReset } from "./uploadResize.ts";

/** APP1 Exif segment with one IFD0 entry: Orientation = ``orientation``. */
function exifSegment(orientation: number, little: boolean): Uint8Array {
  const tiff: number[] = little
    ? [0x49, 0x49, 0x2a, 0x00, 0x08, 0x00, 0x00, 0x00]
    : [0x4d, 0x4d, 0x00, 0x2a, 0x00, 0x00, 0x00, 0x08];
  const u16 = (v: number) => (little ? [v & 0xff, v >> 8] : [v >> 8, v & 0xff]);
  const u32 = (v: number) => (little ? [v & 0xff, 0, 0, 0] : [0, 0, 0, v & 0xff]);
  const ifd = [...u16(1), ...u16(0x0112), ...u16(3), ...u32(1), ...u16(orientation), 0, 0, 0, 0, 0, 0];
  const payload = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff, ...ifd];
  const len = payload.length + 2;
  return new Uint8Array([0xff, 0xe1, len >> 8, len & 0xff, ...payload]);
}

const JFIF = [0xff, 0xe0, 0x00, 0x04, 0x00, 0x00];
const SCAN = [0xff, 0xda, 0x00, 0x02, 0x11, 0x22, 0xff, 0xd9];

function jpeg(...segments: (Uint8Array | number[])[]): Uint8Array {
  const parts = [[0xff, 0xd8], ...segments.map((s) => Array.from(s)), SCAN];
  return new Uint8Array(parts.flat());
}

test("finds the Exif segment after JFIF and stops at the scan", () => {
  const seg = exifSegment(6, true);
  assert.deepEqual(extractExifSegment(jpeg(JFIF, seg)), seg);
  assert.equal(extractExifSegment(jpeg(JFIF)), null);
  assert.equal(extractExifSegment(new Uint8Array([1, 2, 3, 4])), null);
});

test("orientation is reset to 1 in both byte orders", () => {
  for (const little of [true, false]) {
    const reset = withOrientationReset(exifSegment(6, little));
    assert.deepEqual(reset, exifSegment(1, little));
  }
});

test("Exif is inserted right after SOI and replaces encoder Exif", () => {
  const original = exifSegment(1, true);
  const encoderExif = exifSegment(8, false);
  const out = insertExifSegment(jpeg(JFIF, encoderExif), original);
  assert.deepEqual(Array.from(out.subarray(0, 2)), [0xff, 0xd8]);
  assert.deepEqual(extractExifSegment(out), original);
  assert.equal(out.length, 2 + original.length + JFIF.length + SCAN.length);
});

test("scaledSize caps the long edge and keeps aspect", () => {
  assert.deepEqual(scaledSize(4032, 3024, 2560), { width: 2560, height: 1920 });
  assert.deepEqual(scaledSize(3024, 4032, 2560), { width: 1920, height: 2560 });
  assert.deepEqual(scaledSize(1600, 1200, 2560), { width: 1600, height: 1200 });
});
