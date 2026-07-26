# 阶段 07:修复循环 + pytest/DOCX 验证(第 3 个可用版本)

## 目标

在复现确认后,让模型生成最小修复;由独立 Validator(不是模型)判断修复
是否达到可建 PR 的标准,产出机器可读的 validation report。

## 前置依赖

阶段 06(复现判定与失败摘要)。

## 交付物

```text
agent/schemas/fix_generation.py
agent/prompts/generate_fix.md(PROMPT_VERSION=generate-fix-v1)
agent/validators/docx_validator.py  report.py
```

## 实施内容

### 1. 修复 Schema 与上下文

```python
class FixGenerationResult(BaseModel):
    edits: list[Edit]                # 结构化编辑(ADR-006),仅白名单源码路径
    summary: str
    risk_level: Literal["low", "medium", "high"]
    behavior_changes: list[str]
    manual_review_notes: list[str]
```

提供给模型:脱敏反馈、分类结果、test patch、目标测试失败摘要、
允许修改的源码、基线 commit、禁止修改规则、输出 Schema。
不提供:API Key、Supabase Header、GitHub Token、联系方式、环境变量、`.github` 文件。

### 2. 修复循环(在 Job B 内,ADR-007)

```text
Round 1:生成 edits → 生成 diff → Patch Policy → 沙箱内应用 test+fix → 目标测试
  ├─ 通过 → 进入全量验证
  └─ 失败 → 结构化错误摘要(含上一轮补丁摘要,不堆叠历史全文)
              ↓
          重置工作区(git checkout -- . && git clean -fd)→ Round 2
              ↓
          仍失败 → needs_human
```

上限 `MAX_REPAIR_ROUNDS=2`;每轮独立记录(轮次、patch hash、失败摘要写入
`agent_runs.stage_timings` / `reproduction`);
禁止的"修复"模式见 [security-policy §7](00-overview/security-policy.md),
其中"删除/削弱新增测试"由确定性检查兜底:fix patch 不得触碰 test patch 新增的文件行。

### 3. pytest 全量验证

```bash
cd backend
python -m pytest tests/test_feedback_regressions.py -k <selector> -q --junitxml=target.xml
python -m pytest -q --junitxml=full.xml
```

全量必须 exit 0;不允许以 skip 原有测试的方式"通过"(junit 中 skipped 数不得增加)。

### 4. DOCX Validator(`docx_validator.py`)

直接调用现有 `app.pandoc_runner.convert_markdown_to_docx`。

基础检查:非空、`PK` 头、ZIP 可开、`[Content_Types].xml` 与
`word/document.xml` 存在、XML 可解析。

分类专项:

| 类别 | 检查 |
|---|---|
| 表格 | `.//w:tbl` ≥ 期望值;可加三线表边框检查(top/bottom single、insideV nil、表头行下边框) |
| 公式 | `.//m:oMath` + `.//m:oMathPara` ≥ 期望值;**或**预期 `ConversionError`(双路径,见阶段 06) |
| 标题 | `w:pPr/w:pStyle` 样式符合预期(style ID 需按 `backend/app/reference.docx` 实测确认,不写死) |
| 残留 | 提取全部 `w:t` 文本:无 Markdown 分隔行残留、无 TeX 命令作为普通文本 |

命名空间复用 `pandoc_runner.DOCX_XML_NAMESPACES`。

### 5. Validation Report(`report.py`)

```json
{ "passed": true,
  "target_test": { "passed": 1, "failed": 0 },
  "full_pytest": { "passed": 48, "failed": 0, "skipped": 0 },
  "docx": { "valid_zip": true, "document_xml": true, "tables": 1,
            "math_nodes": 2, "unparsed_markdown": false },
  "changed_files": ["backend/app/normalizer.py",
                    "backend/tests/test_feedback_regressions.py"],
  "rounds_used": 1 }
```

该 report 直接复用为阶段 08 的 PR 正文素材。

## 验收清单

- [ ] 集成测试(Fake Provider + 预制补丁)覆盖:一轮修复成功 / 第一轮失败第二轮成功 /
      两轮均失败进 `needs_human` —— `python -m pytest agent/tests -q`;
- [ ] fix patch 触碰新增测试文件时被拒绝(单测);
- [ ] 全量 pytest 有回归时整体判失败;skipped 增加时判失败(单测);
- [ ] 无效 ZIP、缺 `document.xml` 被 DOCX Validator 拒绝(单测);
- [ ] 表格/公式两类专项断言各有 fixture 用例通过;公式类双路径
      (`ConversionError` / 缺节点)均覆盖;
- [ ] `validation.json` 生成且字段齐全,`rounds_used` 正确;
- [ ] 每轮模型调用前工作区已重置(集成测试断言残留文件被清除)。

## 状态

未开始

## 验收记录

(完成后填写)
