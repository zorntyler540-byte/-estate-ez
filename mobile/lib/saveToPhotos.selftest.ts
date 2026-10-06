import {
  isLibraryPermissionError,
  librarySaveNote,
  saveCaptureCopy,
  type LibrarySaver
} from "./saveToPhotos";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const saved: string[] = [];
const saver: LibrarySaver = {
  async save(uri: string) {
    saved.push(uri);
  }
};

const ok = await saveCaptureCopy("file:///lot/1.jpg", saver, "ios");
assert(ok === "saved", "ios save");
assert(saved[0] === "file:///lot/1.jpg", "saved the queue file");
assert(librarySaveNote(ok) === null, "successful save has no warning");

const denied = await saveCaptureCopy("file:///lot/2.jpg", {
  async save() {
    throw new Error("User denied permissions");
  }
}, "ios");
assert(denied === "denied", "permission denial is not a capture failure");
assert(librarySaveNote(denied)?.includes("Queue"), "queue copy is still the source of truth");
assert(isLibraryPermissionError(new Error("Access to photo library was denied")), "ios deny wording");

let androidAsked = false;
const androidDenied = await saveCaptureCopy("file:///lot/3.jpg", {
  async ensureWritePermission() {
    androidAsked = true;
    return false;
  },
  async save() {
    throw new Error("should not save without permission");
  }
}, "android");
assert(androidAsked && androidDenied === "denied", "android asks before writing");

const missing = await saveCaptureCopy("file:///lot/4.jpg", null, "ios");
assert(missing === "unavailable", "old builds without the native module skip the backup");
assert(librarySaveNote(missing) === null, "unavailable is quiet");

const blank = await saveCaptureCopy("  ", saver, "ios");
assert(blank === "failed", "blank uri");

const broken = await saveCaptureCopy("file:///lot/5.jpg", {
  async save() {
    throw new Error("disk full");
  }
}, "ios");
assert(broken === "failed", "other errors stay failed");
assert(librarySaveNote(broken)?.includes("Photos app"), "failed note");

console.log("saveToPhotos.selftest ok");
