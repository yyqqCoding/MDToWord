# Agent开发88个真实问题

这88个问题既可用于面试，也可用于检查当前系统。回答只描述本项目已经采用的做法和明确
边界，不把未来设想写成现有能力。完整流程先看[系统总览](00-end-to-end.md)。

## 一、LangGraph与状态

### 1. 为什么使用LangGraph，而不是一个普通while循环？

普通循环适合“调用模型直到得到答案”，但本项目有分类分支、两轮复现、两轮修复、Docker
验证、PR发布和进程恢复。LangGraph把每一步写成节点，把下一步写成条件边，并在节点结束后
把State保存到PostgreSQL。服务器重启后可以沿用原`run_id`继续，而不是从第一步重新执行。

Scheduler外层仍然有while循环，但它只负责“查询任务并启动或恢复一次Graph”，不负责表达
修复内部流程。

### 2. State中应该保存什么，不应该保存什么？

State只保存后续节点确实需要的小数据：运行ID、反馈ID、`claim_token`、状态、路由、
`base_sha`、各阶段文件地址、轮次、模型/工具/Token/Sandbox用量、最终补丁哈希和PR地址。

State不保存数据库密钥、模型Key、GitHub令牌、联系方式、完整Markdown、完整源码、完整补丁
和完整pytest日志。密钥由对应适配器读取，大内容保存在私有运行目录。

### 3. 为什么大文件只保存引用？

补丁、源码压缩包和JUnit可能很大。如果每个checkpoint都复制一份，会增加数据库大小、
序列化时间和恢复成本，也更容易把用户内容带入Trace。系统把文件写到
`<artifact_root>/<run_id>/`，State只保存类似`artifact://<run_id>/test.patch`的地址。

### 4. State、数据库业务状态、Checkpoint有什么区别？

- State：当前Graph继续执行所需的数据对象；
- Checkpoint：某个节点结束后持久化的State快照；
- `feedback.status`：这条用户反馈处于领取、复现、修复还是终态；
- `agent_runs`：一次运行的阶段、用量、错误和最终摘要。

Checkpoint负责恢复，业务表负责所有权、运营和展示，二者不能互相代替。

### 5. Graph的节点应该拆多细？

一个节点应完成一个可以单独说明输入、输出和失败方式的动作。例如“生成测试”和“运行测试”
必须分开，因为前者调用模型，后者调用Docker，重试和权限完全不同。

也不需要把每次字段赋值拆成节点。当前按分类、准备源码、制定计划、生成编辑、Sandbox执行、
结果分类、最终验证和发布拆分，粒度足够支持恢复和排障。

### 6. 条件边由模型决定还是由代码决定？

由代码决定。模型返回分类、计划或Edit，Pydantic和本地Policy先检查，再由普通Python函数
根据结果返回下一条边。模型不能输出“跳到发布节点”，也不能直接修改`status`。

### 7. 服务重启后从哪里恢复？

Scheduler先查`agent_runs`中的可恢复运行，再用原`run_id`作为LangGraph `thread_id`读取
PostgreSQL checkpoint。恢复时继续使用原`claim_token`、`base_sha`和本地文件地址，不重新
领取反馈。

### 8. 恢复时如何避免前面节点重复执行？

LangGraph从checkpoint记录的下一节点继续。Sandbox使用固定`job_id`返回已保存结果，GitHub
发布使用确定性分支和PR标记，数据库更新要求原`claim_token`。发布失败时只重新打开发布
节点，不重跑模型和Docker。

### 9. 修改Graph后，旧Checkpoint是否还能恢复？

不能默认保证。当前State带`schema_version=1`，新增有默认值的字段通常容易兼容；删除字段、
改名、改变节点或条件边可能让旧checkpoint无法解析或没有后继节点。

项目曾为旧阶段D运行增加一个明确兼容路径：旧Graph停在`finish_reproduction`后，升级时用
`aupdate_state(..., as_node="finish_reproduction")`追加checkpoint，再进入修复。以后发生不兼容
变化，也必须写同类迁移或先结束旧活动run，不能假设LangGraph自动兼容。

### 10. 如何避免循环永远不结束？

复现和修复各最多两轮，格式修正最多一次；整次运行还有最多8次模型、30次工具、配置的
Token上限和默认900秒Sandbox累计时间。每个调用前检查预算，达到上限进入
`budget_exhausted`，不会再执行“最后一次”。

