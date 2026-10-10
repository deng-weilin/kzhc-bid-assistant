# 接口与验收

## 统一信封 `kzhc.bid-ops.v1`

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 固定 `kzhc.bid-ops.v1` |
| `module` | `S1` / `S2` / `S3` |
| `run_id` | 本次运行唯一编号 |
| `project_id` | 项目唯一编号，三段必须一致 |
| `bidder_name` | 固定 `四川省科智汇川人工智能科技有限责任公司` |
| `source_mode` | `DEMO` / `REAL_INPUT` / `MIXED_DEMO` |
| `input_refs[]` | 每份输入文件的 `source_id`、文件名、相对路径、类型、来源、sha256、版本 |
| `processing_status` | `COMPLETE` / `REVIEW` / `BLOCKED`，**只代表本段处理状态** |
| `issues[]` | `code`、`message`、`source_ref`、`owner`、`action` |
| `data` | 各段专属负载 |
| `next_actions[]` | `action`、`owner` |

## 与既有 anmeiqin 链的映射

| 本 skill | anmeiqin 链 | 关系 |
| --- | --- | --- |
| S1 | T1 招标要求拆解 | 功能等价，S1 增加 `severity_symbol` / `scoring_pool` / `symbol_reconciliation` |
| S2 | T3 人员资质匹配 | S2 范围更宽（含资质、业绩、财务、模板），人员证书为其中一类 |
| S3 | T2 响应矩阵 + T4 文档装配 | S3 只到"规划与建议"，不生成正式标书文档 |

两套信封结构兼容，`project_id` 与 `bidder_name` 语义一致，可互相读取；但**不要混用**：anmeiqin 链的固定主体是另一家公司。

## 校验规则

`scripts/validate.py` 分两阶段执行：契约层（JSON Schema）+ 业务层（R 规则）。

### 契约层

用 `schema/module.schema.json`（内部 `$ref` 引用 `envelope.schema.json`）。失败即不通过。

### 业务层

| 规则 | 内容 | 阶段 |
| --- | --- | --- |
| **R1** | 需求 ID 格式 `REQ-\d{3}` 且唯一 | S1 |
| **R2** | 每条要求至少一条 `evidence_refs`，且 `quote` 非空 | S1 |
| **R3** | 非评分类要求不得携带分值（`category != SCORING` 时 `score` 必须为 `null`） | S1 |
| **R4** | 分值池闭合：`Σ(count × deduction_per_item) == declared_total` | S1 |
| **R5** | 符号对账：`symbol_reconciliation.actual` 与 `requirements[]` 中实际符号条数一致 | S1 |
| **R6** | 未读范围已声明（可为空数组，但字段必须在） | S1 |
| **R7** | `bidder_name` 为固定值；`project_id` 三段一致 | 全部 |
| **R8** | `processing_status` 与 `issues[]` 一致：有 `issue` 时不得为 `COMPLETE` | 全部 |
| **R9** | （S2）资料库为空时，`items[]` 不得出现 `COVERED`/`PARTIAL`/`EXPIRED` | S2 |
| **R10** | （S2）`confirmation.confirmed_by` 不得由模型填写 | S2 |
| **R11** | （S2）`summary` 与 `items[]` 实算一致（total / covered / missing） | S2 |
| **R12** | （S3）`clarification_items[].status` 起始只能为 `DRAFTED` | S3 |
| **R13** | （S3）`risk_items[].level` 取值限于 `FATAL/HIGH/MEDIUM/LOW` | S3 |
| **R14** | （S3）`section_plan[].source_requirement_ids` 不得为空 | S3 |
| **R15** | 符号覆盖台账完整：`symbol_reconciliation.coverage_ledger.entries` 必须逐行登记源文本中每一处 ★▲█●；无重复、无漏登、无凭空多出；`COVERED` 指向的需求符号必须相同；`EXEMPT` 必须有理由且不得带 `requirement_id`；每个符号"已落位数" == 需求中该符号条数 | S1 |
| **R16** | locator 可回指源文本：行号须在包内 raw 行数范围内，且 `quote` 前 40 字确实出现在该行附近 | S1 |

**R15 / R16 的输入依赖：** 二者需要读取产出**同目录**下的 `source_fingerprint.json` 与 `tender_raw.<tag>.txt`。找不到源文件时，R15 降级为"仅内部自洽"、R16 直接跳过——**这种降级会掩盖缺陷**，所以校验时产出必须与源文件同目录（金样例目录即按此组织）。

