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

console.log("syncGate selftest ok");