### 11. 为什么复现和修复都只允许两轮？

第一轮用于正常生成，第二轮只根据明确失败原因修正。两轮后仍失败，继续调用通常是在扩大
成本而不是增加确定性，还可能让模型不断偏离原问题。当前反馈量和问题范围下，两轮能保留
一次纠错机会，同时使耗时、Token和Docker成本有明确上限。

### 12. 一个节点执行到一半进程崩溃怎么办？

LangGraph通常只能从上一个已完成checkpoint重新执行该节点，因此节点可能是“至少执行
一次”，不是严格只执行一次。数据库、Sandbox和GitHub等关键副作用必须使用token、job ID或
确定性标记实现幂等。

模型调用本身可能在响应后、checkpoint前崩溃，恢复时再次调用并产生额外费用，这是当前仍然
存在的边界。系统通过调用预算、Trace和数据库/State计数取较大值控制影响，但不声称模型
调用可以做到严格一次。

## 二、模型调用

### 13. 模型API超时、断开、429、5xx怎么处理？

Provider统一转换成`timeout`、`rate_limit`或`provider_unavailable`，包含首次最多三次
attempt，本地等待1秒和2秒。429带数值`Retry-After`时会在10秒上限内尊重更长等待。启用
备用接口时前两次使用主接口、第三次使用备用接口，但仍统一记为`openai_compatible`。
重试耗尽后把稳定错误码和失败位置写入运行，不无限等待。

### 14. 模型认证失败为什么不重试？

401和403通常表示Key、权限或配置错误，几秒后不会自动恢复。系统返回`auth_error`并结束
当前运行，等待维护者修复配置。重复请求只会增加无意义流量，还可能触发安全告警。

### 15. `provider_unavailable`与`invalid_response`有什么区别？

`provider_unavailable`表示连接、传输或上游5xx在有限重试后仍没有可用响应。
`invalid_response`表示已经收到响应，但响应形状、严格Schema或本地跨字段Policy不接受。

排障时前者看网络和上游服务，后者看Langfuse generation状态及本机
`structured output rejected`字段路径。

### 16. 模型返回的JSON格式不合法怎么办？

Pydantic解析失败后，Provider只提取字段路径、错误类型和安全校验文案，不回传整段无效
内容。它追加一条“只重新输出符合Schema的JSON”的修正消息，最多再请求一次。第二次仍错就
返回`invalid_response`。

### 17. JSON符合Schema，但业务内容不合理怎么办？

Schema只能检查形状。本地Policy继续检查字段关系、路径白名单、Edit模式、唯一匹配、修改
规模和危险能力。之后还必须经过真实pytest。结构合法只代表“可以继续检查”，不代表分类、
测试或修复已经正确。

### 18. 哪些模型错误可以重试，哪些不能？

限流、超时、连接失败和上游5xx可以有限重试。认证失败、上下文过大、普通4xx和安全拒绝不
发送同样请求。结构错误只有一次格式修正，业务编辑错误最多进入当前阶段的第二轮。

### 19. 格式重试和复现/修复轮次有什么区别？

格式重试处理“JSON无法解析或字段类型错误”，仍属于同一次业务尝试。复现或修复第二轮处理
“JSON合法，但测试没有复现问题或补丁没有修好”。两类计数分开，但都会累计模型调用和Token。

### 20. 模型上下文过大怎么办？

系统限制反馈Markdown、单文件读取、工具输出和一次源码上下文大小，只发送当前节点需要的
内容。如果Provider仍返回`context_too_large`，不会原样重试。需要通过减少读取文件或调整
确定性上下文构造修复，而不是增加重试次数。

### 21. 模型安全拒绝怎么办？

Provider将其转换为`safety_refusal`。当前不会通过改写安全边界或换成更高权限提示继续尝试，
而是记录错误并结束自动路线。用户内容仍保留在私有反馈数据中供维护者判断。

### 22. Token和成本怎样统计？

Provider规范化输入、输出、缓存、推理和总Token；一次格式重试的两次响应会累计。State和
`agent_runs`保存总量，Langfuse保存到具体generation。没有配置模型单价时成本保持0，但
真实Token仍记录。

### 23. 为什么记录Prompt版本？

