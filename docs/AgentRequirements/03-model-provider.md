# 阶段 03:Model Provider 抽象

## 目标

通过统一接口调用模型,业务流程不绑定任何厂商。**MVP 只实现
`openai_compatible` 一个 Provider**(DeepSeek / Qwen / OpenRouter /
Anthropic OpenAI 兼容端点均可走它);Anthropic 原生 Messages API Provider
推迟到阶段 10,接口抽象本阶段就位。

## 前置依赖

阶段 02(config、CLI 骨架)。

## 交付物

```text
agent/providers/base.py  factory.py  openai_compatible_provider.py
agent/tests/test_providers.py(契约测试)
```

## 实施内容

### 1. 统一接口(`base.py`)

```python
class ModelUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None

class ModelProvider(Protocol):
    def generate_structured(
        self, *, system_prompt: str, user_payload: dict, response_model: type[T],
    ) -> tuple[T, ModelUsage]: ...
```

业务代码禁止出现 `if model_name.startswith("claude")` 之类分支;
一律 `ModelProviderFactory.create(config)`。

### 2. 能力约束(每个 Provider 必须支持)

系统提示词、用户载荷、超时、重试、结构化响应解析、Token 用量记录、
错误标准化、日志不落 API Key、自定义 Base URL、响应原文受控保存
(默认不保存完整用户 Markdown)。

### 3. 结构化策略(两种,按服务能力选择)

```text
native_schema:服务原生支持 JSON Schema 结构化输出
prompt_json:  提示模型仅输出 JSON → 去除可选 ```json 围栏 → json.loads
              → Pydantic 严格校验 → 失败附 Schema 错误摘要仅重试一次
```

### 4. 标准错误模型

```python
class ModelErrorCode(str, Enum):
    AUTH_ERROR = "auth_error";       RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout";             INVALID_RESPONSE = "invalid_response"
    CONTEXT_TOO_LARGE = "context_too_large"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SAFETY_REFUSAL = "safety_refusal"
```

### 5. 配置项

```text
MODEL_PROVIDER=openai_compatible   MODEL_NAME=<模型名>
MODEL_API_KEY=<GitHub Secret>      MODEL_BASE_URL=<兼容服务地址>
MODEL_TIMEOUT_SECONDS=120          MODEL_MAX_OUTPUT_TOKENS=12000
MODEL_TEMPERATURE=0                MODEL_MAX_REPAIR_ROUNDS=2
```

切换模型只改 GitHub Variables/Secrets,不改状态机、Schema、验证器、PR 逻辑。
多 Key 管理规则见 [security-policy §9](00-overview/security-policy.md)。
后续模型分工(低成本分类 + 强代码修复,`CLASSIFIER_MODEL` / `REPAIR_MODEL`)
在阶段 10 启用,MVP 全阶段同一模型。

## 验收清单

- [x] 契约测试通过 —— `python -m pytest agent/tests/test_providers.py -q`,覆盖:
      正常 JSON / 带围栏 / 多余前后文本 / 非法 JSON / 缺字段 / 枚举非法 /
      超时 / 429 / 401 / 500(Mock HTTP,不花真钱);
- [x] `FakeModelProvider` 可返回固定分类(供集成测试);
- [ ] 至少一次真实 API 调用成功(手动触发,记录 provider/model/token);
- [x] 非法 JSON 重试一次后仍失败 → `INVALID_RESPONSE`;
- [x] 日志中无 API Key —— 契约测试断言日志输出不含 Key 子串;
- [ ] 换一个 `MODEL_BASE_URL`/`MODEL_NAME`(如 DeepSeek → Qwen)零代码改动可跑通。

## 状态

进行中(代码与契约测试完成,待真实 API 调用验证)

## 验收记录

- 日期:2026-07-26;分支:`feat/feedback-repair-agent`
- Agent 全部单测 **52 passed**(其中 Provider 契约测试 21 个)
- 交付物:`providers/base.py`(ModelUsage / ModelErrorCode / ModelError /
  ModelProvider Protocol)、`openai_compatible_provider.py`、`factory.py`、
  `tests/test_providers.py`、`fakes.FakeModelProvider`
- 实现说明:
  - 结构化策略两种均实现:`prompt_json`(默认,Schema 注入系统提示 +
    剥围栏/前后杂文 + Pydantic 严格校验 + 失败附错误摘要重试一次)、
    `native_schema`(response_format json_schema);
  - 错误标准化:401/403→AUTH_ERROR(不重试)、429→RATE_LIMIT、
    5xx→PROVIDER_UNAVAILABLE(各重试 3 次)、超时→TIMEOUT、
    400 含上下文超限标记→CONTEXT_TOO_LARGE(不重试)、
    空内容→SAFETY_REFUSAL;error_code 形如 `model_rate_limit`;
  - Key 只进 Authorization Header,契约测试断言异常消息与 stderr 日志均无 Key;
  - 新增 `python -m agent.cli check-model` 冒烟命令(真实调用验证用,
    输出 provider/model/token 用量)
- 待验证(需真实模型 Key):`check-model` 成功一次并记录 token;
  换 `MODEL_BASE_URL/MODEL_NAME` 再跑一次证明零代码切换
