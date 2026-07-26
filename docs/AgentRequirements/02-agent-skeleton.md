# 阶段 02:Agent Python 骨架 + Feedback Repository

## 目标

Agent 成为仓库内可测试的普通 Python 包(逻辑不写进 YAML);封装全部 Supabase
访问,可读取、领取、更新反馈,并输出脱敏 task artifact。

## 前置依赖

阶段 01 完成(表与 RPC 就绪);`agent/requirements.txt` 首版:
`httpx>=0.27.0`、`pydantic>=2.8.0`、`pytest>=8.3.0`(不依赖具体模型 SDK)。

## 交付物

```text
agent/__init__.py  cli.py  config.py  domain.py  state_machine.py  exceptions.py
agent/feedback_repository.py  logging_utils.py
agent/requirements.txt
agent/tests/test_config.py  test_state_machine.py  fakes.py
```

目录全貌见 [architecture.md §3](00-overview/architecture.md)。

## 实施内容

### 1. 配置模型(`config.py`)

Pydantic `AgentConfig`:feedback_id、dry_run、supabase_url/key、
model_provider/name/api_key/base_url/timeout/max_output_tokens/temperature、
max_repair_rounds 及 [security-policy §3](00-overview/security-policy.md) 的各阈值、repo_root。
**不要在 import 时强制读取所有 Secret**,由 CLI 按子命令决定必需项。

### 2. CLI(`cli.py`)

```bash
python -m agent.cli fetch    --feedback-id <uuid> --output task.json
python -m agent.cli classify --task-file task.json
python -m agent.cli repair   --task-file task.json
python -m agent.cli validate --task-file task.json --test-patch test.patch --fix-patch fix.patch
python -m agent.cli finalize --result-file result.json
python -m agent.cli run      --feedback-id <uuid> --dry-run
```

MVP 可先实现 `run`,但内部必须调用拆分后的服务对象,方便阶段 08 分 Job。

### 3. Feedback Repository(`feedback_repository.py`)

```python
class FeedbackRepository(Protocol):
    def get_feedback(self, feedback_id: UUID): ...
    def claim_feedback(self, feedback_id: UUID, claim_token: UUID): ...
    def create_run(self, ...): ...
    def update_feedback(self, feedback_id: UUID, **fields): ...
    def update_run(self, run_id: UUID, **fields): ...
    def find_open_resolution(self, fingerprint: str): ...
```

实现 `SupabaseFeedbackRepository` 与 `FakeFeedbackRepository`(测试用)。
HTTP 要求:`httpx.Client`、每请求超时、认证 Header 只在 Repository 内构建、
错误响应截断记录、429/5xx 有限重试、401/403 不重试。

### 4. 内容指纹

**与原始文档的差异**:指纹用 `feedback_type`(用户提交时已有)而非模型分类结果,
使去重可在花钱调模型**之前**完成:

```python
def build_fingerprint(feedback_type: str, markdown: str, description: str) -> str:
    normalized = "\n".join([
        feedback_type.strip().lower(),
        markdown.strip().replace("\r\n", "\n"),
        description.strip(),
    ])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

### 5. 脱敏 Task Artifact

```json
{
  "feedback_id": "...", "feedback_type": "bug",
  "markdown_content": "...", "description": "...",
  "expected_behavior": null, "source_version": null,
  "fingerprint": "...", "claim_token": "..."
}
```

禁止包含:`contact`、`supabase_key`、任何 Authorization Header
(完整规则见 [security-policy §6/§8](00-overview/security-policy.md))。

## 验收清单

- [x] `python -m agent.cli --help` 正常;缺 `--feedback-id` 时报错信息明确;
- [x] Agent 单元测试通过 —— `python -m pytest agent/tests -q`,exit 0;
- [x] `fetch` 能读取阶段 01 的测试反馈并原子领取(二次领取失败);
- [x] 能创建 `agent_run` 记录;
- [x] `task.json` 中不存在 `contact` 字段 —— `python -c "import json;d=json.load(open('task.json'));assert 'contact' not in d"`(单测 + 结构上 TaskArtifact 无该字段);
- [x] 401 / 429 / 500 分别映射为不同错误码(单测覆盖);
- [x] 相同输入指纹稳定,CRLF 与 LF 归一后指纹一致(单测覆盖);
- [x] dry-run 路径不要求 GitHub 写权限(fetch 仅依赖 Supabase 凭据)。

## 状态

已验收(2026-07-26)

## 验收记录

- 日期:2026-07-26;分支:`feat/feedback-repair-agent`
- Agent 单测:**31 passed**(`python -m pytest agent/tests -q`,exit 0)
- 交付物齐备:`config / domain / state_machine / exceptions /
  feedback_repository / logging_utils / cli / requirements.txt / tests`
- 与 spec 的实现说明:
  - `fetch` 完整实现(读取→指纹去重→RPC 领取→建 run→脱敏 task);
    `classify/repair/validate/finalize` 留桩至对应阶段;
  - TaskArtifact 额外携带 `agent_run_id`,供阶段 08 Job E 回写(结构上无 `contact`);
  - CLI 退出码约定:0 成功 / 1 错误 / 2 参数错误 / 20 重复反馈 / 21 领取失败;
  - 错误码映射:401/403→`supabase_unauthorized`(不重试)、429→`supabase_rate_limited`、
    5xx→`supabase_server_error`(各有限重试 3 次)
- 真实 Supabase 验证(2026-07-26,测试反馈 `2d3d3eb0`):
  首次 fetch 领取成功(attempt_count=1,agent_run `0f9373b2` 已创建,
  task.json 输出且无 contact 字段);同一反馈二次 fetch 返回
  `claim_unavailable`;attempt_count 拉满时领取被拒(重置后方可领取)
