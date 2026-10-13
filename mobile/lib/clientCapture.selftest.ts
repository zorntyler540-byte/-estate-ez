import { captureGate, isClientAccountRole } from "./clientCapture";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(isClientAccountRole(" Client ") === true, "client role");
assert(isClientAccountRole("owner") === false, "owner is not a client");

const clientReady = captureGate({ role: "client", saleId: "sale-1", saleName: "Spring estate" });
assert(clientReady.action === "capture", "client with a sale can capture");
if (clientReady.action === "capture") {
  assert(clientReady.saleLine.includes("Spring estate"), "client sale name");
  assert(clientReady.staffHint === null, "client is not sent to the Sale tab");
}

const clientMissing = captureGate({ role: "client", saleId: null, saleName: null });
assert(clientMissing.action === "wait-for-owner", "client without a sale waits for the owner");

const staffMissing = captureGate({ role: "catalogger", saleId: "", saleName: null });
assert(staffMissing.action === "pick-sale", "staff still pick a sale");

const staffReady = captureGate({ role: "owner", saleId: "sale-2", saleName: "Other house" });
assert(staffReady.action === "capture", "staff with a sale can capture");
if (staffReady.action === "capture") {
  assert(staffReady.staffHint?.includes("Sale tab"), "staff can change the sale");
}

console.log("clientCapture.selftest ok");
