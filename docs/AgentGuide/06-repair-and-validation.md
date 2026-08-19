# 生成修复与最终验证

## 1. 进入条件

只有复现阶段已经得到一条有效失败测试，才会进入修复。此时State至少包含：

```text
base_sha
source_snapshot_ref
reproduction_plan_ref
test_patch_ref
reproduction_result_ref
```

如果复现失败、测试被安全拒绝或问题需要修改扩展，系统不会调用修复模型。

## 2. generate_fix_edit：生成最小修复

修复模型看到：

- 脱敏反馈摘要；
- 复现计划和目标失败摘要；
- 新增测试的有限摘要；
- 当前允许读取的业务源码；
- 上一轮修复失败原因；
- 剩余模型、工具和Docker预算。

它返回结构化`Edit[]`，不能输出任意命令。当前只能修改：

```text
backend/app/normalizer.py
backend/app/pandoc_runner.py
```

`backend/app/mermaid_renderer.py`可以作为受信任接口读取，但模型不能修改它。依赖、Dockerfile、
部署配置、测试和浏览器扩展都不在修复白名单中。

## 3. 修复补丁检查

Agent主进程把Edit转换成`fix.patch`前检查：

- `search`必须逐字匹配固定源码中的唯一片段；
- 不允许整文件覆盖业务源码；
- 不允许修改或删除测试补丁；
- 不允许新增网络、动态执行、任意文件读取或环境密钥读取；
- 不允许捕获所有异常后返回空DOCX；
- 不允许关闭现有检查或只增加超时；
- 不允许新增依赖或要求修改部署；
- 总文件数、增删行数和补丁大小不能超限。

需要新增依赖或修改部署时，结果进入`needs_human`，不会为了完成自动化而扩大模型权限。

## 4. 目标测试验证

通过Policy后，Agent提交`validate_target`：

```text
固定源码快照 + test.patch + fix.patch
```

Worker创建新容器，运行同一个目标测试。结果分为：

- 目标测试通过：进入最终验证；
- 目标测试仍失败：生成最多4 KB的失败摘要，进入下一轮修复；
- JUnit无效、容器失败或超时：按明确错误处理；
- 工作区被越权修改：`security_rejected`。

修复最多两轮。第二轮仍然从原始`base_sha`生成完整修复，不在第一轮临时工作区上继续修改。

## 5. 为什么还要最终验证

目标测试通过只能证明一个场景，不能证明：

- 新测试在原始代码上仍然失败；
- 修复没有破坏其他转换行为；
- DOCX仍然是合法文件；
- 复现容器中的缓存没有影响结果；
- 最终发布的补丁与测试时完全一致。

因此`validate_final`使用全新容器执行独立验证。

## 6. 最终验证执行什么

```text
步骤1：从base_sha重新建立干净源码
步骤2：只应用test.patch
步骤3：重新确认目标测试按预期失败
步骤4：再应用fix.patch
步骤5：确认目标测试通过
步骤6：运行后端全量pytest
步骤7：运行登记的DOCX结构检查
步骤8：检查workspace没有未授权修改
步骤9：生成validated.patch及SHA-256
```

只有全部通过，才会得到：

```text
ValidationResult.passed = true
validated_patch_sha256 = <64位SHA-256>
```

## 7. validated.patch为什么重要

Publisher不会重新相信模型输出，也不会随意组合之前的文件。它只接受最终验证结果中绑定的
`validated.patch`：

1. 读取`validation_result_ref`；
2. 检查`passed=true`；
3. 读取`validated.patch`；
4. 重新计算SHA-256；
5. 必须与`validated_patch_sha256`完全一致；
6. 应用后再次检查最终diff。

这保证“送去创建PR的代码”就是“刚刚通过独立验证的代码”。

## 8. 本阶段可能怎样结束

| 结果 | 处理 |
|---|---|
| 目标和全量验证通过 | 进入发布 |
| 两轮修复仍失败 | 记录失败并结束 |
| 需要新依赖或部署修改 | `needs_human` |
| 补丁越权或工作区被修改 | `security_rejected` |
| 模型/工具/沙箱预算耗尽 | `budget_exhausted` |
| Docker或Worker暂时不可用 | 记录稳定错误码，不在主机直接执行 |

对应实现：

- [agent/repair.py](../../agent/repair.py)
- [agent/domain/repair.py](../../agent/domain/repair.py)
- [agent/workspace/validation.py](../../agent/workspace/validation.py)
- [agent/graph.py](../../agent/graph.py)