退出码 `0` 为通过，`1` 为存在失败项。

### R5 与 R15 的分工（重要）

| | R5 | R15 |
| --- | --- | --- |
| 比较对象 | `actual` / `symbol_reconciliation` / `scoring_pool` —— **三者全在产出内部** | **源文本 `symbol_lines` ↔ 产出 `requirements[]`** |
| 能否发现"漏拆" | ❌ 不能。产出整体缩水时三者同步缩水，仍然一致 | ✅ 能 |
| v0.1.x 实测 | 8 项规则全绿，而 27 条实质性 ★ 条款漏拆 26 条 | v0.2.0 新增后当场拦下 |

**这是本项目最重要的一次教训：只做"产出内部自洽"的校验，等于没有校验覆盖度。** 校验器必须至少有一处与**外部源**对账。

## 验收口径

一次拆解"通过"的充要条件：

1. 契约层校验通过；
2. R1—R8 全部通过（S2 追加 R9—R11，S3 追加 R12—R14），**S1 还须 R15、R16 通过**；
3. 分值池闭合等式成立；
4. 所有差异项已进入 `issues[]` 且指定了 `owner`。

**不满足第 3 条时，不得标记 `COMPLETE`。** 这是本 skill 与"跑通即可"式作业的核心区别。

**第 2 条里的 R15/R16 必须"真的跑了"，不是"跳过也算过"。** 校验前确认产出同目录存在 `source_fingerprint.json` 与 `tender_raw.<tag>.txt`；若日志里 R15 写着"仅做内部一致性校验"、R16 写着"跳过"，那这次校验对覆盖度**没有证据**。

## 校验器自身的验证（元验证）

校验器不能自己证明自己。所以有一组固定的阴性对照：向合规产出注入已知错误，确认逐条被拒。

**一键复跑：**

```bash
python scripts/self_test.py          # 退出码 0 = 正常路径全放行 且 全部注入被拒
```

它先确认合规产出退出码为 0（不误杀），再逐条注入下表 17 类错误、确认退出码为 1（不放水）。

| # | 注入错误 | 期望命中 |
| --- | --- | --- |
| 01 | 篡改投标主体为别家公司 | 契约层 `const` + R7 |
| 02 | 分值池 `declared_total` 改为 47 | R4（差额 1.0） |
| 03 | 给非评分类要求硬塞分值 | R3 |
| 04 | 抽掉一条要求的 `evidence_refs` | R2 |
| 05 | 符号对账 `actual` 写虚（▲ 12→99） | R5 |
| 06 | 制造重复的 `requirement_id` | R1 |
| 07 | 仍有 `issues` 却标 `COMPLETE` | R8 |
| 08 | 空资料库却把条目判为 `COVERED` | R9（+ R11） |
| 09 | 模型代填 `confirmation.confirmed_by` | R10 |
| 10 | 澄清问题预置为 `SENT` | R12 |
| 11 | 章节计划不绑定 `source_requirement_ids` | R14 |
| 12 | 风险等级用非法取值 `SEVERE` | R13 |
| 13 | 整体删除符号覆盖台账 | R15 |
| 14 | 偷偷抹掉一条 ★ 而不更新台账 | R15 |
| 15 | 把已落位登记改成 `EXEMPT` 以逃避对账 | R15 |
| 16 | locator 行号越界 | R16 |
| 17 | locator 指向错误行 | R16 |

当前基线：**20/20 符合预期**（3 条正常路径 + 17 条注入）。

**改动 schema、校验规则或任一脚本后必须复跑 `self_test.py`，且必须仍为 20/20。** 出现任何一条不符合预期，就说明校验器在放水或误杀，此时前面所有"校验通过"的结论都作废。

**注入用例必须带上源文件。** R15/R16 依赖同目录的 `source_fingerprint.json` 与 `tender_raw.docx.txt`；`self_test.py` 会把这两个文件一并复制进临时目录。若不复制，两条规则会退化为"跳过"，注入用例就会假通过——这正是 13–17 号用例要防的坑。

**这个自测脚本本身也接受过反向检验**：把 `validate.py` 换成恒返回成功的空壳后复跑，`self_test.py` 报出 12 项不符合预期并以退出码 1 结束——证明它不是摆设。
