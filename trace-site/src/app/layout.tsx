import type { Metadata } from "next";
import { Header } from "@/components/shell/Header";
import "./globals.css";

export const metadata: Metadata = {
  title: "MD To Word · 修复 Agent Trace",
  description:
    "展示用户反馈如何被自动分类、复现、修复、验证并提交 Pull Request 的完整执行证据。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        {/* 首帧前打标：滚动显现的初始隐藏只在该标记下生效，无 JS 时内容完整可见 */}
        <script
          dangerouslySetInnerHTML={{
            __html: `document.documentElement.classList.add("has-js");`,
          }}
        />
      </head>
      <body className="antialiased">
        <div className="flex min-h-dvh flex-col">
          <Header />
          <main className="min-w-0 flex-1">{children}</main>
        </div>
      </body>
    </html>
  );
}
