import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 展示站全程只读，不需要图片优化以外的任何服务端能力。
  poweredByHeader: false,
  // 允许把构建产物写到独立目录，避免验证构建与正在运行的 dev server 争用 .next。
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;
