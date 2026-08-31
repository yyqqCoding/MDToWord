# Scheduler、领取与恢复

## 1. Scheduler 做什么

Scheduler 是生产 Agent 的节流器。它每次最多处理一条反馈，先恢复已经领取但未终结的
run，再领取新的 pending 反馈。它不执行模型以外的额外业务判断，也不绕过 Graph。

单并发是有意的：2H2G 主机上的模型、PostgreSQL 和 Sandbox 已经足够繁忙；更重要的是
避免多个修复同时基于不同 main 生成冲突补丁。

## 2. 一次领取

领取过程由数据库原子操作完成：

1. 找到 pending 或租约已过期的反馈；
2. 写入 claim token、租约和尝试次数；
3. 创建或恢复 agent_runs；
4. Scheduler 带着 claim token 启动外层 Graph。

旧进程即使稍后恢复，也因 token 不匹配不能覆盖新结果。领取失败不应导致进程退出，下一
轮继续轮询。

## 3. 恢复顺序

重启或临时失败后，Scheduler 按下列顺序处理：

~~~text
有可恢复 run？
  -> 使用原 run_id、claim lease 和 checkpoint
  -> 没有则领取新的 pending feedback
~~~

Repair Agent 的内层 thread 为 repair:<run_id>。恢复会保留 phase、patch 引用、Sandbox
结果和累计模型/工具预算，不把恢复误当作新任务。

## 4. 租约和终态

租约只保护“谁当前可以写入”，不代表任务必然成功。运行进入 completed、failed、
needs_human、security_rejected、cannot_reproduce、stale_base 或 budget_exhausted 等
终态后，反馈不再被普通领取 RPC 选中。

stale_base 是发布时 main 变化的既有一次性重排：系统重新基于最新 main 验证，超过重排
次数后转人工。它不属于模型或 Sandbox 的短传输重试。

## 5. 启停

生产更新先停止领取，再更新代码和依赖，审计通过后由维护者显式 enable：

~~~bash
sudo mdtoword-agentctl disable
sudo git -C /opt/mdtoword pull --ff-only origin main
sudo bash /opt/mdtoword/deploy/agent/deploy.sh
sudo mdtoword-agentctl audit
sudo mdtoword-agentctl enable
~~~

enable 要求输入 ENABLE。服务 active 只说明进程存在，不能替代 audit、版本检查、Worker
认证和真实反馈验收。

## 6. 维护者观察

看到“反馈被领取但页面没有进展”时，先查看：

- agent_runs 的 status、phase、last_error_code、lease；
- Scheduler 最近日志；
- repair:<run_id> checkpoint 是否有新的模型/工具消息；
- Worker 是否监听本机端口并通过认证。

不要直接修改数据库状态来“解锁”任务。先判断是租约过期、可恢复错误、永久错误还是
真正的代码故障，再使用原 run ID 恢复。
