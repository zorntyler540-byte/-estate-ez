import { realpath } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";

export function homeDir(): string {
  return homedir();
}

/** True when `candidate` is the home directory or a path inside it. */
export function insideHome(candidate: string): boolean {
  const home = path.resolve(homedir());
  const resolved = path.resolve(candidate);
  const rel = path.relative(home, resolved);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

/** Resolve a user path against the home directory. `~` is the home itself. */
export function expandHome(input: string | undefined): string {
  const home = homedir();
  if (input === undefined || input.trim() === "" || input.trim() === "~") {
    return home;
  }
  const trimmed = input.trim();
  if (trimmed.startsWith("~/") || trimmed.startsWith("~\\")) {
    return path.resolve(home, trimmed.slice(2));
  }
  if (path.isAbsolute(trimmed)) {
    return path.resolve(trimmed);
  }
  return path.resolve(home, trimmed);
}

export type Confined =
  | { ok: true; path: string }
  | { ok: false; reason: string };

/**
 * Resolve `input` and refuse anything that leaves the home directory,
 * including a symlink whose target is outside it.
 */
export async function confine(input: string | undefined): Promise<Confined> {
  const expanded = expandHome(input);
  if (!insideHome(expanded)) {
    return { ok: false, reason: "That path is outside the home directory." };
  }
  try {
    const real = await realpath(expanded);
    if (!insideHome(real)) {
      return { ok: false, reason: "That path escapes the home directory." };
    }
    return { ok: true, path: real };
  } catch (error) {
    const code =
      error !== null && typeof error === "object" && "code" in error
        ? String(error.code)
        : "";
    if (code === "ENOENT") {
      return { ok: false, reason: "Nothing is at that path." };
    }
    return { ok: false, reason: "That path could not be read." };
  }
}
