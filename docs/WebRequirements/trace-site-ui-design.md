# Trace 站前端优化设计（2026-08）

状态：已实施（typecheck / test / build 全绿），待维护者本地验收。
分支 `feature/trace-site-motion-polish`。

## 背景与诊断

`trace-site` 是 Agent 运行证据的公开展示站（Next.js 15 App Router + Tailwind v4，
深色 observability console 基调）。现有动效体系已经统一：单一缓动曲线
`cubic-bezier(0.16, 1, 0.3, 1)`、三档交互语汇（`.lift` / `.glow-strong` /
`.row-hover`）、聚光边框、胶片颗粒、骨架屏、CountUp，且 reduced-motion 与无 JS
降级完整。本次优化不推倒重来，只补四个真实缺口：

1. **动画在看不见的地方播完**：所有 `anim-rise` 在 mount 时触发。详情页证据卡、
   about 页下半部分，用户滚动到时动画早已结束。
2. **阶段切换没有连续性**：`StageDetail` 靠 key 重建原地淡入，切换方向无空间隐喻。
3. **首页缺少视觉主体**：站点核心故事是七段流水线，但 hero 只是两行文字，
   精选案例里的 StageChips 是静态终态。
4. **视觉语言靠复制维持**：页头 + grid-backdrop 三处手写、mock 横幅两处复制、
   `Kpi`（home）与 `Stat`（RunDetail）是同一规则的两份实现。

另有功能缺口：运行记录页约百条数据零筛选。

## 目标与硬约束

- 动画发生在视线所及之处；阶段切换有方向感；首页有一个代表"Agent 在执行"的
  标志性动效；结构组件归位；运行列表可按结论筛选。
- 不编造数据（KPI 趋势线、成本磁贴此前已因无真实数据源被否决，维持）。
- 不引入动效库，CSS 语汇保持 globals.css 注释契约下的单一体系。
- SSR / 无 JS 内容完整可见；`prefers-reduced-motion` 全局降级；脱敏边界不变。

## 方案

### A. 结构抽取（重构，无行为变化）

| 组件 | 位置 | 说明 |
|------|------|------|
| `PageHeader` | `components/shell/PageHeader.tsx` | 收拢"标题 + 描述 + grid-backdrop"，home/runs 带 backdrop，about 不带 |
| `MockBanner` | `components/ui/mock-banner.tsx` | 构造数据横幅三处合一，统一全角标点 |
| `StatCard` | `components/ui/stat-card.tsx` | 合并 `Kpi`/`Stat`：label + count/value 二选一 + 注脚 + 入场延迟 |

### B. 动效基础设施

- **Reveal 滚动显现**（`components/ui/reveal.tsx`）：
  - layout 注入一行内联脚本给 `<html>` 加 `has-js`，初始隐藏态只在
    `html.has-js .reveal` 下生效 → 无 JS 永远静态可见；
  - IntersectionObserver 进入视口后加 `is-visible` 触发 rise/fade；
  - **动画结束即移除动画类**（animationend → done 态），避免 fill-mode 钉死
    transform 导致 `.lift:hover` 上浮失效 —— 这是与现有 hover 语汇共存的关键；
  - reduced-motion 直接呈现终态。
- **阶段切换方向过渡**：RunDetail 记录新旧 `activeIndex` 差值，
  `StageDetail` 重建时按方向走 `slide-forward` / `slide-back`（复用统一缓动）。
- StageChips 的连接线生长动画保持 mount 触发（编排与 mount 绑定，
  改为视口触发需要级联 gate，收益不成比例，记录为已知取舍）。

### C. 标志性动效：真实运行重放 —— 已否决并撤下

首版实现了按真实耗时压缩的重放时间轴（进入视口播一次 + 重播按钮）。
验收过程中发现光标实现为全宽填充条会盖住分段（已改为细线平移），
随后维护者于 2026-08-23 决定整体撤下：**精选案例保持静态 StageChips
呈现，不做重放动画**。`PipelineReplay` 组件已删除。

