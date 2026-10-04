/** What the Capture tab should do for this signed-in person. */

export type CaptureGate =
  | { action: "capture"; saleLine: string; staffHint: string | null }
  | { action: "pick-sale"; message: string }
  | { action: "wait-for-owner"; message: string };

export function isClientAccountRole(role: string | null | undefined): boolean {
  return (role || "").trim().toLowerCase() === "client";
}

export function captureGate(input: {
  role: string | null | undefined;
  saleId: string | null | undefined;
  saleName: string | null | undefined;
}): CaptureGate {
  const client = isClientAccountRole(input.role);
  const saleId = (input.saleId || "").trim();
  const saleName = (input.saleName || "").trim();
  if (!saleId) {
    if (client) {
      return {
        action: "wait-for-owner",
        message: "This login does not have a sale yet. Ask the account owner to assign one before taking photos."
      };
    }
    return {
      action: "pick-sale",
      message: "Choose a sale on the Sale tab before capturing."
    };
  }
  const label = saleName || "your sale";
  if (client) {
    return {
      action: "capture",
      saleLine: `Photos from this phone go into ${label}.`,
      staffHint: null
    };
  }
  return {
    action: "capture",
    saleLine: `Uploading to: ${label}`,
    staffHint: "Wrong sale? Change it on the Sale tab before capturing."
  };
}
