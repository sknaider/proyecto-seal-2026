import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/bridge/:path*", destination: "http://127.0.0.1:8765/:path*" },
      { source: "/studio/:path*", destination: "http://127.0.0.1:8800/:path*" },
    ];
  },
};

export default nextConfig;
