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
import { PageHeader } from "@/components/shell/PageHeader";
import { Reveal } from "@/components/ui/reveal";
import { Spotlight } from "@/components/ui/spotlight";
import { RUN_STAGES } from "@/lib/run-graph";

/**
 * 项目说明。
 *
 * 上一版把内容压进两张并排的大卡片：七步流程横向挤成一行（标题被截断），
 * 脱敏对照表装在重型卡片里，整页是"盒子套盒子"，扫读吃力。
 *
 * 这一版改成文档式开放版面：
 *   - 流程改竖向轨道，每一步独占一行，标题与说明都有完整空间；
 *   - 脱敏对照脱掉卡片外框，用细分隔线组织；
 *   - 结论从卡片页脚改为左侧主色竖线的引注；
 *   - 数据来源不再装盒子，图标块 + 文字直接排在网格里。
 * 留白和分隔线替代边框，是这页的主要节奏。
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

function SectionHeader({
  title,
  description,
  aside,
  delay = 0,
}: {
  title: string;
  description?: string;
  aside?: React.ReactNode;
  delay?: number;
}) {
  return (
    /* 本页各 section 大多在首屏以下，标题随区块进入视口再入场 */
    <Reveal
      as="header"
      delay={delay}
      className="mb-6 flex items-start justify-between gap-4"
    >
      <div>
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        {description && (
          <p className="mt-1.5 text-sm leading-relaxed text-ink-faint">{description}</p>
        )}
      </div>
      {aside}
    </Reveal>
  );
}

export default function AboutPage() {
  return (
    <div className="mx-auto w-full max-w-4xl px-5 py-7 lg:px-8">
      {/* 文档式版面：无网格纹理；间距由各 section 的 mt 控制，头部不额外留 mb */}
      <PageHeader
        title="项目说明"
        description="用户提交一条反馈，Agent 自动分类、复现、修复、验证，最后提交 Pull Request。这一页讲清楚它能做什么、不能做什么，以及为什么站点上看不到原始内容。"
        className=""
      />

      {/* 处理流程：竖向轨道，主色填充沿轨道向下生长，节点错峰入场。
          该区块位于首屏内且编排与 mount 绑定（连接线生长、逐项延迟），
          保持 mount 即播；下方首屏外的区块才走 Reveal。 */}
      <section className="mt-14">
        <SectionHeader
          title="处理流程"
          description="固定七步，每一步都留有可审计的执行证据"
          delay={60}
        />
        <ol className="relative">
          {/* 轨道：底色线 + 主色生长线，两条重叠 */}
          <span
            aria-hidden
            className="absolute bottom-2 left-[13px] top-2 w-px bg-line"
          />
          <span
            aria-hidden
            className="anim-grow-y absolute bottom-2 left-[13px] top-2 w-px bg-accent/60"
            style={{ animationDelay: "200ms" }}
          />
          {RUN_STAGES.map((stage, index) => (
            <li
              key={stage.key}
              className="anim-rise group relative flex gap-4 pb-7 last:pb-0"
              style={{ animationDelay: `${100 + index * 70}ms` }}
            >
              <span
                aria-hidden
                className="relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border border-line bg-surface font-mono text-xs text-ink-faint transition-colors duration-200 group-hover:border-accent/60 group-hover:text-accent"
              >
                {index + 1}
              </span>
              <div className="min-w-0 pt-0.5">
                <p className="text-sm font-medium text-ink">{stage.label}</p>
                <p className="mt-1 max-w-xl text-sm leading-relaxed text-ink-faint">
                  {stage.hint}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* 脱敏对照：开放表格，只用分隔线；结论做成引注 */}
      <section className="mt-16">
        <SectionHeader
          title="Trace 为什么看不到原始内容"
          description="观测数据默认脱敏；下面是每类内容实际保留了什么"
          aside={<ShieldCheck aria-hidden className="mt-0.5 size-5 shrink-0 text-ink-faint" />}
          delay={60}
        />
        <ul className="border-y border-line/60">
          {MASKING.map((row, index) => (
            <Reveal
              as="li"
              key={row.source}
              variant="fade"
              delay={index * 50}
              className="row-hover grid gap-1 border-b border-line/60 px-2 py-4 last:border-b-0 hover:bg-raised/40 sm:grid-cols-[12rem_1fr] sm:gap-6"
            >
              <span className="text-sm text-ink">{row.source}</span>
              <span className="text-sm leading-relaxed text-ink-muted">{row.kept}</span>
            </Reveal>
          ))}
        </ul>
        <Reveal
          as="p"
          variant="fade"
          delay={120}
          className="mt-6 border-l-2 border-accent/60 pl-4 text-sm leading-relaxed text-ink-muted"
        >
          因此本站展示的是<span className="text-ink">执行结构与判定依据</span>，
          而不是内容本身。这是设计选择，不是数据缺失。
        </Reveal>
      </section>

      {/* 能力边界：保留卡片，但只在整页一个区块使用盒子 */}
      <section className="mt-16">
        <SectionHeader
          title="Agent 的能力边界"
          description="五条硬约束，写进执行路径而不是靠自觉"
          delay={60}
        />
        {/* 网格子项由外层 Reveal 承担入场，内层 Spotlight 只负责聚光边框；
            h-full 保证同一行卡片等高 */}
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {BOUNDARIES.map((item, index) => {
            const Icon = item.icon;
            return (
              <Reveal key={item.title} delay={index * 60}>
                <Spotlight className="lift panel group h-full rounded-xl border border-line bg-surface p-5 hover:border-accent/40">
                  <Icon
                    aria-hidden
                    className="size-5 text-accent transition-transform duration-200 group-hover:scale-110"
                  />
                  <p className="mt-3.5 text-sm font-medium text-ink">{item.title}</p>
                  <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">{item.detail}</p>
                </Spotlight>
              </Reveal>
            );
          })}
        </div>
      </section>

      {/* 数据来源：不装盒子，图标块 + 文字直接排开 */}
      <section className="mt-16">
        <SectionHeader title="数据来源" delay={60} />
        <div className="grid gap-x-8 gap-y-6 sm:grid-cols-3">
          {SOURCES.map((item, index) => {
            const Icon = item.icon;
            return (
              <Reveal
                key={item.title}
                delay={index * 60}
                className="group flex items-center gap-3.5"
              >
                <span className="panel flex size-10 shrink-0 items-center justify-center rounded-lg border border-line bg-surface transition-colors duration-200 group-hover:border-accent/40">
                  <Icon
                    aria-hidden
                    className="size-5 text-ink-faint transition-colors duration-200 group-hover:text-accent"
                  />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">{item.title}</p>
                  <p className="mt-0.5 text-sm text-ink-muted">{item.detail}</p>
                </div>
              </Reveal>
            );
          })}
        </div>
      </section>
    </div>
  );
}
