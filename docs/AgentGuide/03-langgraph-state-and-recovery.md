# LangGraph State、节点与恢复

## 1. 为什么使用LangGraph

一次修复包含分类、读取源码、模型生成、Docker执行、重试和发布，可能持续几分钟。普通
函数调用一旦进程退出，只能从头开始。LangGraph把流程写成明确节点，并在节点结束后把
State保存到PostgreSQL，因此可以从上次完成的位置继续。

LangGraph负责“下一步运行哪个节点”和“保存当前State”。安全规则、补丁检查、测试判定和
数据库状态转换仍由普通Python代码负责。

## 2. State有哪些字段

当前`AgentState`包含以下字段。

### 身份和运行方式

| 字段 | 含义 |
|---|---|
| `schema_version` | State结构版本，当前为2；仍接受v1 checkpoint恢复 |
| `run_id` | 本次运行ID，也是LangGraph `thread_id` |
| `feedback_id` | 对应的反馈ID |
| `claim_token` | 更新反馈状态时证明当前运行仍拥有任务 |
| `trace_id` | 本次运行在Langfuse使用的ID |
| `dry_run` | 是否禁止创建真实PR |

### 当前进度和路由

| 字段 | 含义 |
|---|---|
| `status` | 当前运行状态 |
| `route` | 分类后的处理方向 |
| `area` | backend、extension、cross_component等处理范围 |
| `category` | 问题类别 |
| `risk` | 风险等级 |
| `base_sha` | 本次修复固定使用的Git提交 |
| `extension_version` | 运行时读取到的插件版本，没有则为`unknown` |

### 本地运行文件地址

| 字段 | 指向的内容 |
|---|---|
| `task_artifact_ref` | 已去除联系方式的任务内容 |
| `source_snapshot_ref` | 固定版本源码压缩包或目录 |
| `gate_result_ref` | 分类结果 |
| `reproduction_plan_ref` | 复现计划 |
| `test_patch_ref` | 测试补丁 |
| `reproduction_result_ref` | 复现结果 |
| `fix_patch_ref` | 修复补丁 |
| `repair_result_ref` | 修复阶段结果 |
| `validation_result_ref` | 最终验证结果 |
| `publication_result_ref` | GitHub发布结果 |
| `issue_publication_result_ref` | Issue发布结果；与PR发布引用分离 |

这些字段只保存文件地址，不保存完整补丁和长日志。地址形式类似：

```text
artifact://<run_id>/test.patch
```

### 循环和预算

| 字段 | 含义 |
|---|---|
| `reproduction_round` | 当前复现轮次 |
| `repair_round` | 当前修复轮次 |
| `model_calls` | 已完成的模型调用次数 |
| `tool_calls` | 已完成的受控工具调用次数 |
| `sandbox_duration_ms` | Docker执行累计时间 |
| `usage` | 输入、输出、总Token和估算成本 |

### 最终结果和错误

| 字段 | 含义 |
|---|---|
| `fix_summary` | 修复摘要 |
| `fix_source_paths` | 修复涉及的业务文件 |
| `validated_patch_sha256` | 最终验证通过补丁的哈希 |
| `pr_url` | 创建成功的PR地址 |
| `issue_url` | 创建或复用成功的Issue地址 |
| `last_error_code` | 稳定错误码 |
| `last_error_message` | 受控错误摘要 |

State不保存模型密钥、数据库密钥、GitHub令牌、联系方式、完整用户Markdown、完整源码或
完整pytest日志。

## 3. 当前LangGraph节点

```text
START
  ↓
start_gate
  ↓
classify_gate
  ↓
route_feedback
  ├─ 无关/注入/信息不足/重复 → END
  ├─ 功能需求或前端Bug → publish_issue
  │                          ↓
  │                        finish_issue_publication → END
  └─ 后端Bug
       ↓
     prepare_source
       ↓
     plan_reproduction
       ↓
     generate_test_edit
       ↓
     run_reproduction_in_sandbox
       ↓
     classify_reproduction
       ├─ 修改测试后再试，最多2轮
       ├─ 无法复现或安全拒绝 → finish_reproduction → END
       └─ 复现成功 → finish_reproduction
                         ↓
                       generate_fix_edit
                         ↓
                       run_target_validation
                         ↓
                       classify_target
                         ├─ 修改修复后再试，最多2轮
                         ├─ 失败 → finish_repair_failure → END
                         └─ 目标通过 → finish_repair_success
                                           ↓
                                         validate_final
                                           ↓
                                         finish_validation
                                           ├─ 失败 → END
                                           └─ 通过 → publish_pull_request
                                                        ↓
                                                      finish_publication
                                                        ↓
                                                       END
```

