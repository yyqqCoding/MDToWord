# 阶段 D：自动复现问题/解决方案

## 问题 1：模型生成测试并不等于可信复现

测试可能直接通过、没有收集目标、发生 ImportError/SyntaxError、依赖缺失、超时，或者
通过修改基础设施制造失败。

### 解决方案

只把目标 testcase 的 `AssertionError` 或计划允许的 `ConversionError` 视为复现。测试
只能新增到固定回归路径，禁止 pytest plugin/hook、Shell、网络和直接伪造 DOCX 结构；
每轮从相同原始快照创建全新 Sandbox workspace，最多两轮。

## 问题 2：DOCX “打开不正确”不能靠文本返回值判断

后端可能返回 200 且前端预览正确，但 Word 内部缺少表格、公式或 drawing 节点。只检查
文件存在会把错误导出当成成功。

### 解决方案

在只读 Sandbox 镜像中提供受信 DOCX Oracle，检查 ZIP、必需 XML 部件、表格、公式、
drawing、样式和三线表边框。模型只能选择已登记 validator 和参数，不能自行解析或修改
Oracle。

## 问题 3：Mermaid 测试编辑经常不符合本地编辑规则

模型能理解“流程图没有渲染”，但第一轮常生成不存在目标的 `search_replace`、不合法
Python 或错误 fixture，第二轮继续调用模型既昂贵又不稳定。

### 解决方案

仅当计划明确为 Mermaid drawing 且第一轮是 `invalid_test_edit` 时，第二轮由 Controller
生成固定受信测试与 fixture。模板只调用 `assert_minimum_drawing_count`，仍经过同一 Patch
Policy 和真实 Docker Sandbox；普通问题仍使用模型修订。

## 问题 4：JUnit 报告缺少 `failure type` 导致错误分类

真实 pytest JUnit 只在 `message` 开头写 `AssertionError`。旧逻辑又扫描完整 traceback，
变量名 `FIXTURES` 被误命中为 fixture 基础设施错误，最终把真实缺陷判成
`cannot_reproduce`。

### 解决方案

从结构化 JUnit `message` 开头推断异常类型；基础设施判定只依据异常类型和确定性字段，
不再扫描测试源码 traceback 的普通单词。为真实报告形态增加回归测试。

## 问题 5：模型只读取很短源码片段后猜测接口

计划若只读文件首行或局部片段，生成的测试容易引用不存在的函数和调用签名。

### 解决方案

计划只能选择固定快照中实际存在的白名单路径，每个读取范围至少覆盖 20 行；源码工具按
实际行号返回，Policy 拒绝猜测路径。失败只把稳定原因交给下一轮，不回显用户原文或完整
源码到日志。

## 问题 6：模型网关在线但长请求仍超时或断开

多个兼容接口能通过 Gate，却在 35～40 KB 的测试生成请求中返回 503、连接断开或
`invalid_response`。

### 解决方案

阶段 D 使用独立的 180 秒默认模型超时，可在 30～300 秒间配置；传输错误最多重试两次，
退避固定为 1 秒和 4 秒。更换 API 后必须验证代表性严格 Schema 请求，不能只看
`/models` 的 200。

## 问题 7：终端停留在已被 Docker 删除的 bind mount

关闭或重启容器后，WSL 终端当前目录可能仍是
`/mnt/wsl/docker-desktop-bind-mounts/...`。Python 随后报 `failed to make path absolute`
或 `Fatal Python error: error evaluating path`。

### 解决方案

先回到真实仓库目录 `/mnt/e/PythonProject/MDToWord`，确认 `pwd` 和虚拟环境路径，再启动
Worker 或 CLI。不要在已经失效的 Docker bind mount 目录中继续运行 Python。

## 问题 8：失败后重新按 feedback ID 执行得到 `feedback is not claimable`

反馈已经被某个 run 领取或进入终态时，再创建新 run 会违反原子 claim 语义。

### 解决方案

可恢复错误使用原来的 `--resume-run-id` 从 PostgreSQL checkpoint 继续，不重新领取反馈；
历史终态保持不变。需要重新验证修正后行为时，新建一条可丢弃的 `pending` 反馈。

## 问题 9：Mermaid 测试生成在严格 Schema 阶段直接失败

生产模型可以正确完成 Gate 和复现计划，但 `generate-test` 的复杂返回连续两次不符合
严格 Schema，运行以 `InvalidModelResponseError/invalid_response` 终结。原有 Mermaid
回退只处理已经通过 Schema、随后被本地编辑规则拒绝的结果，因此无法接管这种失败。

