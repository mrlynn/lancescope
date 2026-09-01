import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Proxy the FastAPI layer so the browser talks to one origin and Range requests
    // on /api/video/* reach Lance untouched.
    // Overridable so a second checkout can run its own API on another port without
    // fighting the first for :8000. Unset, this is exactly what it always was.
    const api = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${api}/:path*` }];
  },
};

export default nextConfig;