相同模型和反馈在不同提示词下可能得到不同结果。当前四个提示词都有版本，写入
`agent_runs.prompt_versions`和Langfuse。看到历史失败时可以确认当时使用的规则，避免拿
新提示词解释旧运行。

### 24. 为什么当前只使用一个模型？

当前问题范围小，分类、计划、测试和修复可以通过同一个结构化Provider完成。先保持一种
模型可以减少路由、计费和故障组合。只有真实记录证明某个阶段需要不同能力或成本策略时，
才值得拆模型；业务Policy不能根据模型名称分支。

## 三、提示词

### 25. 系统提示词应该怎样组织？

先写唯一任务，再写哪些输入不可信，然后列允许范围、禁止行为、输出字段关系和少量边界
示例。避免先写长篇角色背景。当前四个提示词分别只负责分类、复现计划、测试Edit和修复Edit。

### 26. 为什么要明确“用户内容是不可信数据”？

用户Markdown可能正常包含“system”“工具”或命令文本，也可能故意要求模型忽略规则。如果
不把它放在明确数据边界中，模型容易把待转换内容当作操作指令。源码、工具输出和测试日志
也使用同样规则，因为它们同样可能包含误导文字。

### 27. 提示词如何区分普通技术文字和Prompt Injection？

不能看到“system prompt”几个字就一律隔离。`gate-v10`要求只有内容试图改变当前分类任务、
索要内部信息、要求越权操作或把数据伪装成给模型的指令时，才设置
`injection_suspected=true`。普通代码和技术讨论仍按产品内容分类。

### 28. 严格JSON Schema能代替提示词规则吗？

不能。Schema适合字段类型、枚举和长度，无法完整表达“现有文件非空时只能用
search_replace”“路径必须来自本次允许列表”等跨字段和外部状态规则。这些规则同时写进
提示词和Python校验器，最终以代码判断为准。

### 29. 校验错误应该怎样回给模型？

要指出具体字段和可执行修正，例如“`edits[0].mode`必须是`search_replace`”，不能只说
“格式不对”。每个失败条件使用独立消息。回传内容不包含模型原文、用户原文或密钥，只包含
有限字段路径和规则。

### 30. 如何避免提示词越来越长？

每个节点只保留自己的职责和高频错误规则，公共安全边界用短句重复，不把整个系统说明塞进
模型上下文。已经由代码固定的Docker参数、数据库流程和GitHub权限不需要详细告诉生成模型。

### 31. 每一轮应该给模型哪些上下文？

只给脱敏任务、当前计划、允许源码、目标失败摘要、上一轮明确错误和剩余预算。修复第二轮
仍从原`base_sha`生成完整Edit，不累积全部对话、完整日志和上一轮临时工作区。

### 32. 如何防止模型编造文件、函数和依赖？

提示词要求路径逐字选自`allowed_source_paths`，Edit的`search`逐字复制已提供源码。模型不能
修改依赖。Python端还会拒绝未知路径、零次或多次匹配及不存在的文件；提示词只是减少错误，
Policy才负责阻止执行。

### 33. 如何避免修复只对当前样例生效？

`fix-generation-v4`明确禁止针对测试函数名、反馈ID或完整用户输入增加硬编码分支，要求解决
复现测试证明的通用原因。最终还要运行已有全量测试，减少为单一样例破坏兼容行为的风险。

### 34. 修改提示词是否必须建设大型评测平台？

当前不需要。项目已有真实失败、严格Schema、本地Policy和版本记录。小范围文字完善只需
同步版本并检查真实失败原因是否被写清；代码、安全边界或模型发生明显变化时再使用已有离线
用例和真实手工验收，不为每次措辞调整新增复杂平台。

## 四、工具选择与调用

### 35. Agent面对多个工具时如何选择？

先由LangGraph确定阶段，再只给该阶段最小能力：Gate无工具，源码阶段只能搜索和读取，测试
阶段只能提交测试Edit，复现阶段只能运行复现Job，修复阶段只能提交修复Edit，验证阶段只能
运行目标验证。模型不在全部工具中自由挑选。

### 36. 当前模型会直接发起Tool Call吗？

不会。当前OpenAI兼容Provider要求`tools=()`，如果响应仍带tool call就判为非法。模型返回
结构化`files_to_read`或`Edit[]`，Graph验证后由Python调用本地函数。这比让模型直接操作
工具更容易控制和恢复。

### 37. 工具权限怎样与节点绑定？

