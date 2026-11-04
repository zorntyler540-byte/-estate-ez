import { PHASE_PRODUCTION_BUILD } from "next/constants.js";

import { checkApiBase } from "./lib/apiBaseRule.mjs";

/** @type {import('next').NextConfig} */
// Standalone is for Docker/Railway (`Dockerfile.web`). Vercel sets VERCEL=1; default output avoids heavier trace + OOM on small build runners.
const isVercel = process.env.VERCEL === "1";

const nextConfig = {
  ...(isVercel ? {} : { output: "standalone" }),
  // Allow opening the dev app as http://127.0.0.1:3000 while Next binds as localhost (or vice versa).
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default function config(phase) {
  if (phase === PHASE_PRODUCTION_BUILD) {
    const { error } = checkApiBase(process.env.NEXT_PUBLIC_API_BASE, { production: true });
    if (error) throw new Error(error);
  }
  return nextConfig;
}