若未来重启此功能，保留两条教训：
- 光标必须用细线随容器平移，绝不能用全宽填充条缩放（fill-mode 会永久
  盖住内容）；
- 展示性动画先给维护者看静态稿再动。

### D. 功能与打磨

- **运行记录筛选 + 分页**（`lib/run-filters.ts` + `components/run/RunsExplorer.tsx`）：
  状态分组 chips + run_ref/title/category 文本搜索；每页 20 条客户端分页
  （上一页/下一页 + 页码指示），筛选或搜索变化自动回到第 1 页。
  分组沿用 `describeOutcome` 的 tone 归类（已确认）：全部 / 已建 PR(good) /
  未修复(warn) / 失败·拦截(critical+serious) / 其他结论(neutral) / 进行中(accent)。
- **标题阶梯升一级**：h1 用 `text-3xl lg:text-4xl tracking-tight`，
  同步更新 globals.css 字号契约注释。
- **移动导航激活胶囊**：MobileNav 当前项加 `bg-accent/12 rounded-full` 底色，
  对齐侧栏语汇。
- **顶部导航替代侧栏**（2026-08-23 验收反馈）：仅三个入口的侧栏横向占
  w-60 且大部分区域空置；改为全宽吸顶头部（`components/shell/Header.tsx`，
  品牌居左、胶囊导航居右，原 MobileNav 样式升格而来），内容区取回全部
  宽度。原侧栏「数据来源」注脚删除 —— 项目说明页已有更完整的同名章节。
- **Diff 两层展开**（2026-08-23 验收反馈调整）：文件层默认只显示文件列表
  （路径 + 增删统计），点击展开该文件 diff；行数层对超 24 行的文件保留
  "展开其余 N 行"二次折叠。均复用 `grid-rows-[0fr→1fr]` 过渡模式。

## 已确认决定

1. 重放：进入视口播一次 + 重播按钮。
2. 筛选分组：沿用 `describeOutcome` 口径，不发明第二套文案。
3. 设计文档放 `docs/WebRequirements/`，git 管理、不忽略
   （根 .gitignore 已增加豁免行）。

## 明确拒绝的方案

| 方案 | 理由 |
|------|------|
| framer-motion / motion | 现有 CSS 语汇是有契约的单一体系，引库 = 两套范式并存 + ~40KB |
| View Transitions 路由过渡 | Next 15 中仍 experimental，Firefox 未默认启用；相对现有逐页入场增益有限，列为后续可选 |
| 首页七阶段漏斗统计图 | `getRunList` 有 100 条上限会静默失真；与重放争夺同一位置 |
| diff 语法高亮 | diff 体量小，正则着色误报率与维护成本大于收益 |
| 明亮主题 | 与 console 定位冲突 |

## 实施顺序

1. A 结构抽取 → 2. B Reveal 系统 → 3. B 方向过渡 → 4. D6 筛选 →
5. C 重放 → 6. D7-D8 打磨。

## 验收清单

- [ ] `npm run typecheck`、`npm test`、`npm run build` 全绿。
- [ ] 无 JS（禁用 JS 直开页面）：全部内容可见，无隐藏元素。
- [ ] `prefers-reduced-motion: reduce`：无位移动画，重放直接呈现终态，
      CountUp 不滚动，骨架静止。
- [ ] 详情页：滚到下方才触发证据卡入场；hover 上浮在 reveal 完成后仍生效。
- [ ] 阶段切换：从第 7 切到第 2 时内容从左侧滑入，相邻切换方向正确。
- [ ] 筛选：五组归类与表格内 StatusBadge 文案一致；搜索命中 run_ref/类别；
      每页 20 条，翻页/筛选/搜索互不串页。
- [ ] 精选案例：静态 StageChips，无重放动画。
- [ ] 顶部导航在桌面与窄屏均吸顶可达，激活态胶囊正确跟随路由。
- [ ] Diff：文件行点击展开/收起；超 24 行文件展开后仍有"展开其余 N 行"。
- [ ] 键盘 Tab 可达所有交互元素，focus ring 正常。