`ToolNode`到`ToolName`有固定授权表；Graph本身也只在对应节点装配具体依赖。工具不能靠
参数改变节点，数据库和GitHub等副作用能力根本不作为模型工具开放。

### 38. 模型请求不存在或无权使用的工具怎么办？

不存在的名称返回“tool is not registered”，存在但不属于当前节点返回“tool is not
authorized for this node”。系统不猜测近似名称，也不自动换成权限更大的工具。

### 39. 工具参数不合法怎么办？

先过Pydantic Schema，再检查路径规范化、行号、输出大小、当前State、预算和业务Policy。
绝对路径、`..`、未知枚举、超长查询或非法Edit在进入真实文件或Docker适配器前被拒绝。

### 40. 工具输出中包含提示注入怎么办？

源码、搜索结果和测试日志重新放进明确的“不可信数据”边界，只作为分析材料。输出还会限制
大小、清理控制字符和疑似密钥。工具结果不能直接改变State或触发另一个高权限工具。

### 41. 源码读取工具如何限制范围？

它只读取固定`base_sha`快照和白名单文件，拒绝绝对路径、仓库外符号链接、`.git`、`.env`和
本机路径。单文件最多80 KB，一次输出最多20 KB，搜索使用字面量匹配而不是正则或Shell。

### 42. 为什么不给模型Shell工具？

Shell把命令、路径、环境变量和子进程能力同时开放，很难建立精确权限。当前需求只有读取、
结构化编辑和固定测试，因此使用专用函数和固定Docker argv即可，没有必要承担任意Shell的
风险。

### 43. 如何限制工具调用成本？

State累计`tool_calls`，默认整次运行最多30次；源码文件数、行范围和输出字节也有限制。
调用前检查预算，达到上限直接进入`budget_exhausted`，不会让模型继续搜索整个仓库。

### 44. 工具调用失败后是重试、换工具还是结束？

先看错误类型。非法路径、越权和确定性参数错误不重试；模型Edit可根据明确错误进入第二轮；
Sandbox传输故障使用同一Job ID包含首次最多三次attempt；没有同权限等价工具时不会自动
换工具。安全
失败不会通过降级到Shell解决。

### 45. 有副作用的工具如何保证幂等？

数据库更新匹配`claim_token`，Sandbox使用`job_id`和请求指纹，GitHub使用确定性分支、提交、
PR标记和补丁哈希，网站Trace使用`run_id` upsert。模型调用只产生内容，不直接承担这些
副作用。

## 五、复现、修复与验证

### 46. 为什么必须先复现问题，再生成修复？

如果没有测试证明原代码失败，就无法判断用户描述是否仍适用于当前`main`，也无法区分真实
修复和碰巧修改。系统先得到`原代码 + test.patch = 目标失败`，才允许调用修复模型。

### 47. 什么才算复现成功？

目标测试必须被pytest真实收集，并以计划指定的断言失败或预期转换异常结束；JUnit必须
有效，容器不能超时，工作区不能越权变化。普通stdout出现“failed”不算证据。

### 48. 为什么ImportError、SyntaxError和fixture缺失不算复现？

这些错误只能证明测试写坏了，不能证明用户报告的Markdown转换问题存在。如果把它们当作
复现，模型可能修复测试环境而不是产品缺陷。系统从JUnit区分目标断言和测试自身错误。

### 49. 两轮仍无法复现怎么办？

反馈进入`cannot_reproduce`，不生成修复和PR。这是正常终态，表示当前固定源码和确定性
测试没有证明问题。维护者可以结合原始样例决定是否补充信息或手工检查，但Agent不会猜测。

### 50. 为什么每轮都从原始`base_sha`开始？

如果第二轮在第一轮临时文件上继续，结果会依赖隐藏状态，最终补丁也难以重放。当前每轮都
重新物化固定源码，再应用该轮完整测试或修复Edit，保证补丁可以独立应用和审查。

### 51. 什么是受信任测试模板，为什么只给少数场景？

它是Agent主进程内预先编写的确定性测试生成逻辑，不是模型自由模板。目前只用于完整
Mermaid drawing和明确转换崩溃这两类已有可靠Oracle的场景。它仍经过补丁Policy和Docker。
其他问题没有足够固定语义，不能为了提高完成率套模板。

### 52. 为什么让模型输出结构化Edit，而不是unified diff？