### 解决方案

模型格式修正耗尽后，仅当原文包含 Mermaid、计划要求 `AssertionError`，并且 Oracle 是
受信的 `assert_minimum_drawing_count` 时，Controller 使用已有固定测试与 fixture。模板仍
经过 Patch Policy 和 Docker Sandbox；普通反馈继续保持严格失败，不放宽 Schema，也不
增加模型重试或外部依赖。

2026-08-13 生产复测确认该路径生效：已修复的裸 `graph TD` 反馈不再因
`generate-test/invalid_response` 提前终结，受信模板进入 Docker 后无法复现旧缺陷，最终
状态为 `cannot_reproduce`，没有进入修复或发布。

## 问题 10：`invalid_response` 在任何地方都查不到失败原因

生产 run 以 `InvalidModelResponseError/invalid_response` 终结，但三处刻意的脱敏边界
叠加后，"哪个字段不合规"这一信息在系统里不存在：

- `openai_compatible.py` 用 `raise ... from None` 切断 `ValidationError` 异常链；
- `controller.py` 只把 `type(error).__name__` 写进数据库 `error_message`；
- `cli.py` 捕获 `AgentError` 后只输出 `{"error": error_code}`，且仓库没有
  `logging.basicConfig`，`_LOGGER.info` 不会出现在 journalctl。

结果是数据库、Langfuse、展示站和系统日志都只有 `invalid_response` 一个词。

### 解决方案

Provider 层新增 `_schema_error_paths`，只输出「字段路径:Pydantic 规则名」。该摘要挂到
`InvalidModelResponseError` 上，由 `ObservedModelProvider` 转发进 Langfuse Generation 的
失败输出，同时以 WARNING 写入进程日志；两次格式尝试都记，便于判断修正提示是否生效。

日志额外带 `detail=`，即回传给模型的那份含校验器文案的摘要，只进本机日志。Langfuse 与
公开展示站仍只收字段路径 —— `extra=forbid` 下路径可能是模型编造的字段名，逐段截断。

由此可以区分 `invalid_response` 的两层来源：Generation 被标 ERROR 说明失败在 Provider
严格 Schema 层；Generation 全部成功而 run 失败，说明是 `reproduction.py` 的本地 Policy。

## 问题 11：校验器把多个失败条件合并成一条消息，重试越改越偏

`Edit.validate_mode_fields` 原本用一条 `full_file requires content only` 覆盖三种互不
相同的违规。该消息会**原样回传给模型**作为格式修正提示，模型看到它会认为自己已经给了
content，无从知道真正的问题是未使用字段填了 `""` 而不是 `null`。

生产日志显示第一轮只有 `edits.1` 一处失败，第二轮恶化为 `edits.0`、`edits.1` 全错并
触发 `edits:too_short`。维护者侧同样只能看到 `edits.N:value_error`。

### 解决方案

拆开每个失败条件，各自给出点名字段的消息，例如 `full_file requires search to be null`、
`search_replace requires a non-empty search`。128 种字段组合已逐一比对，接受/拒绝集合与
原实现完全一致，只有消息不同。`validate_fix_paths` 的拒绝消息同样改为直接列出白名单，
并由 `allowed` 集合拼出以免与校验逻辑漂移。

## 问题 12：严格 Schema 无法表达的规则没有写进提示词

严格 Structured Outputs 要求每个属性都进入 `required`，可空性只能用 `null` 表达，跨字段
约束根本无法编码进 JSON Schema。而 `generate_test.md` 从未说明何时用 `search_replace`、
何时用 `full_file`，也从未给出 `files_needed_for_fix` 的白名单。唯一讲模式选择的那句话在
`reproduction.py` 的本地 Policy 重试提示里，格式层失败永远走不到。

模型因此只能猜：给新建固件选了 `search_replace` 却没有原文可搜；PlantUML 反馈的自然答案
`backend/app/mermaid_renderer.py` 又恰好是可读不可写的受信模块。

### 解决方案

白名单不放宽 —— `mermaid_renderer.py` 驱动 Mermaid CLI 与 Chromium 子进程，写入权限是
刻意不给的，见 [security-and-sandbox.md](../AgentRequirements/security-and-sandbox.md)。
改为把规则明确写进 `generate_test.md`：新建文件必须 `full_file`；`search_replace` 的
`search` 必须非空且恰好命中一次；`files_needed_for_fix` 只接受 Policy `write.fix_exact`
中的两个路径，不确定时填空数组。提示词内容变化时同步 bump
`TEST_GENERATION_PROMPT_VERSION`。

