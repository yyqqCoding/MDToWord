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