Edit明确包含路径、模式、搜索文本和替换文本，容易做Schema和跨字段检查。统一diff有行号、
上下文和解析歧义，也更容易夹带重命名、权限或额外文件。系统最终仍生成标准patch供Docker
和GitHub使用，但这个转换由受信Python代码完成。

### 53. 补丁Policy检查什么？

它检查文件白名单、黑名单、修改阶段、文件数、增删行、总字节、二进制、符号链接、子模块、
重命名、权限、危险能力、`git diff --check`和是否能应用到`base_sha`。扩大白名单只能由维护者
修改代码和权威文档。

### 54. 如何防止模型删除、跳过或弱化测试？

修复阶段没有测试写权限，修复补丁和测试补丁路径必须互斥。最终组合补丁会再次确认新增
测试没有被删除或改变。测试运行时如果修改workspace，执行后diff校验也会拒绝。

### 55. 如何防止模型针对测试硬编码？

提示词禁止根据测试名、反馈ID或完整输入写特殊分支；修复文件范围很小；已有全量测试会与
目标测试一起运行。维护者还会在PR中看到实际diff。当前不能形式化证明“没有任何过拟合”，
但通过生成约束、回归测试和人工合并降低风险。

### 56. 修复目标测试通过后为什么不能直接创建PR？

单个测试通过可能来自缓存、测试污染、对其他行为的破坏或错误的最终补丁。系统还要在新
容器中重新证明基线失败、目标通过、全量pytest和登记的DOCX检查通过，并重新生成最终diff。

### 57. 最终验证具体执行什么？

从`base_sha`建立干净源码，只应用测试补丁证明目标失败；再应用修复补丁证明目标通过；运行
后端全量测试和DOCX结构检查；比较执行前后workspace；最后生成`ValidationResult`和
`validated.patch`。

### 58. 为什么给最终补丁计算SHA-256？

哈希把“测试通过的字节”和“准备发布的字节”绑定起来。Publisher重新读取
`validated.patch`并计算哈希，必须与`validated_patch_sha256`一致才发布，避免验证后文件
被替换或错误组合。

## 六、Sandbox与安全

### 59. Docker沙箱是这个项目的什么优势？

优势不只是用了Docker，而是把不可信模型代码与持有密钥的Agent主进程分开：执行前检查
补丁，执行时无网络、非root、只读根文件系统和限资源，执行后解析JUnit并检查源码diff，
最终还用新容器复验。任何一层出错都不能直接获得主机和发布权限。

### 60. Sandbox Worker和任务容器有什么区别？

Worker是受信Python服务，使用独立Linux用户和`SANDBOX_*`配置，可以通过Docker组调用
Docker Engine。任务容器运行不可信代码，没有业务密钥和Docker Socket。生产中二者可以在
同一私有ECS，但权限和进程边界不同。

### 61. 任务容器有哪些权限限制？

它固定使用UID/GID 65532，`--cap-drop=ALL`，`no-new-privileges`，根文件系统只读，网络
关闭，最多2 CPU、2 GiB内存和256个进程。镜像使用固定digest，不能运行时安装依赖。

### 62. 容器完全不能写文件吗？

不是。根文件系统只读；`/tmp`是`noexec/nosuid/nodev`的512 MiB tmpfs；`/result`用于
JUnit；`/workspace`是受控挂载，现有源码目录和文件对固定非root用户只读。执行后任何超出
批准补丁的workspace变化都会被拒绝。

### 63. 为什么禁用网络？

测试不需要联网。禁网可以阻止下载代码、访问GitHub、模型服务、云元数据和外部回传数据，
也让测试结果更确定。Mermaid使用镜像内预装的CLI和Chromium，不需要运行时联网。

### 64. 如何保证容器里没有业务密钥？

Worker只读取`SANDBOX_*`环境，启动Docker CLI时只传`PATH`和`LANG`，容器只设置固定测试
变量。真实Docker测试检查Supabase、模型、Langfuse、GitHub和Worker凭据均不在环境中，且
Docker Socket不存在。

### 65. 为什么Docker里只能运行固定命令？

Job只有枚举类型，没有命令字符串。Worker根据类型映射为固定的`python -m pytest`或
`python -m compileall`参数，不使用`sh -c`。测试选择器还必须匹配小写字母、数字和下划线
规则，不能拼接Shell内容。

