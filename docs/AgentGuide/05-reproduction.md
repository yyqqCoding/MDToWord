# 复现问题

## 1. 为什么先复现再修复

用户描述可能不完整，当前`main`也可能已经解决问题。如果没有一条能够在原代码上稳定
失败的测试，就无法证明后面的修改真正解决了用户问题。因此系统先生成回归测试，并要求：

```text
原始代码 + 新增测试 = 指定测试按预期方式失败
```

语法错误、导入错误、缺少fixture、容器超时或测试根本没有收集，都不算复现成功。

## 2. prepare_source：固定源码版本

分类通过后，Agent主进程从指定GitHub仓库读取`main`当前SHA，将它记录为`base_sha`，然后
下载该提交的源码快照。

源码快照会：

- 校验仓库和提交格式；
- 计算`source_snapshot_sha256`；
- 保存到本次运行的私有目录；
- 把`base_sha`和`source_snapshot_ref`写入State；
- 后续每轮复现和修复都从这个快照重新开始。

固定版本可以避免任务执行期间`main`变化导致测试和补丁基于不同代码。

## 3. plan_reproduction：模型制定测试计划

模型收到以下信息：

- 已脱敏的反馈类型、描述和Markdown；
- 后端允许处理的问题范围；
- 当前允许读取的源码文件清单；
- 可用的固定DOCX检查类型；
- 目标测试名必须使用的反馈ID前缀。

模型返回严格结构：

```text
hypothesis                 对问题原因的假设
target_test_selector       唯一目标测试名
expected_failure_kind      预期是断言失败还是转换异常
oracle                     怎样判断DOCX结果
files_to_read              后续确实需要读取的文件
extension_sync_possible    是否只能通过前端修改解决
```

模型不能要求运行任意命令，也不能自己提供XPath、Shell或文件路径。`files_to_read`必须逐字
选自Agent提供的允许列表，最多8个。

## 4. 读取源码

Agent根据计划中的`files_to_read`调用受控源码读取器。它不是任意文件工具：

- 只能读取固定`base_sha`快照；
- 只允许规定的后端源码、测试和项目说明；
- 拒绝绝对路径、`..`、仓库外符号链接、`.git`和`.env`；
- 单文件最多80 KB；
- 一次工具输出最多20 KB；
- 行号范围最多1000行；
- 文本搜索是普通字符串匹配，不解释为正则或Shell。

读取结果再次被标记为不可信文本，因为仓库内容本身也可能包含误导模型的文字。

## 5. generate_test_edit：生成结构化测试修改

模型不直接输出Git diff，而是输出`Edit[]`。每个Edit明确说明：

```text
path       修改哪个文件
mode       full_file或search_replace
search     要精确匹配的原文
replace    替换后的内容
content    新文件的完整内容
```

测试阶段只允许：

```text
backend/tests/test_feedback_regressions.py
backend/tests/fixtures/feedback/**/*
```

模型不能修改业务源码、`conftest.py`、依赖、Agent、扩展或部署文件。现有回归测试文件非空
时，只能使用`search_replace`在指定锚点后追加测试，不能重写整个文件。

## 6. 从Edit生成test.patch

受信任的`PatchBuilder`逐项检查Edit：

1. 路径是否属于测试白名单；
2. `search`是否在文件中恰好出现一次；
3. 新建文件扩展名和位置是否合法；
4. 是否引入网络、Shell、环境密钥、pytest Hook等危险能力；
5. 修改文件数、行数和总字节是否超限；
6. 生成补丁后能否干净应用到`base_sha`。

通过后写入：

```text
<run_id>/test.patch
```

State只保存`test_patch_ref`。如果结构化编辑不合法，系统最多进入第二轮，让模型根据明确的
失败原因重新生成完整Edit。

## 7. run_reproduction_in_sandbox：证明原代码失败

Agent提交`reproduce_target`任务，输入为：

```text
固定源码快照 + test.patch + target_test_selector
```

Worker在全新Docker容器中执行固定pytest命令。Agent主进程只根据Worker解析出的JUnit字段
判断结果，不根据stdout中的“passed”文字判断。

复现成功必须同时满足：

- 目标测试确实被pytest收集；
- 目标测试发生计划要求的失败；
- 失败类型与`expected_failure_kind`一致；
- 容器没有超时或安全拒绝；
- 工作区执行前后没有未授权修改。

## 8. 最多两轮

```text
第一轮测试
  ├─ 目标按预期失败 → 复现成功
  ├─ 测试通过 → 告诉模型“没有触发问题”，进入第二轮
  ├─ 测试无效 → 告诉模型具体字段或执行问题，进入第二轮
  └─ 安全拒绝 → 立即结束

第二轮仍不能复现 → cannot_reproduce
```

`cannot_reproduce`是正常业务结果，不等于系统崩溃。它表示在当前`main`和确定性测试条件下
无法再次证明用户问题，因此不能安全生成PR。

## 9. 受信任测试模板

只有两类已明确的问题允许使用固定模板兜底：

- Mermaid源码没有生成drawing；
- 明确的转换崩溃。

当模型结构持续不合法，或者第一轮测试编辑无法形成有效测试时，Agent主进程可以生成
预先编写的固定测试。固定模板仍然经过相同补丁Policy和Docker验证，不会放宽权限。其他
问题类型不会使用该兜底。

## 10. 本阶段写入什么

| 位置 | 内容 |
|---|---|
| 本地运行目录 | `reproduction-plan.json`、`test.patch`、复现结果 |
| State | 计划、测试补丁和复现结果的引用，轮次和用量 |
| `feedback` | `reproducing`、`cannot_reproduce`或后续`repairing` |
| `agent_runs` | 阶段、复现摘要、模型/工具/沙箱用量 |
| Langfuse | 计划、源码读取、编辑提交和沙箱调用的脱敏摘要 |

对应实现：

- [agent/reproduction.py](../../agent/reproduction.py)
- [agent/tools/source.py](../../agent/tools/source.py)
- [agent/tools/edits.py](../../agent/tools/edits.py)
- [agent/graph.py](../../agent/graph.py)

## 11. 结合源码看复现循环

[agent/reproduction.py](../../agent/reproduction.py)的`plan_reproduction()`先让模型返回
`ReproductionPlan`，但仍不给模型工具：

```python
response = await provider.generate_structured(
    messages,
    ReproductionPlan,
    tools=(),
    timeout_seconds=timeout_seconds,
)
_reject_model_tool_calls(response)
response.output.validate_source_paths(allowed_source_paths)
response.output.validate_task_oracle(task)
```

计划通过后，[agent/graph.py](../../agent/graph.py)中的`generate_test_edit()`才按
`plan.files_to_read`读取源码。也就是说，模型先声明要看哪些文件，受信代码再检查路径并读取，
不是模型拿到仓库后自由浏览。

最多两轮由条件边直接限制：

```python
def route_after_reproduction(state: AgentState) -> str:
    if report.disposition in {
        ReproductionDisposition.REPRODUCED,
        ReproductionDisposition.SECURITY_REJECTED,
    }:
        return "finish"
    return "revise" if state.reproduction_round < 2 else "finish"
```

这段流程具有“执行、观察、修订”的ReAct特点，但不是预置ReAct Agent：模型不选择工具，
执行和条件边都由Graph控制。
