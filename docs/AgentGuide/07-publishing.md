# 发布：把验证结果交给维护者

## 1. 发布边界

模型没有 GitHub 工具。只有外层 Publisher 能创建 PR 或 Issue，且发布前必须检查受信
Artifact。Agent 不合并、不部署、不关闭 Issue、不修改 Actions 或 Secrets。

## 2. 后端 Bug 的 PR

PR Publisher 只接受 final validation 通过的结果，并再次检查：

~~~text
base_sha 仍对应目标 main
patch hash 与 validated Artifact 一致
测试/修复路径、文件数和差异满足 Policy
没有重复 marker 或已有同一 patch 的 PR
~~~

PR 正文包含脱敏问题摘要、基线失败、目标和全量测试、DOCX 验证、修改文件、风险、
Prompt/模型版本、用量、base_sha、patch hash 和 Trace URL。完整用户 Markdown、联系方式、
源码片段、密钥和原始日志不进入正文。

## 3. 功能和前端问题的 Issue

Gate 将功能需求、前端/扩展缺陷路由为 issue_required。该分支不创建源码快照、Sandbox
或 PR，只由 Issue Publisher 用固定标签和脱敏摘要创建 Issue。Issue 标题、正文和 marker
经过 Schema 和敏感信息检查。

历史 route 值只用于展示；不要批量把历史记录重新发布。

## 4. 幂等和 stale_base

发布请求使用 run、fingerprint、patch hash 和固定 marker。网络超时后恢复时，Publisher
先查询已有分支、PR 或 Issue，再决定是否创建，避免重复外部写入。

如果 main 在验证后变化，返回 stale_base。系统不把旧补丁直接 rebase 到新代码，而是按
既有一次性重排重新获取快照、复现和验证；超过限制进入人工处理。

## 5. 维护者审核

维护者至少检查：

1. 测试真的复现了用户问题，而不是宽松断言；
2. 修复只改允许的后端文件，逻辑足够小；
3. 全量测试和 DOCX 证据可信；
4. 没有删除/跳过测试、增加网络或依赖；
5. PR/Issue 和 Trace 没有敏感信息；
6. 合并后使用原 Markdown 在 Render 和 Word 中人工回放。

GitHub App 使用最小权限。合并、部署和扩展商店发布属于维护者流程，不由 Agent 自动化。