### 66. 如何防止死循环、fork bomb和内存耗尽？

Docker限制CPU、内存和进程数，Job有最长900秒墙钟时间，整次运行还有Sandbox累计预算。
stdout/stderr只保留尾部4 KiB，避免无限输出占满Agent内存。

### 67. 容器超时后怎样清理？

Worker先杀死Docker执行进程，再使用固定容器名执行`docker rm -f`，返回
`sandbox_timeout`。Python临时目录上下文随后删除源码和结果目录。即使清理命令失败，原Job
仍按超时终结，由主机容器巡检处理残留，不复用旧容器。

### 68. Sandbox请求超时后重试会不会运行两次？

Client包含首次最多三次attempt，并使用相同`job_id`、`Idempotency-Key`和请求指纹。
Worker用锁覆盖“查询结果、执行、保存”，相同请求完成后直接返回持久化结果；相同ID但内容
不同返回冲突。这使网络重放安全，但不能把ID换掉后再称为同一次重试。

### 69. 如何发现测试运行时偷偷修改源码？

Worker在容器前记录授权diff，容器结束后从容器看不到的Git基线重新计算diff。两者字节不
一致就返回`security_rejected/workspace_modified`，该结果不能进入后续验证。

### 70. 为什么复现和最终验证要使用不同新容器？

复用容器可能保留缓存、临时文件、进程或测试顺序影响。每个Job创建新workspace和新容器，
最终验证重新从固定源码开始，避免上一次执行的隐藏状态造成假通过。

## 七、领取、数据库与发布

### 71. Agent如何知道Supabase里有新反馈？

不是数据库推送。私有ECS上的Scheduler每轮主动调用Supabase领取函数，空闲时两轮之间约5
秒。正常感知延迟约0到5秒，还要加上当前单任务的排队时间。

### 72. Scheduler是cron定时任务吗？

不是。它是systemd管理的常驻Python进程，内部`run_forever`循环查询、执行并等待。systemd
负责启动、异常重启和权限限制；cron不会每次重新启动Agent脚本。

### 73. 怎样避免两个进程领取同一条反馈？

Supabase RPC在事务中用`FOR UPDATE SKIP LOCKED`选一行并立即更新为`claimed`。其他领取者
会跳过被锁行。领取同时增加尝试次数并生成新`claim_token`。

### 74. `claim_token`和租约分别解决什么问题？

租约让“领取后进程消失”的反馈在超时后可以重新领取；`claim_token`让旧进程即使稍后恢复，
也不能覆盖新领取者的状态。所有后续反馈更新必须匹配当前token。

### 75. 为什么生产Scheduler当前单并发？

反馈量小，单并发能避免模型和Docker抢资源，也减少多个补丁同时基于旧`main`。代价是长任务
会让后续反馈排队。以后增加并发前要评估ECS资源、基线过期率和数据库领取，不只改一个数字。

### 76. Supabase暂时不可用怎么办？

系统不会在本地伪造领取或成功状态。Repository异常使当前服务失败，systemd随后重启；恢复
后先查未完成run和checkpoint。反馈入口写库失败返回脱敏502，已经消费的限流额度不返还。

### 77. 怎样识别重复反馈？

系统对规范化后的`feedback_type + markdown_content + description`计算SHA-256内容指纹，
查询已有记录后把精确重复路由为`duplicate`。联系方式不参与指纹，避免同一问题因联系方式
不同重复修复。

### 78. 修复期间`main`发生变化怎么办？

发布前读取当前`main`，与任务开始固定的`base_sha`比较。不同则不写GitHub，第一次把反馈
重新排队，从新基线完整复现；第二次仍过期转`needs_human`。系统不让模型自动rebase旧补丁。

### 79. 如何避免网络重试创建两个PR或Issue？

Publisher使用确定性分支、提交信息、PR marker、feedback ID和补丁哈希。恢复时先查已有
分支和PR；如果GitHub已创建但本地尚未保存，就复用已有结果。同一反馈和补丁不能存在多个
打开的PR。Issue使用不可逆run reference和内容指纹marker，创建前同时查询开放和关闭Issue；
网络响应丢失或Issue被人工关闭后恢复，都复用原Issue。

### 80. 为什么后端修复只创建PR，不自动合并？

