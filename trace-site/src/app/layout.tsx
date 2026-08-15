import type { Metadata } from "next";
import { MobileNav, Sidebar } from "@/components/shell/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "MD To Word · 修复 Agent Trace",
  description:
    "展示用户反馈如何被自动分类、复现、修复、验证并提交 Pull Request 的完整执行证据。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">
        <div className="flex min-h-dvh">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <MobileNav />
            <main className="min-w-0 flex-1">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
