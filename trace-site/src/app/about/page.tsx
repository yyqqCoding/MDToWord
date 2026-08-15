import {
  Boxes,
  Database,
  FileLock2,
  GitPullRequest,
  Github,
  ListChecks,
  ShieldCheck,
  SquareStack,
  XCircle,
} from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { RUN_STAGES } from "@/lib/run-graph";

/**
 * 项目说明。
 *
 * 早期版本是两张并排的大段文字卡，信息全堆在一起、无法扫读。
 * 这些内容本身是结构化的：脱敏规则天然是「原始内容 → 保留什么」的对照表，
 * 能力边界天然是一组并列条目。按结构呈现，而不是压成段落。
 */

const MASKING = [
  { source: "用户 Markdown", kept: "SHA-256 哈希、字节数、分类摘要" },
  { source: "源码与补丁", kept: "文件路径、增删行数、SHA-256" },
  { source: "模型输入输出", kept: "结构化结果摘要" },
  { source: "沙箱 stdout / stderr", kept: "截断后的错误码" },
  { source: "联系方式", kept: "从不上传，任何环节都不出现" },
];

const BOUNDARIES = [
  {
    icon: FileLock2,
    title: "只改后端白名单文件",
    detail: "扩展、依赖、Dockerfile 与部署配置都不在可写范围内。",
  },
  {
    icon: ListChecks,
    title: "断言来自登记列表",
    detail: "只能从已注册的 Oracle 中选择，不能提交可执行表达式。",
  },
  {
    icon: Boxes,
    title: "隔离沙箱执行",
    detail: "所有测试与补丁在容器中运行，容器内没有任何凭据。",
  },
  {
    icon: GitPullRequest,
    title: "验证通过才建 PR",
    detail: "绝不自动合并、绝不自动部署，最终由人审核。",
  },
  {
    icon: XCircle,
    title: "复现不了就放弃",
    detail: "进入 cannot_reproduce，不提交空修复凑数。",
  },
];

const SOURCES = [
  { icon: Database, title: "Supabase", detail: "运行摘要、状态与用量" },
  { icon: SquareStack, title: "Langfuse", detail: "逐次调用的执行 Trace" },
  { icon: Github, title: "GitHub", detail: "已合并 PR 的公开 diff" },
];

export default function AboutPage() {
  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-7 lg:px-8">
      <header className="anim-rise mb-8">
        <h1 className="text-2xl font-semibold text-ink">项目说明</h1>
        <p className="mt-2 max-w-3xl text-base leading-relaxed text-ink-muted">
          用户提交一条反馈，Agent 自动分类、复现、修复、验证，最后提交 Pull Request。
          这一页讲清楚它能做什么、不能做什么，以及为什么站点上看不到原始内容。
        </p>
      </header>

      <Card className="mb-6" delay={60}>
        <CardHeader title="处理流程" description="固定七步，每一步都留有可审计的执行证据" />
        <ol className="flex flex-col gap-2 p-5 xl:flex-row xl:items-stretch xl:gap-0">
          {RUN_STAGES.map((stage, index) => (
            <li
              key={stage.key}
              className="anim-rise flex min-w-0 flex-1 items-center"
              style={{ animationDelay: `${120 + index * 60}ms` }}
            >
              <div className="lift group h-full w-full rounded-lg border border-line bg-canvas px-3.5 py-3 hover:border-accent/50">
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="flex size-5 shrink-0 items-center justify-center rounded bg-raised font-mono text-xs text-ink-faint transition-colors duration-200 group-hover:bg-accent/20 group-hover:text-accent"
                  >
                    {index + 1}
                  </span>
                  <span className="truncate text-sm font-medium text-ink">{stage.label}</span>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-ink-faint">{stage.hint}</p>
              </div>
              {index < RUN_STAGES.length - 1 && (
                <span aria-hidden className="hidden h-px w-3 shrink-0 bg-line-strong xl:block" />
              )}
            </li>
          ))}
        </ol>
      </Card>

      <Card className="mb-6" delay={120}>
        <CardHeader
          title="Trace 为什么看不到原始内容"
          description="观测数据默认脱敏；下面是每类内容实际保留了什么"
          aside={<ShieldCheck aria-hidden className="size-5 text-ink-faint" />}
        />
        <ul className="divide-y divide-line/60">
          {MASKING.map((row, index) => (
            <li
              key={row.source}
              className="anim-fade row-hover grid gap-1 px-5 py-3.5 hover:bg-raised/50 sm:grid-cols-[14rem_1fr] sm:gap-4"
              style={{ animationDelay: `${180 + index * 50}ms` }}
            >
              <span className="text-sm text-ink">{row.source}</span>
              <span className="text-sm leading-relaxed text-ink-muted">{row.kept}</span>
            </li>
          ))}
        </ul>
        <p className="border-t border-line px-5 py-4 text-sm leading-relaxed text-ink-muted">
          因此本站展示的是<span className="text-ink">执行结构与判定依据</span>，
          而不是内容本身。这是设计选择，不是数据缺失。
        </p>
      </Card>

      <section className="mb-6">
        <h2 className="anim-rise mb-3 text-base font-semibold text-ink">Agent 的能力边界</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {BOUNDARIES.map((item, index) => {
            const Icon = item.icon;
            return (
              <div
                key={item.title}
                className="anim-rise lift group rounded-xl border border-line bg-surface p-5 hover:border-accent/40"
                style={{ animationDelay: `${240 + index * 60}ms` }}
              >
                <Icon
                  aria-hidden
                  className="size-5 text-accent transition-transform duration-200 group-hover:scale-110"
                />
                <p className="mt-3 text-sm font-medium text-ink">{item.title}</p>
                <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">{item.detail}</p>
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <h2 className="anim-rise mb-3 text-base font-semibold text-ink">数据来源</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {SOURCES.map((item, index) => {
            const Icon = item.icon;
            return (
              <div
                key={item.title}
                className="anim-rise lift group flex items-center gap-3 rounded-xl border border-line bg-surface px-5 py-4 hover:border-accent/40"
                style={{ animationDelay: `${560 + index * 60}ms` }}
              >
                <Icon
                  aria-hidden
                  className="size-5 shrink-0 text-ink-faint transition-colors duration-200 group-hover:text-accent"
                />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">{item.title}</p>
                  <p className="text-sm text-ink-muted">{item.detail}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