凡是只能由 Pydantic 校验器表达的规则，都必须在对应提示词里复述一遍：模型看不到 Policy
文件，也无法从 Schema 推断。

## 问题 13：已有回归文件没有追加锚点，模型按提示重写后被安全拒绝

2026-08-16 的真实 feedback `5180ba17-...` 已通过 `gate-v7`，复现计划也正确选择
`unexpected_conversion_error`。但 run `1ebfb33c-...` 最终显示
`test_edit_security_rejected`；Trace 中两次测试生成都把 Markdown fixture 与
`backend/tests/test_feedback_regressions.py` 作为 `full_file` 编辑提交。后者没有完整保留
快照中的既有测试，Patch Policy 因而以 `test edits must preserve existing regressions`
拒绝。该拒绝发生在 Controller 的 `submit-test-edits`，Sandbox Job 实际没有启动；界面上的
“沙箱复现：安全拒绝”只是阶段汇总，不能据此判断为 Docker 内复现失败。

### 根因

这是提示词与运行时上下文共同造成的契约缺口。`test-generation-v3` 告诉模型可以用
`full_file` 提交完整回归文件，本地修正提示还把“不在 source_files”错误等同于“文件不
存在”；但 Graph 只把计划选择的应用源码放进 `source_files`，没有向模型提供已有回归文件
内容。模型既无法完整保留它，又没有可构造尾部 `search_replace` 的精确文本，因此合法的
结构化响应仍会在更下游触发安全拒绝。

### 解决方案

安全规则不放宽。Controller 从固定源码快照计算最短且唯一的文件尾部，把它作为
`regression_append_context.append_anchor` 交给测试生成器。文件非空时，
`test-generation-v4` 只允许一个 `search_replace`：`search` 必须精确复制锚点，`replace`
必须先原样保留锚点再追加 import 和目标测试；只有空文件才能使用 `full_file`。同一规则
同时进入本地跨字段校验，违规输出在调用 Patch Builder 前获得一次有界格式修正，不再把
可纠正的编辑模式误报为安全事件。Patch Builder 仍独立验证既有内容前缀、AST 能力、唯一
selector、路径和补丁规模。

## 问题 14：转换崩溃的模型测试两轮都没有生成 JUnit

部署 `test-generation-v4` 后，真实 feedback `064f8e30-...` 的 run `a00cc2f7-...` 已正常
通过 Gate、计划和追加式 Patch Policy。两轮测试都成功生成 fixture 与目标测试补丁，证明
问题 13 已修复；但两个 Sandbox Job 均在约 2 秒内返回
`status=completed/exit_code=1/junit=null`，Controller 因而判为 `invalid_test/missing_junit`。
界面中的“第 2 轮成功”表示测试生成与补丁提交成功，不表示 pytest 已产出可判定结果。

### 根因

`unexpected_conversion_error` 的回归测试结构其实是确定的：读取原始 Markdown，调用
`convert_markdown_to_docx`；缺陷存在时目标测试抛出 `ConversionError`，修复后转换成功并
通过登记 Oracle。旧 Graph 却让模型在第二轮继续自由重写完整测试。首轮没有 JUnit 时，
`previous_report` 只能提供固定 `missing_junit`，既不能安全回传可能含用户公式的 stderr，
也没有足够信息让模型修正 pytest 启动/收集结构，因此第二轮容易重复同一无效模式。

### 解决方案

`agent-graph-v8` 为 `expected_failure_kind=unexpected_conversion_error` 增加受信回退。首轮
`invalid_test`（含 `missing_junit`）后，第二轮由 Controller 固定生成 `.md` fixture、
`convert_markdown_to_docx` 调用，以及计划已经登记的受信 Oracle 调用；不再请求模型生成
测试。模型格式修正耗尽时也可使用同一模板。模板只从计划白名单镜像
`files_needed_for_fix`，仍经过既有路径、AST、唯一 selector、补丁规模 Policy 和全新
Sandbox；没有放宽测试或修复权限。Graph 回归固定“首轮无 JUnit、第二轮目标
ConversionError”路径，真实 Docker 回归则要求固定模板必须产生目标 JUnit，不能用 Fake
结果代替。
