# 06 · 复现/修复结束后去哪:每种收场各自的路

> 上一章([05](05-repair-agent-deep-dive.md))讲了 AI 在隔离车间里复现、修复的全过程。
> 这一章回答一个问题:**AI 那一轮干完(或干不下去)之后,外层流水线把这次运行送到哪条路上去?**
>
> AI 的收场方式五花八门——复现成功、复现不出来、修好了、没修好、修到一半说"要人来看"、
> 钱花超了……每种收场都对应反馈的一个**终态**或**下一步去向**。这一章把这张"分流地图"画全。

## 1. 一句话:AI 结束 ≠ 反馈结束,中间还隔着"收场分流"

上一章我们看到 AI 在一个 ReAct 循环里反复试,最后会走到某个**收尾动作**:调用 `complete_repair`
宣布修好、调用 `report_blocked` 承认卡住、或者被预算耗尽打断。但 AI 一停,**外层流水线立刻接手**,
先看 AI 留下了什么状态,再决定这次运行往哪走。

这个"看状态 → 决定去向"的动作,在代码里是**几个写死的收场节点**:
`finish_reproduction`、`finish_agent_blocked`、`finish_repair_success`、`finish_repair_failure`、
`finish_budget_exhausted`。它们不猜、不调模型,只是**照着 AI 留下的受信状态引用**翻译成反馈状态
和运行状态。

> 记住一个关键点:AI 说过什么"我修好了"的话,外层**一个字都不信**。外层只看 AI 通过工具留下的
> **结构化存档引用**(reproduction_result_ref、repair_result_ref 这些)——模型文字在这里连被解析的
> 资格都没有。这正是全系统"本地代码拍板、模型只出建议"的又一次落地。

## 2. 先分清:反馈、运行,两个状态在同步

收场节点做的事,本质上是在**同步两个账本**:

| 账本 | 记什么 | 状态例子 |
|---|---|---|
| **反馈(feedback)** | 这条用户反馈现在是什么处境 | 复现中、修复中、终验中、已拒绝、待人工…… |
| **运行(run / agent_runs)** | 这次处理过程的状态 + 用量结账 | 复现中、修复中、终验中、预算耗尽…… |

每到一个收场节点,代码都会**先核对认领牌**(这条反馈确实还是我们这次运行在管),再把反馈状态
推进到对应位置,同时给这次运行**结一次账**(记模型调用次数、token、花费)。所以下一节的每张表,
你都会看到"反馈状态"和"运行状态"两栏——它们总是同步变。

## 3. 复现阶段的两种收场

AI 在复现阶段停下来,无非两种:复现成功、复现失败/卡住。

### 3.1 复现成功 → 去修复(finish_reproduction → repairing)

AI 写出了那条会失败的测试,且它真的在车间里失败了(上一章的 `REPRODUCED`)。收场节点做的事:

- 反馈状态:复现中(reproducing)→ **修复中(repairing)**;
- 运行状态:→ **修复中(repairing)**;
- 那条"会失败的测试"的存档,被正式记为这次复现的证据。

然后外层把接力棒交回内层——但注意:回到内层时,AI 的**阶段已经切到"修复"**(工具列表也跟着
收窄,`submit_fix_edits` 亮出来、`submit_test_edits` 收走)。复现到修复的切换不是 AI 自己申请的,
是外层节点看着存档里 `REPRODUCED` 的戳自动推进的。

> 特殊分支:如果这次运行**只做复现不做修复**(系统配置成 reproduction-only),复现成功就直接
> 收工(END),不会进修复。

### 3.2 复现失败 / AI 卡住 → 按原因分流(finish_reproduction 的另一半 / finish_agent_blocked)

复现阶段失败分成两大类。先看 AI **自己承认干不了**的路——它调用 `report_blocked`,只能填四种
**写死的错误码**之一(想填别的会被工具直接拒绝):

| report_blocked 的错误码 | 什么时候能用(守则强校验) | 收场节点 | 反馈去哪 |
|---|---|---|---|
| `cannot_reproduce`(复现不出来) | **必须**已试满 2 轮复现,否则拒绝 | finish_reproduction(记为 NOT_REPRODUCED) | 无法复现(cannot_reproduce) |
| `needs_human`(要人来看) | **必须**已至少试过 1 轮 | finish_agent_blocked | 转人工(needs_human) |
| `external_dependency_required`(要外部依赖) | **永远拒绝**——只有本地补丁守则才能证明"确实需要外部依赖",AI 没资格下这结论 | — | (工具直接报错) |
| `budget_exhausted`(预算没了) | **必须**受信计时器真的到顶,否则拒绝 | 走预算耗尽节点(第 4 节) | — |

再看**不是 AI 自己喊停**、而是被系统判定收场的路——收场节点读 AI 留下的复现结果存档,按结果盖戳:

| 复现结果 | 反馈去哪 | 意思 |
|---|---|---|
| `SECURITY_REJECTED`(容器被动过手脚) | 安全拒绝(security_rejected) | 有坏东西,直接关闸,谁也不碰 |
| `NOT_REPRODUCED`(试满还复现不出来) | 无法复现(cannot_reproduce) | 不是代码不肯承认,是证据不足,留给人工判断 |
| `BASELINE_REGRESSION` / `INVALID_TEST`(测试自身有问题) | 无法复现(cannot_reproduce) | 测试写得不对,同样留人工 |
| 其他 | 无法复现(cannot_reproduce) | 兜底 |

> 一句话:**复现阶段失败了,反馈不会自动消失,而是进"无法复现"或"转人工"这类终态**——让一个
> 真人决定"这条到底还修不修"。机器不会假装这是个 Bug,也不会把证据不足的反馈悄悄丢掉。

