import { spawn } from "node:child_process";
import { defineTool } from "@cursor/bdk/tools";
import * as z from "zod/v4";
import { homeDir } from "../lib/home.js";

const OUTPUT_CAP = 12_000;

const DELETES =
  /\b(rm|unlink|rmdir|shred|truncate)\b|\bfind\b[\s\S]*\s-delete\b|\bgit\s+clean\b|\bgit\s+reset\s+--hard\b|\bmkfs\b|\bdd\b/;
const SENDS =
  /\b(curl|wget|scp|sftp|rsync|ssh|nc|ncat|netcat|socat|ftp|telnet|sendmail|msmtp|mutt)\b|\bgit\s+push\b|\b(npm|pnpm|yarn)\s+publish\b|\bmail\b/;
const SETTINGS =
  /\b(systemctl|service|sysctl|timedatectl|hostnamectl|localectl|loginctl|ufw|iptables|nft|firewall-cmd|passwd|useradd|userdel|usermod|groupadd|groupdel|visudo|mount|umount|swapon|swapoff|crontab|chmod|chown|chgrp|launchctl|nmcli|poweroff|reboot|shutdown|halt|sudo|doas|setcap)\b|\bdefaults\s+write\b/;

type Gated = { needsConfirmation: true; reason: string };

function needsConfirmation(command: string): Gated | undefined {
  const reasons: string[] = [];
  if (DELETES.test(command)) {
    reasons.push("it would delete something");
  }
  if (SENDS.test(command)) {
    reasons.push("it would send data off this machine");
  }
  if (SETTINGS.test(command)) {
    reasons.push("it would change system settings");
  }
  if (reasons.length === 0) {
    return undefined;
  }
  return {
    needsConfirmation: true,
    reason: `Not run. Ask the user before continuing: ${reasons.join("; ")}.`,
  };
}

function runShell(command: string): Promise<{
  exitCode: number | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
}> {
  return new Promise((resolve) => {
    const child = spawn(command, {
      shell: true,
      cwd: homeDir(),
      timeout: 15_000,
    });
    let stdout = "";
    let stderr = "";
    let truncated = false;
    const take = (chunk: Buffer, which: "stdout" | "stderr") => {
      const text = chunk.toString("utf8");
      const current = which === "stdout" ? stdout : stderr;
      if (current.length >= OUTPUT_CAP) {
        truncated = true;
        return;
      }
      const next = current + text.slice(0, OUTPUT_CAP - current.length);
      if (next.length >= OUTPUT_CAP) {
        truncated = true;
      }
      if (which === "stdout") {
        stdout = next;
      } else {
        stderr = next;
      }
    };
    child.stdout?.on("data", (chunk: Buffer) => take(chunk, "stdout"));
    child.stderr?.on("data", (chunk: Buffer) => take(chunk, "stderr"));
    child.on("error", (error) => {
      resolve({
        exitCode: null,
        stdout,
        stderr: stderr || error.message,
        truncated,
      });
    });
    child.on("close", (code) => {
      resolve({ exitCode: code, stdout, stderr, truncated });
    });
  });
}

export default defineTool({
  description:
    "Run one shell command on this machine, from the user home directory. Deletes, sending data off the machine, and system-setting changes are not executed; those return needsConfirmation instead.",
  execution: "server",
  effect: "write",
  inputSchema: z.object({
    command: z.string().trim().min(1).max(2000),
  }),
  async execute({ command }) {
    const gated = needsConfirmation(command);
    if (gated !== undefined) {
      return gated;
    }
    return runShell(command);
  },
});
