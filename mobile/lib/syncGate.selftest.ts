import { SyncGate } from "./syncGate";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const gate = new SyncGate();
assert(gate.begin() === true, "first Sync starts");
assert(gate.busy, "busy while running");
assert(gate.end() === null, "nothing queued → no rerun");
assert(!gate.busy, "idle after end");

assert(gate.begin() === true, "start");
assert(gate.begin() === false, "second request waits");
assert(gate.begin({ manual: true }) === false, "third request collapses");
assert(gate.begin() === false, "fourth request collapses");
const next = gate.end();
assert(next !== null && next.manual === true, "one rerun, manual sticks");
assert(gate.end() === null, "rerun consumed once");

assert(gate.begin() === true, "start again");
assert(gate.begin() === false, "auto request waits");
assert(gate.end()?.manual === false, "auto rerun stays auto");

assert(gate.begin() === true, "start");
assert(gate.begin({ allowCellular: true }) === false, "cellular override waits");
assert(gate.begin() === false, "later auto request");
assert(gate.end()?.allowCellular === true, "cellular override sticks through collapse");

// Background task holds the gate; the app's request runs through the handler when it finishes.
{
  const shared = new SyncGate();
  const reruns: string[] = [];
  assert(shared.begin() === true, "background Sync starts");
  assert(shared.begin({ manual: true }) === false, "app Sync waits for the background one");
  shared.finish();
  assert(!shared.busy, "idle after finish");
  // No handler yet (app not mounted): the request stays pending for the next begin/end.
  assert(shared.begin() === true, "app starts later");
  assert(shared.end()?.manual === true, "pending request survives until someone runs it");

  shared.setRerunHandler((req) => reruns.push(req.manual ? "manual" : "auto"));
  shared.begin();
  shared.begin();
  shared.finish();
  assert(reruns.join() === "auto", "handler runs the queued request once");
  shared.begin();
  shared.finish();
  assert(reruns.length === 1, "no request → no rerun");
}

console.log("syncGate selftest ok");
