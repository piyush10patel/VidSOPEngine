import { randomBytes } from "crypto";
import type { NextConfig } from "next";

// One build ID per `next build` invocation, baked into both client and
// server bundles. Drives the per-deploy CACHE_VERSION in /sw.js so the
// browser detects each deploy as a new service worker and runs the
// install/activate cycle (otherwise the old SW would stay forever).
const buildId =
  process.env.NEXT_PUBLIC_BUILD_ID
  || process.env.VERCEL_GIT_COMMIT_SHA
  || process.env.RENDER_GIT_COMMIT
  || randomBytes(8).toString("hex");

const nextConfig: NextConfig = {
  reactCompiler: true,
  generateBuildId: async () => buildId,
  env: { NEXT_PUBLIC_BUILD_ID: buildId },
};

export default nextConfig;
