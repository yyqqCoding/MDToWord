# 阶段 06:测试生成与复现验证(第 2 个可用版本)

## 目标

模型先生成回归测试(不许碰业务代码);只应用测试补丁时,新测试必须在
基线代码上出现**目标失败**,以此证明问题真实存在(ADR-004)。

## 前置依赖

阶段 04(分类)、05(编辑格式与 Patch Policy)。

## 交付物

```text
agent/schemas/test_generation.py
agent/prompts/generate_test.md(PROMPT_VERSION=generate-test-v1)
agent/validators/pytest_validator.py(junitxml 解析)
backend/tests/docx_assertions.py(DOCX 断言工具,人工先建好骨架)
```

## 实施内容

### 1. 测试生成 Schema

```python
class TestGenerationResult(BaseModel):
    edits: list[Edit]                    # 阶段 05 的结构化编辑,仅允许 tests 路径
    target_test_selector: str            # 如 "feedback_ab12cd",Harness 拼进固定命令模板
    expected_failure_kind: Literal["conversion_error", "assertion"]  # 断言方向
    expected_failure_reason: str
    files_needed_for_fix: list[str]
```

Harness **不执行模型给的任意命令**,只用固定模板:
`python -m pytest tests/test_feedback_regressions.py -k <selector> -q --junitxml=...`。

### 2. 测试文件策略

统一放 `backend/tests/test_feedback_regressions.py`;
命名 `test_feedback_<short-id>_<行为描述>`(short-id = UUID 前 8 位小写字母数字);
不含完整 UUID、邮箱、原始问题描述;不依赖网络。

### 3. DOCX 断言工具(`docx_assertions.py`)

```python
assert_valid_docx(docx_bytes)
assert_docx_contains_table(docx_bytes, minimum=1)
assert_docx_contains_math(docx_bytes, minimum=1)
assert_docx_contains_paragraph_style(docx_bytes, style_id)
assert_docx_not_contains_text(docx_bytes, text)
extract_document_text(docx_bytes)
```

命名空间可复用 `backend/app/pandoc_runner.py` 中现成的 `DOCX_XML_NAMESPACES`。

### 4. 复现判定(关键逻辑)

只应用 test patch 后,在沙箱内执行目标测试(沙箱规则见
[security-policy §5](00-overview/security-policy.md)),用
`--junitxml` 结构化解析结果,**不用正则解析文本日志**:

1. 测试必须运行到断言阶段:junit 中该用例为 `<failure>`(断言失败)才算复现;
   `<error>` 且异常类型为 ImportError / SyntaxError / fixture 缺失 → 假失败;
2. 失败方向必须与 `expected_failure_kind` 一致。注意公式/崩溃类的双向性
   (当前实现 TeX 失败直接抛 `ConversionError`,见阶段 04):
   - `expect_conversion_error`:基线上 `pytest.raises(ConversionError)` **不该抛**
     才是"未复现";抛了且测试因此失败/通过的方向要按断言写法判定;
   - `expect_docx_missing_node`:基线上节点数不满足断言 → 复现成功;
   - 不能把"出现异常"一律当作复现成功;
3. 测试直接通过 → 未复现 → 重新生成一次测试,仍未复现 → `needs_human`;
4. 给模型的失败摘要(供阶段 07 修复用)控制在结构化小对象:

```json
{ "exit_code": 1, "failed_test": "test_feedback_ab12cd_...",
  "failure_kind": "assertion",
  "assertion": "expected at least 1 m:oMath node, found 0",
  "stderr_tail": "...≤4000 字符..." }
```

## 验收清单

- [ ] test patch 只含 `backend/tests/**` 路径,其他路径被 Patch Policy 拒绝(单测);
- [ ] 用一条已知可复现样例(如去掉 normalizer 某规则的临时分支)运行:
      新测试在基线失败,junit 解析出 `<failure>` 且方向与
      `expected_failure_kind` 一致;
- [ ] 构造 ImportError 场景(测试引用不存在的模块),判定为假失败而非复现成功(单测);
- [ ] 构造"测试直接通过"场景,进入重新生成→`needs_human` 路径(集成测试);
- [ ] `expect_conversion_error` 与 `expect_docx_missing_node` 两个方向各有一条
      fixture 用例通过判定逻辑单测;
- [ ] 测试名不含完整 UUID / 邮箱(patch policy 或生成后检查);
- [ ] 目标测试执行发生在 `--network=none` 容器内(workflow 阶段 08 复验,本阶段本地可跳过容器)。

## 状态

未开始

## 验收记录

(完成后填写)