节点名是代码名称。比如`prepare_source`实际做的是从GitHub读取`main`的SHA、下载该版本
源码、校验快照并保存引用。

## 4. 数据库状态与State的区别

| 数据 | 用途 |
|---|---|
| LangGraph State | 保存下一节点执行所需数据，支持继续运行 |
| `agent_runs` | 保存当前阶段、用量、错误和最终结果，供排障和网站读取 |
| `feedback.status` | 表示用户反馈当前处于领取、复现、修复、发布或终态 |

State快照不是对外展示页面，`agent_runs`也不能替代checkpoint。节点通常先完成本步操作，
再更新数据库摘要和State；恢复时会同时检查两者，避免重复副作用。

## 5. 进程重启后怎样恢复

Scheduler启动后先调用`find_resumable()`。发现未完成运行后：

1. 使用原来的`run_id`读取`agent_runs`；
2. 用`run_id`作为LangGraph `thread_id`读取最近State快照；
3. 校验反馈仍然属于原`claim_token`；
4. 从checkpoint记录的下一个节点继续；
5. 复用原来的`base_sha`和补丁文件，不重新领取反馈。

如果数据库中已有运行，但没有任何checkpoint，Agent会使用`agent_runs`中的基本字段重新
构造初始State。

## 6. 恢复时如何避免重复操作

- 模型、工具和沙箱计数取数据库与checkpoint中的较大值，避免少记预算；
- Sandbox Job使用固定`job_id`，重复提交返回第一次结果；
- 发布失败恢复时只重新进入对应PR或Issue发布节点，不重新调用模型和Docker；
- GitHub发布使用确定性分支和提交，并检查同一反馈和补丁是否已有PR；
- Issue发布使用run reference与内容指纹marker，并同时检查开放和关闭Issue；
- 所有源码和补丁继续绑定原来的`base_sha`与SHA-256。

## 7. 哪些失败可以继续，哪些必须结束

可以有限重试或恢复：

- 模型限流、超时和短暂网络故障；
- 模型输出JSON格式错误的一次修正；
- 最多两轮测试生成；
- 最多两轮修复生成；
- Worker请求重复或Agent主进程重启；
- GitHub发布的幂等重试；
- 维护者提高调用预算后，显式恢复同一 `budget_exhausted` run；
- `main`改变后最多重新排队一次。

必须结束或转人工：

- 疑似提示注入；
- 补丁越过文件白名单；
- 需要新增依赖或修改部署；
- 预算耗尽（Scheduler 必须停止；只有维护者提高预算并显式指定 run ID 才能重开）；
- 两轮仍无法复现或修复；
- 第二次遇到`stale_base`。

对应实现：

- [agent/state.py](../../agent/state.py)
- [agent/graph.py](../../agent/graph.py)
- [agent/controller.py](../../agent/controller.py)

## 8. 结合源码看State和Checkpoint

[agent/state.py](../../agent/state.py)中的`AgentState`只保存小字段和文件引用：

```python
class AgentState(BaseModel):
    run_id: UUID
    feedback_id: UUID
    claim_token: UUID
    status: AgentRunStatus
    base_sha: str | None = None
    task_artifact_ref: str | None = None
    source_snapshot_ref: str | None = None
    test_patch_ref: str | None = None
    fix_patch_ref: str | None = None
    reproduction_round: int = Field(default=0, ge=0)
    repair_round: int = Field(default=0, ge=0)
```

可以看到用户Markdown、源码压缩包和补丁正文没有直接放进State，而是保存`*_ref`。

Graph编译时注入checkpointer：[agent/graph.py](../../agent/graph.py)

```python
return builder.compile(
    checkpointer=checkpointer,
    interrupt_after=list(interrupt_after) if interrupt_after else None,
    name="feedback-agent",
)
```

恢复入口在[agent/controller.py](../../agent/controller.py)的`resume()`。它先读取原
`agent_runs`，再使用同一个`run_id`继续Graph；发布失败还会只重开发布状态，不重新运行模型
和Sandbox。
