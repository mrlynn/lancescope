import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Proxy the FastAPI layer so the browser talks to one origin and Range requests
    // on /api/video/* reach Lance untouched.
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/:path*" }];
  },
};

export default nextConfig;