## 4. 修复阶段的收场:成功要进终验,失败看失败类型

### 4.1 修复成功 → 进独立终验(finish_repair_success → validating)

AI 在车间里把目标测试跑绿了,调用 `complete_repair`。收场节点做的事:

- 反馈状态:修复中(repairing)→ **终验中(validating)**;
- 运行状态:→ **终验中(validating)**。

注意**它不去发 PR**。修复成功只是"AI 自己说修好了",外层给它的下一步是那台**不信 AI 自夸的
独立终验机器**(下一章)。那里会用**全新的容器**把"测试补丁 + 修复补丁"重跑三遍,过了才算真的。

### 4.2 修复失败 → 按失败类型分流(finish_repair_failure)

修复阶段失败也分成几类,收场节点读修复结果存档(`repair_result_ref`)里的**受信结论**——系统只认
车间 JUnit 里的可信断言,不认模型说辞:

| 修复结果 | 反馈去哪 | 意思 |
|---|---|---|
| `NEEDS_HUMAN`(AI 有理由认为要人介入) | 转人工(needs_human) | 机器判断"这超出自动修复该碰的范围" |
| `SECURITY_REJECTED`(安全被拒) | 安全拒绝(security_rejected) | 有坏东西,关闸 |
| `TARGET_FAILED`(试满没修好) | 失败(failed) | 尽力了,目标测试没绿,这条以失败收场 |
| `INVALID_RESULT`(结果不合规) | 失败(failed) | 同上,兜底 |

### 4.3 修复中途预算耗尽 → 专门一条路(finish_budget_exhausted)

如果 AI 不是正常收尾,而是**撞破了预算上限**(模型调用 50 次 / 工具调用 30 次 / 车间 900 秒),
外层会走一个专门的收场节点 `finish_budget_exhausted`,把运行状态标成**预算耗尽
(BUDGET_EXHAUSTED)**、反馈转成需要处理的终态。它不是"AI 卡住"也不是"修复失败",而是**机器自己
喊停**:钱烧到上限了,不许再试。

## 5. 一张图串起所有收场

```text
repair_agent 节点收尾(内层循环结束)
   │
   ├─ 复现成功(REPRODUCED)──► finish_reproduction
   │        │   反馈: reproducing → repairing
   │        ▼
   │    回到内层修复阶段(round 1 开始)
   │        │
   │        ├─ 修复成功(目标测试绿)──► finish_repair_success
   │        │        │   反馈: repairing → validating
   │        │        ▼
   │        │    validate_final(独立终验,下一章)
   │        │
   │        └─ 修复失败
   │             ├ NEEDS_HUMAN      → finish_repair_failure → 转人工(needs_human)
   │             ├ SECURITY_REJECTED → finish_repair_failure → 安全拒绝
   │             └ 其他(TARGET_FAILED等) → finish_repair_failure → 失败(failed)
   │
   ├─ AI 承认复现不出来(试满 2 轮 cannot_reproduce)
   │        └─► finish_reproduction → 无法复现(cannot_reproduce)
   │
   ├─ AI 卡住喊人(report_blocked needs_human)
   │        └─► finish_agent_blocked → 转人工(needs_human)
   │
   ├─ 车间发现被动过手脚(SECURITY_REJECTED)
   │        └─► finish_reproduction / finish_repair_failure → 安全拒绝
   │
   └─ 撞破预算上限
            └─► finish_budget_exhausted → 预算耗尽(留给人工/第 9 章)
```

## 6. 这一章的"反馈终态"速查

收场之后,一条反馈可能停在下面任何一个状态(终态 = 不会再被自动处理,除少数会重新排队):

| 反馈终态 | 什么时候到 | 还会被自动处理吗 |
|---|---|---|
| repairing(修复中) | 复现成功,开始修 | 会——继续修复流程 |
| validating(终验中) | 修复成功,进独立终验 | 会——进下一章 |
| cannot_reproduce(无法复现) | 复现失败 / 复现不出来 | 否——留人工 |
| needs_human(转人工) | AI 求援 / 复现阶段卡住 | 否——留人工 |
| failed(失败) | 修复没修好 | 否 |
| security_rejected(安全拒绝) | 车间被动过手脚 | 否——谁也不碰 |
| budget_exhausted(预算耗尽) | 撞破预算上限 | 否——交给人工/运维判断 |

> 如果你发现某个状态没在这张表里,别急——反馈的一生很长:前面安检有 rejected、duplicate、
> quarantine,后面终验和发布还有 validated、stale_base、pr_opened。这一章只讲**复现/修复结束
> 这一个路口**。

## 7. 这一章出现的关键词速查

| 词 | 人话 | 对应代码/位置 |
|---|---|---|
| finish_reproduction | 复现结束收场:成功→repairing;失败→cannot_reproduce | `graph.py`(agent_runtime 分支) |
| finish_agent_blocked | AI 卡住收场:needs_human | `graph.py` |
| finish_repair_success | 修复成功收场:→validating | `graph.py` |
| finish_repair_failure | 修复失败收场:按类型→needs_human / security_rejected / failed | `graph.py` |
| finish_budget_exhausted | 撞破预算收场 | `graph.py`、第 9 章 |
| report_blocked 错误码 | AI 只能填 4 种写死原因(想乱填会被拒) | `tools.py` 的 `report_blocked` |
| 受信状态引用 | 外层只看 AI 工具留下的存档指针,不解析模型文字 | `*_result_ref` 字段 |

**复现/修复结束了,下一站是终验。** 如果 AI 修复成功、反馈进了 validating,外层就启动那台
**全新的、独立的验证机器**——下一章讲它怎么用三种容器交叉证明"AI 没自夸"。