DOCX结构测试能检查ZIP、XML、公式、表格和drawing数量，但不能完全替代在Word里观察版式。
维护者需要查看代码、Trace、测试和实际文档效果。人工合并是明确质量边界，不是Agent流程
遗漏。

## 八、观测、网站与生产验收

### 81. Supabase和Langfuse分别保存什么？

Supabase的`feedback`和`agent_runs`是业务状态与最终摘要的事实来源，PostgreSQL checkpoint
用于Graph恢复。Langfuse保存模型、工具、耗时和Token的观察记录；Langfuse失败不能改变
任务状态。

### 82. Artifact是什么？

它不是抽象平台概念，而是某个`run_id`在Agent服务器磁盘上的过程文件，例如`gate.json`、
`test.patch`、`fix.patch`、`validated.patch`和`publication.json`。中文文档称为“Agent本地
运行文件”，State只保存它们的地址和哈希。

### 83. 追踪网站是实时的吗？

不是逐节点WebSocket或SSE。Agent进入终态后通知Vercel，页面缓存失效，网站后台抓取Trace。
因此是“运行结束后近实时更新”，运行过程中不保证浏览器同步显示每个节点。

### 84. 网站使用推模式还是拉模式？

两者结合。Agent推送的只有`run_id`和`status`完成信号；Vercel收到后再从Supabase拉运行
摘要、从Langfuse拉调用明细，并把安全投影upsert到`agent_run_traces`。完整Trace不会通过
Webhook传输。

### 85. Langfuse数据延迟或通知丢失怎么办？

Agent通知前先`flush`。Vercel后台第一次找不到Trace时等待4秒、12秒重试。通知本身只尝试
一次；如果丢失，用户打开终态详情且快照缺失时触发一次按需补抓。Trace最终仍缺失时，页面
继续显示Supabase摘要。

### 86. 一次Agent失败应该怎样定位？

先看`agent_runs.status/route/error_code`，再在Langfuse找最后一个模型或工具节点；模型错误
区分传输失败与结构失败；Sandbox看Worker错误码、JUnit和workspace diff；发布看`base_sha`、
当前main和已有PR；最后按`run_id`检查checkpoint和本地运行文件。

### 87. 如何防止日志和Trace泄露用户内容或密钥？

联系方式在模型前移除；Trace默认`TRACE_CONTENT=false`，只写路径、哈希、计数和状态；
错误不回显Provider、Worker和GitHub响应正文；stdout/stderr有长度和密钥模式清理；网站只读
字段白名单视图，不查询`feedback`表。密钥始终由对应适配器私有持有。

### 88. 为什么单元测试通过不代表生产完整链路正常？

单元测试通常使用Fake Provider、内存checkpoint和Mock HTTP，无法证明真实模型严格Schema、
Supabase权限、Langfuse异步索引、GitHub App权限、ECS systemd、Docker cgroup、网络隔离和
Render部署都正确。真实服务验收也不能替代自动测试，因为它难以覆盖所有错误分支。

本项目按层验证：单元/集成测试检查确定性规则，真实Docker检查隔离，手工端到端检查
Supabase、模型、Langfuse、GitHub和Worker，PR合并后再用原Markdown在生产Render导出并用
Word查看。只有这些证据分别成立，才能说明完整链路正常。

## 代码入口

阅读这88个问题时，不需要相信文档结论。可以用下面的入口检查回答是否仍符合当前实现。
最关键的判断方法是看“模型调用时传了什么”和“条件边由谁返回”。例如Gate源码明确传
`tools=()`，Graph源码明确由Python函数返回`revise/finish/publish`，这两处可以直接证明模型
既不能自由拿工具，也不能决定状态跳转。

- [LangGraph State](../../agent/state.py)
- [LangGraph节点](../../agent/graph.py)
- [Scheduler](../../agent/scheduler.py)
- [模型Provider](../../agent/providers/openai_compatible.py)
- [工具授权](../../agent/tools/authorization.py)
- [补丁Policy](../../agent/workspace/patch_policy.py)
- [Sandbox Client](../../agent/sandbox/client.py)
- [Sandbox Worker](../../agent/sandbox/worker.py)
- [Docker Runner](../../agent/sandbox/docker_runner.py)
- [发布模块](../../agent/publishing/github.py)
- [追踪网站通知](../../agent/operations/site_notify.py)
- [反馈入口限流](../../backend/app/feedback_rate_limit.py)
- [反馈API](../../backend/app/main.py)
