# 从用户反馈到Pull Request

## 1. 一句话说明

用户通过浏览器插件提交反馈，Render后端把反馈保存到Supabase。私有ECS上的Scheduler
每5秒查询一次Supabase，领取反馈后启动LangGraph。LangGraph依次完成分类、复现、修复、
独立验证和PR创建。模型生成的测试与修改后代码只能在无网络Docker容器中运行。Agent创建
PR后结束，最终合并和部署仍由维护者完成。

## 2. 完整流程

```text
浏览器插件提交反馈
        ↓ POST /feedback
Render FastAPI检查客户端IP和提交频率
        ↓
Supabase.feedback新增一行，status=pending
        ↓ 没有数据库推送
ECS上的Scheduler每5秒查询一次
        ↓
Supabase函数原子领取一条反馈，status=claimed
        ↓
创建agent_runs和LangGraph初始State
        ↓
分类与安全检查
        ├─ 无关、前端问题、疑似攻击、信息不足 → 记录原因并结束
        └─ 可自动处理的后端Bug
                ↓
        从GitHub main取得固定base_sha源码
                ↓
        生成回归测试并在Docker中证明原代码失败
                ↓
        生成修复补丁并在Docker中运行目标测试
                ↓
        用全新Docker容器重新验证基线失败、目标通过、全量测试通过
                ↓
        确认GitHub main仍等于base_sha
                ↓
        GitHub App创建分支、提交和PR
                ↓
        Agent通知Vercel追踪网站本次运行已结束
                ↓
维护者查看PR和Word实际效果，人工合并
        ↓
Render根据main分支重新部署转换后端
```

## 3. 数据分别保存在哪里

| 保存位置 | 保存内容 | 为什么放这里 |
|---|---|---|
| `feedback` | 用户反馈、领取状态、重试次数、最终结果 | Supabase是反馈状态的事实来源 |
| `agent_runs` | 每次运行的阶段、用量、错误、补丁哈希和PR地址 | 用于恢复、排障和网站列表 |
| PostgreSQL checkpoint表 | 每个LangGraph节点执行后的State快照 | Agent进程重启后继续执行 |
| Agent服务器运行目录 | 测试补丁、修复补丁、验证结果等大文件 | 避免把大段内容放进State |
| Langfuse | 模型调用、工具调用、耗时和脱敏摘要 | 用于查看一次运行内部做了什么 |
| `agent_run_traces` | 网站从Langfuse整理出的安全展示数据 | 页面访问不依赖Langfuse实时可用 |
| GitHub | 固定源码版本、Agent分支、提交和PR | 用于代码审核和人工合并 |

## 4. 哪些步骤由模型完成

模型只参与四类判断或生成：

1. 分类反馈；
2. 制定复现计划；
3. 生成结构化测试修改；
4. 生成结构化修复修改。

下列操作都由受信任的Python代码决定，模型没有执行权限：

- 领取反馈和更新数据库状态；
- 选择进入哪个LangGraph节点；
- 判断模型输出是否符合Schema；
- 检查读取路径和修改白名单；
- 把结构化修改转换成补丁；
- 决定Docker运行的命令和资源上限；
- 根据JUnit判断测试失败或通过；
- 创建GitHub分支和PR；
- 通知追踪网站。

## 5. 四条不能混淆的链路

### 用户反馈链路

```text
插件 → Render /feedback → Supabase.feedback
```

### Agent执行链路

```text
Scheduler → Supabase领取 → LangGraph → Sandbox Worker → GitHub PR
```

### 运行记录链路

```text
LangGraph节点/模型/工具 → Langfuse
LangGraph阶段结果 → Supabase.agent_runs
大文件 → Agent服务器运行目录
```

### 追踪网站链路

```text
Agent结束通知 → Vercel
Vercel → Supabase读取运行摘要
Vercel → Langfuse读取调用明细
Vercel → Supabase.agent_run_traces保存展示快照
浏览器 → Vercel页面
```

追踪网站使用“推送通知触发读取”，不是Agent把完整Trace推给网站，也不是浏览器与Agent
保持WebSocket连接。

## 6. 自动化在哪里结束

自动化终点是“PR已创建”，不是“代码已上线”。

```text
Agent验证通过并创建PR
        ↓
维护者审查代码、测试和Word实际效果
        ↓
维护者手动合并
        ↓
Render部署
```

保留人工合并是刻意设计：自动测试可以检查DOCX结构，但不能完全替代在Word中查看版式。
