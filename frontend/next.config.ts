import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    // Pin the workspace root to this app. The repository contains a second
    // package-lock.json (the SMS prototype folder), so Next would otherwise
    // infer the parent directory as the root and emit module ids relative to
    // it, which breaks the React client manifest lookup at runtime.
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
