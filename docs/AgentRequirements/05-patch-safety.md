# 阶段 05:Workspace 与补丁安全策略

## 目标

模型产出的任何改动,先经确定性策略检查,再应用到临时工作区;
恶意或越界改动在执行前被拒绝。

## 前置依赖

阶段 02;[security-policy](00-overview/security-policy.md) §1/§3 的白名单与阈值。

## 交付物

```text
agent/workspace.py  agent/patching.py
agent/validators/patch_policy.py
agent/policy.yaml(内容与 security-policy 一致,模型禁改)
agent/tests/test_patch_policy.py  test_patching.py
```

## 实施内容

### 1. 编辑格式与 diff 生成(ADR-006,与原始文档不同)

模型**不直接输出 unified diff**,而是输出结构化编辑:

```json
{
  "edits": [
    { "path": "backend/app/normalizer.py",
      "mode": "full_file",              // 或 "search_replace"
      "content": "<完整文件内容>" },
    { "path": "backend/tests/test_feedback_regressions.py",
      "mode": "search_replace",
      "search": "<唯一上下文片段>", "replace": "<替换内容>" }
  ]
}
```

Harness 在干净基线工作区落盘后执行 `git diff --binary=false` 生成补丁;
后续所有策略检查、验证、发布都作用于这个**确定性生成的 diff**。
`search_replace` 的 `search` 必须在目标文件中恰好匹配一次,否则拒绝。

### 2. Workspace(`workspace.py`)

记录基线 commit;本地用 `git worktree add <tmp> <base-sha>` 建临时工作区
(CI 中各 Job checkout 天然隔离);应用 test/fix patch;恢复干净状态
(`git checkout -- . && git clean -fd`);生成最终 combined patch。

### 3. Patch Policy(`patch_policy.py`)

白名单/黑名单/阈值从 `policy.yaml` 读取,与
[security-policy §1/§3](00-overview/security-policy.md) 保持一致;
检查顺序按 security-policy §3 的 10 步执行。
命中黑名单 → 状态 `security_rejected`,不重试。

## 验收清单

以下用例全部在 `agent/tests/test_patch_policy.py` 中固化,`python -m pytest agent/tests -q` 通过:

- [ ] 修改 `.github/workflows/**` 的编辑被拒绝;
- [ ] 修改 `extension/**` 被拒绝;
- [ ] 修改 `backend/app/settings.py` / `backend/pyproject.toml` / `Dockerfile` 被拒绝;
- [ ] 新增二进制文件被拒绝;文件权限改为可执行被拒绝;符号链接被拒绝;
- [ ] 超过 `MAX_ADDED_LINES` / `MAX_CHANGED_FILES` 被拒绝;
- [ ] 删除 `backend/app/reference.docx` 被拒绝;
- [ ] 大量删除现有测试(删除行数超阈值)被拒绝;
- [ ] `search_replace` 匹配 0 次或 ≥2 次被拒绝;
- [ ] 合法编辑(只改 `normalizer.py` + 新增测试)通过全部 10 步检查,
      生成的 diff 可 `git apply --check`;
- [ ] `compileall` 对含语法错误的编辑返回失败。

## 状态

未开始

## 验收记录

(完成后填写)
