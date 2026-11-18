/** Same rules as backend `app/services/standard_description.py`. */

export function normalizeStandardDescription(raw: string | null | undefined): string {
  const text = (raw || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  if (!text) return "";
  return text
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/g, ""))
    .join("\n")
    .trim();
}

function paragraphs(text: string): string[] {
  return text
    .split("\n\n")
    .map((block) => block.trim())
    .filter(Boolean);
}

export function suffixAlreadyPresent(description: string | null | undefined, suffix: string | null | undefined): boolean {
  const extra = normalizeStandardDescription(suffix);
  if (!extra) return true;
  const body = (description || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\s+$/g, "");
  if (body === extra || body.endsWith(`\n\n${extra}`) || body.endsWith(`\n${extra}`)) return true;
  const extraParts = paragraphs(extra);
  const parts = paragraphs(body);
  if (!extraParts.length || parts.length < extraParts.length) return false;
  const width = extraParts.length;
  for (let index = 0; index <= parts.length - width; index += 1) {
    let match = true;
    for (let offset = 0; offset < width; offset += 1) {
      if (parts[index + offset] !== extraParts[offset]) {
        match = false;
        break;
      }
    }
    if (match) return true;
  }
  return false;
}

export function appendStandardDescription(description: string | null | undefined, suffix: string | null | undefined): string {
  const extra = normalizeStandardDescription(suffix);
  const body = (description || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\s+$/g, "");
  if (!extra || suffixAlreadyPresent(body, extra)) return body;
  if (!body) return extra;
  return `${body}\n\n${extra}`;
}

export function replaceTrailingStandardDescription(
  description: string | null | undefined,
  previous: string | null | undefined,
  next: string | null | undefined
): string {
  let body = (description || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\s+$/g, "");
  const prev = normalizeStandardDescription(previous);
  if (prev) {
    const parts = paragraphs(body);
    const prevParts = paragraphs(prev);
    const tail = parts.slice(parts.length - prevParts.length);
    const sameTail =
      prevParts.length > 0 &&
      tail.length === prevParts.length &&
      tail.every((part, index) => part === prevParts[index]);
    if (sameTail) {
      body = parts.slice(0, parts.length - prevParts.length).join("\n\n").replace(/\s+$/g, "");
    } else if (body === prev) {
      body = "";
    } else if (body.endsWith(`\n\n${prev}`)) {
      body = body.slice(0, -(prev.length + 2)).replace(/\s+$/g, "");
    } else if (body.endsWith(`\n${prev}`)) {
      body = body.slice(0, -(prev.length + 1)).replace(/\s+$/g, "");
    }
  }
  return appendStandardDescription(body, next);
}
