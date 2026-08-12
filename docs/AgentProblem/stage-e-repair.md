# 阶段 E：修复循环与独立验证问题/解决方案

## 问题 1：最初把所有新增外部依赖都视为不可自动修复

Mermaid 无法仅靠 Pandoc 原生转换为期望的 Word 图形；固定镜像没有渲染器时，正确修复
必然涉及平台依赖。完全禁止依赖会让真实、可解决的问题永久停在
`external_dependency_required`。

### 解决方案

调整口径：模型仍不能修改依赖或部署文件，但维护者可以审核真实需求并预装固定版本平台
能力。生产与 Sandbox 同时加入 `@mermaid-js/mermaid-cli 11.16.0`、固定 Puppeteer、系统
Chromium 和中文字体，并提交锁文件。

## 问题 2：Mermaid 在 Word 中无法保持源码级可编辑

Word/Pandoc 没有把 Mermaid 节点直接转换成 Word 原生流程图形状的稳定内置能力。

### 解决方案

受信 `mermaid_renderer` 在本地把安全 Mermaid 源码渲染为 PNG，再交给 Pandoc 嵌入
DOCX。正文、表格和公式仍尽可能保持原生可编辑；流程图以高清图片呈现，图内节点不可在
Word 中逐个编辑。渲染器限制图数、源码大小、超时，并拒绝外链、HTML、click 和 init。

## 问题 3：模型误判 Mermaid 反馈信息不足

反馈同时包含完整 `graph TD` 源码和明确“导出 Word 不显示”，模型仍把
`sufficient_information` 判为 false，导致 `needs_human`。

### 解决方案

增加窄范围确定性校正：仅在高相关后端 `docx_structure` 分类、完整 Mermaid 证据和明确
导出问题同时成立时忽略单一信息不足判断。注入、低相关、前端和未知类别优先级不变。

## 问题 4：修复模型读取范围不足且不能按序修改同一文件

真实 run 只读取 `pandoc_runner.py` 前 50 行，两轮编辑都无法应用；旧编辑器也把同一文件
的多个顺序 `search_replace` 当作冲突。

### 解决方案

修复阶段读取完整可编辑白名单文件，允许同一文件按顺序应用多个确定性
`search_replace`。失败时只向第二轮提供受信的稳定编辑错误，不把模型 Edit、源码或用户
内容打印到 CLI 和日志。

## 问题 5：修复失败前的模型用量没有及时写入数据库

模型调用完成但尚未进入汇总节点时发生超时，数据库仍停留在旧 checkpoint 的调用数和
Token，无法准确解释成本与失败位置。

### 解决方案

Controller 在失败终结时合并数据库摘要与最新 checkpoint 的单调用用量，再写入稳定终态。
预算在每次模型和 Sandbox 调用前检查，耗尽后禁止继续产生外部调用。

## 问题 6：只验证“修复后测试通过”可能得到伪修复

模型可能弱化测试、触碰 fixture，或者修复目标测试但破坏后端其他转换行为。

### 解决方案

最终验证使用三个全新容器分别证明：只应用 test patch 时基线失败；test+fix 时目标通过；
全量 pytest 和同一 DOCX Oracle 通过。Fix patch 与 test patch 必须修改不同文件集合，最终
`validated.patch` 内容和 `validated_patch_sha256` 必须一致。

## 问题 7：Sandbox 出现 `sandbox_unavailable` 或长时间无输出

Worker 未启动、Credential 不一致、Docker 关闭或当前目录失效时，CLI 无法提交 Job；真实
模型和 Chromium 冷启动期间又可能暂时没有控制台输出。

### 解决方案

先独立检查 Docker、镜像 digest、Worker 端口和认证，再启动 Controller。可恢复运行使用
原 `run_id`；CLI 和 Sandbox 都设置明确超时并返回稳定错误，不用重复领取 feedback 或
无限等待。
