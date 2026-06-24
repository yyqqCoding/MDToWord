# 📝 MD To Word —— 一键把 AI 输出变成排版规范的 Word

> 把网页 AI（ChatGPT / Claude / DeepSeek / 文心 / 通义……）的回答 **直接复制**，转成可编辑的 `.docx`：
> 表格变 **三线表**，公式变 **可编辑的 Word 公式**，`#` 变 **Word 标题样式**。✨

<p align="left">
  <img alt="backend" src="https://img.shields.io/badge/backend-FastAPI%20%2B%20Pandoc-009688">
  <img alt="extension" src="https://img.shields.io/badge/extension-React%20%2B%20TS%20%2B%20MV3-3178c6">
  <img alt="docx" src="https://img.shields.io/badge/output-editable%20.docx-2b579a">
</p>

---
## 🎯 Edge插件地址

[Edge浏览器插件地址](https://microsoftedge.microsoft.com/addons/detail/md-to-word/cippambdocmalcoceomjlibolaeicmoe?hl=zh-CN)


## 🎯 为什么需要它
AI 网页端给出的内容看着是 Markdown，但**复制到 Word 里往往一团糟** 😩：

- 表格变成一堆 `| --- | --- |` 竖线文字
- 公式变成乱码，或只能截图，**不能编辑**
- `#`、`##` 标题原样保留，没有变成 Word 的标题样式
- 网页 AI 输出的还**不是纯正 Markdown**：行内公式写成 `(z_T)`、块公式用 `\[...\]` 或裸 `[...]`、下标写成 `*{LL}`、有时混入全角竖线 `｜` 或中文破折号……

**MD To Word** 把这些「脏」Markdown 自动修好，再交给 Pandoc 生成排版规范、可继续编辑的 Word 文档。👇

---

## ✨ 核心功能

| 功能 | 说明 |
| --- | --- |
| 📋 **直接复制即用** | 从 AI 网页复制内容，粘进侧边栏，点一下就能导出 `.docx`，无需手动整理格式 |
| 📊 **表格 → 三线表** | Markdown 表格自动转成学术规范的**三线表**（上下粗线 + 表头下细线、无内部网格），可直接用于论文/报告 |
| 🧮 **公式可编辑** | 行内/块级公式转成 **Word 原生公式（OMML）**，不是图片，能在 Word 里继续编辑 |
| 🔠 **标题映射** | `#` → Word 一级标题、`##` → 二级…… `######` → 六级；超过六级自动降为正文 |
| 🇨🇳 **中文排版** | 正文宋体、标题黑体、1.5 倍行距等中文论文常用样式（基于 `reference.docx`） |
| 🛡️ **脏 Markdown 兜底** | 自动修复网页 AI 输出的非标准写法（见下） |
| 👀 **实时预览** | 侧边栏内用 KaTeX + markdown-it 实时预览，所见接近所得 |
| 🗂️ **文件夹管理** | 多对话、多文件夹组织内容，支持批量选择与导出顺序 |

### 🛡️ 兜底修复都修了啥？

为了让「网页 AI 输出」也能稳定转换，转换前会自动归一化以下常见问题：

- 🔧 块公式 `\[...\]` 和裸括号块 `[ \n ... \n ]` → 标准 `$$...$$`
- 🔧 行内 AI 公式 `(z_T)`、`\(...\)` → `$z_T$`（普通文字如 `(注)` 不动）
- 🔧 AI 下标错误 `*{LL}`、`*2` → `_{LL}`、`_2`
- 🔧 表格**紧贴标题缺空行**、**全角竖线 `｜`**、**分隔行用中文破折号 `——`** → 自动补空行、转半角、转 `-`
- 🔧 列表紧贴段落无空行（开启 Pandoc `lists_without_preceding_blankline`）
- 🔧 标题缺空格 `#标题` → `# 标题`
- 🔧 高大括号 / 分数 / 范数等定界符自动放大，避免 Word 公式显示过小
- 🩺 导出后自检：若仍有未被解析的表格残留，会在后端日志告警，便于排查

> 📐 预览（前端 `extension/src/normalizer.ts`）与导出（后端 `backend/app/normalizer.py`）使用**一致的归一化规则**，保证「预览长啥样，Word 就是啥样」。

---

## 🚀 快速开始（普通用户）

1. 🏗️ 构建扩展：在 `extension/` 下执行 `npm install && npm run build`
2. 🧩 打开浏览器扩展页：`chrome://extensions` 或 `edge://extensions`
3. 🛠️ 打开「开发者模式」
4. 📂 选择「加载已解压的扩展程序」，指向 `extension/dist`
5. 🖱️ 点击扩展图标，打开侧边栏
6. 📋 把 AI 回答复制进去 → 预览确认 → 点击 **导出 Word** 🎉

> 默认连接线上转换服务。
> 本地自建后端时，把服务地址改为 `http://127.0.0.1:8000`。

---

## 🧱 项目结构

```text
backend/    🐍 FastAPI + Pandoc 转换服务（归一化 → Pandoc → 三线表后处理）
extension/  🧩 浏览器扩展（React + TypeScript + Manifest V3 侧边栏）
docs/       📚 设计文档、部署说明、示例与使用文档
logs/       🧪 调试样例与效果图
```

### 🔁 转换流程

```text
AI 网页内容
   │  复制粘贴
   ▼
扩展侧边栏 ──(实时预览: markdown-it + KaTeX)
   │  POST /convert
   ▼
后端 normalize_markdown()  🛡️ 修复脏 Markdown
   │
   ▼
Pandoc（math + tables 扩展 + reference.docx 中文样式）
   │
   ▼
三线表后处理 + 导出自检  🩺
   │
   ▼
可编辑的 .docx  📄✅
```

---

## 🛠️ 开发与验证

### 后端 🐍

```bash
cd backend
uv venv .venv
uv pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload      # 启动服务
.venv/bin/python -m pytest -v                 # 运行测试
```

> Pandoc 用于 `.docx` 转换。开发依赖含 `pypandoc_binary`，本地测试无需单独安装 Pandoc。

### 扩展 🧩

```bash
cd extension
npm install
npm run dev      # 开发预览
npm run build    # 构建 dist（tsc + vite）
```

### Docker 🐳

```bash
cd backend
docker build -t md-to-word-backend .
```

---

## ✅ 人工验收清单

用 Microsoft Word 打开导出的 `.docx`，确认：

- [ ] 📝 正文文字可读、样式正常
- [ ] 📊 表格是 Word 原生表格，且为三线表样式
- [ ] 🧮 公式是可编辑的 Word 公式（不是图片）
- [ ] 🔠 标题层级映射正确（`#` → 一级标题……）

---

## ☁️ 部署

- 后端从 `backend/` 部署到 Render，健康检查路径 `/health`。
- 扩展不部署到 Render：构建 `extension/dist` 后在浏览器刷新「已解压扩展」即可。

---

## 🙌 适用场景

- 🎓 写论文 / 课程作业：让 AI 生成内容，复制即得规范 Word（三线表 + 可编辑公式）
- 📑 写报告 / 文档：免去手动调格式
- 🧑‍🏫 教学演示：Markdown 转 Word 的工程实践参考


## 友情链接
[LinuxDO](https://linux.do)
