import {
  PauseSignal,
  UPLOAD_PAUSED_MESSAGE,
  UploadPausedError,
  isPausedMessage,
  isUploadPausedError,
  treatAsPause
} from "./uploadPause";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

{
  const s = new PauseSignal();
  assert(!s.paused && s.reason === null, "starts running");
  s.throwIfPaused();

  // In-flight upload tasks subscribe and are cancelled once.
  const heard: string[] = [];
  const offA = s.onPause((r) => heard.push(`a:${r}`));
  s.onPause((r) => heard.push(`b:${r}`));
  offA();
  s.pause("offline");
  s.pause("wifi_only");
  assert(s.paused && s.reason === "offline", "first reason wins");
  assert(heard.join() === "b:offline", "listeners hear the pause once; unsubscribed ones do not");

  let threw: unknown = null;
  try {
    s.throwIfPaused();
  } catch (e) {
    threw = e;
  }
  assert(isUploadPausedError(threw), "throwIfPaused throws UploadPausedError");

  // A task created after the pause hears it immediately.
  let late = "";
  s.onPause((r) => (late = r));
  assert(late === "offline", "late subscriber is told right away");

  s.clear();
  assert(!s.paused, "clear() lets the next Sync run");
  s.pause("time_budget");
  assert(heard.length === 2 && heard[1] === "b:time_budget", "listener still subscribed after clear");
}

{
  const e = new UploadPausedError("wifi_only");
  assert(e.message.includes("Wi‑Fi only"), "wifi copy");
  assert(isUploadPausedError(e), "detects instance");
  const plain = new Error("x");
  plain.name = "UploadPausedError";
  assert(isUploadPausedError(plain), "detects by name (bundled copies)");
  assert(!isUploadPausedError(new Error("Network request failed")), "plain error is not a pause");
  assert(isPausedMessage(UPLOAD_PAUSED_MESSAGE), "paused message detected");
  assert(isPausedMessage(new UploadPausedError("time_budget").message), "time budget message detected");
  assert(!isPausedMessage("Upload failed (500)"), "other messages are not pauses");
}

{
  const net = new Error("Network request failed");
  assert(treatAsPause({ error: new UploadPausedError("offline"), signalPaused: false, permanent: false, netOffline: false }), "paused error");
  assert(treatAsPause({ error: net, signalPaused: true, permanent: false, netOffline: false }), "signal paused");
  assert(treatAsPause({ error: net, signalPaused: false, permanent: false, netOffline: true }), "network error while offline");
  assert(!treatAsPause({ error: net, signalPaused: false, permanent: false, netOffline: false }), "network error while online = transient failure");
  assert(!treatAsPause({ error: net, signalPaused: false, permanent: true, netOffline: true }), "4xx is never a pause");
}

console.log("uploadPause selftest ok");
