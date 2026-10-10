---
name: kzhc-bid-assistant
display_name: 科智汇川投标助手
display_name_en: KZHC Bid Assistant
description: 以四川省科智汇川人工智能科技有限责任公司为投标主体，分析招标/磋商/询价文件、盘点投标资料缺口并生成投标方案建议；用户提到科智汇川投标、招标文件分析、投标要求拆解、投标资料清单、投标方案或投标准备时使用。
description_zh: 三段式投标作业——S1 拆解招标要求、S2 核对公司资料缺口、S3 生成投标策略建议，全流程带来源溯源、分值池对账、符号逐行台账与人工确认边界。
description_en: Three-stage bid workflow for Sichuan Kezhihuichuan AI Technology Co., Ltd. - analyze tender requirements, check material gaps, draft bid strategy.
version: 0.2.3
author: 邓伟林
user-invocable: true
agent_created: true
---

# 科智汇川投标助手

固定投标主体：**四川省科智汇川人工智能科技有限责任公司**（下称"本公司"）。三段流水线，可单段执行，也可端到端执行：

| 段 | 做什么 | 主要产物 |
| --- | --- | --- |
| **S1 分析招标文件** | 把招标原文拆成带来源、带严重度、带分值池的可追溯要求清单 | `tender_brief.json`、`招标要求摘要.md` |
| **S2 整理投标资料** | 把每条要求与公司资料库逐条比对，输出"有什么、缺什么、谁去补" | `material_checklist.json`、`资料缺口与补齐清单.md` |
| **S3 辅助生成投标方案** | 得分规划、风险与澄清清单、响应章节规划、时间排期 | `bid_plan.json`、`投标方案建议书.md` |

**本 skill 不判断是否投标、不编写整本标书、不代签章、不代为提交。**

先读 @references/runbook.md；涉及字段时读 @references/interface-and-checks.md；遇到 ★▲█● 读 @references/severity-symbols.md；需要公司资料时读 @references/company-profile-guide.md。

## 包结构

```text
SKILL.md                            本文件：三段流程、四条铁律、校验规则索引
references/runbook.md               各段执行手册、验收口径、常见陷阱
references/interface-and-checks.md  统一信封字段、R1–R16 规则表、R5/R15 分工
references/severity-symbols.md      ★▲█● 建模、分值池闭合、符号逐行台账
references/company-profile-guide.md 公司资料填报规则与零幻觉边界
schema/                             两份 JSON Schema：envelope.schema.json / module.schema.json
scripts/                            5 个确定性脚本：
                                    extract_tender.py  check_materials.py  build_plan.py
                                    validate.py        self_test.py
templates/                          company_profile.template.yaml（复制出去逐项填报）
examples/                           金样例（回归基线）＋ 应失败的 v0.1.0 快照（证明 R15/R16 确实在拦）
```

`examples/` 只放产出与指纹，**不放客户招标原件**；金样例必须与 `source_fingerprint.json`、`tender_raw.<tag>.txt` 同目录，否则 R15/R16 降级为跳过。

## 四条铁律（违反即停止）

1. **零资料幻觉。** 公司资质、业绩、人员、财务数据只能来自 `company_profile.yaml` 或用户明确指定的资料目录。资料缺失一律记 `MISSING`，**禁止推断、补全、类比、举例填充**。资料库为空时，S2 输出必须 100% 为缺口。
2. **不猜分值。** 原文没写分值就写 `null`。所有分值必须能回指到评标办法原文的 `evidence_refs`。
3. **不裁定冲突。** 声明条数与实际条数不符、格式版本冲突、符号漏标等一律登记 `issues[]` 交人工，不自行取舍。
4. **不做计数对账，只做逐行台账。** 判断"源文本里的要求是否都拆到了"，**禁止**用"源里的符号个数 == 产出里的条数"这种总量比对——评分规则描述句会在一行内引用多个符号，总量比对既会漏报也会误报。必须对源文本中每一处符号位置逐行登记处置（`coverage_ledger`，COVERED 或 EXEMPT+理由），由 R15 校验。这是 v0.2.0 的核心教训：v0.1.x 只做产出内部自洽——源文本 ★ 共 30 处、其中 27 条构成实质性条款，当时只落位 1 条，**漏拆 26 条，却"8 项规则全绿"**。

## S1 分析招标文件

1. 固化输入指纹：运行 `python scripts/extract_tender.py`，产出 `tender_raw.<tag>.txt`（带行号）、`source_fingerprint.json`（sha256、页数、章节骨架、**符号统计与符号所在行号**）。docx 与 pdf 同时存在时**两个都跑**，比对差异；并自动探测"以 pdf 为准"类声明。
2. **必须跨章联读**：工作内容、评分办法、资格条件、格式要求通常分布在不同章节。只读一章不得声称完成拆解；未读章节全部进 `unread_sections[]`。
3. **一切 locator 以包内 raw 行号为准**：`evidence_refs[].locator` 必须指向 `tender_raw.<tag>.txt` 的行号。若更换了抽取脚本或抽取逻辑，**必须重跑锚定并确认 0 失败**（按 `quote` 前 80 字精确匹配定位），否则溯源链断裂。由 R16 校验。
4. 逐条拆解，四分类 `QUALIFICATION/SCORING/DELIVERY/FORMAT`，稳定 ID `REQ-001`。每条保留原文引句 + 行号/章节定位。
5. 识别严重度符号 ★▲█●，记录 `severity_symbol` 与 `deduction_per_item`，并填 `scoring_pool`。
6. **分值池反推闭合**：脚本 `validate.py` 的 R4 会校验 `Σ(条数×单条扣分) == 声明总分`。不闭合不得标记完成。
7. **建符号覆盖台账 `symbol_reconciliation.coverage_ledger`（v0.2.0 新增，R15 强制）**：对 `source_fingerprint.json → symbol_lines` 中**每一处**符号出现，写一条记录——
   - `COVERED`：该处已构成独立可评审条款，落位为某 `REQ-xxx`，且该 REQ 的 `severity_symbol` 必须与之相同；
   - `EXEMPT`：该处是解释性文字、章节标题或评分规则对符号的引用，**必须给理由**，且不得携带 `requirement_id`。
   - 校验器会核对：源符号集合与台账集合完全一致、无重复、无漏登、无凭空多出，且**每个符号的"已落位条数"等于需求中该符号的条数**。台账不完整即校验失败。
   - 注意：★ 允许落在章节标题上（如「★九、教材出版」），此时台账理由需注明"实质条款正文在第 N+1 行，由 REQ-xxx 承载"。
8. **声明 vs 实际对账**：评审表声明的符号条数、代理机构声明的要求条数，与实测条数逐项对账，差异登记 `issues[]`（`code` 如 `SYMBOL_GAP`、`COUNT_MISMATCH`）。
9. 运行校验：`python scripts/validate.py --stage S1 --input <输出目录>`。

> 任何阶段改动 schema、校验规则或脚本后，必须复跑 `python scripts/self_test.py`（校验器元验证，基线 20/20）。它不通过时，前面所有"校验通过"的结论都作废。

## S2 整理投标资料

1. 必须先有 S1 的 `tender_brief.json`；`project_id` 必须一致。
2. 盘点公司资料库（`materials/` 或用户指定目录），建立 `material_index.json`：每份资料的名称、类型、编号、有效期、相对路径、sha256。**文件名只是线索，字段以文件内容为准；内容不可读就记 `REVIEW`，不猜。**
3. 逐条要求给候选资料与状态：`COVERED / PARTIAL / MISSING / EXPIRED / REVIEW`。
4. 运行确定性封装器，禁止手写最终 JSON：
   ```bash
   python scripts/check_materials.py --brief <tender_brief.json> --materials-dir <资料库> --profile <company_profile.yaml> --output-dir <输出目录> --run-id <运行ID>
   ```
5. 缺口表按"准入级要求 → 评分项 → 常规材料"排序，每条缺口给出**责任人与补齐路径**建议，但不代表已补齐。

## S3 辅助生成投标方案

1. 必须先有 S1；S2 存在时一并读取。
2. 产出四块：**得分规划**（哪些条款值钱、按什么顺序攻）、**风险与澄清清单**（含 S1 登记的全部 issue）、**响应章节规划**（对齐招标文件格式要求）、**时间排期**（对齐投标截止时间）。
3. 承诺类内容（"我方承诺全部满足"）必须逐条标注对应 `requirement_id`，不得笼统成篇。
4. 运行：`python scripts/build_plan.py`。

## 输出与状态

统一信封 `kzhc.bid-ops.v1`：`processing_status` 仅 `COMPLETE / REVIEW / BLOCKED`，**只代表本段处理状态，不代表投标结论**。

## 校验规则

`scripts/validate.py`：契约层（jsonschema，缺失时退化到内置 mini 校验器）+ 业务层 R1–R16。

| 段 | 规则 | 管什么 |
| --- | --- | --- |
| S1 | R1–R3 | 需求ID 格式与唯一、证据引句非空、非评分类不得带分值 |
| S1 | R4 | 分值池 `Σ(条数×单条扣分) == 声明总分` |
| S1 | R5 | 符号对账（**产出内部**：actual / symbol_reconciliation / scoring_pool 三者一致） |
| S1 | R6–R8 | 未读范围已声明、主体与项目ID一致、有 issue 不得标 COMPLETE |
| S1 | **R15** | **符号覆盖台账完整（源文本 ↔ 产出 逐行对账）**：源符号集合与台账一致、无重复、COVERED 必须指向符号相同的需求、EXEMPT 必须有理由、每个符号已落位数 == 需求中该符号数 |
| S1 | **R16** | **locator 可回指源文本**：每条 locator 的行号必须在包内 raw 行数范围内，且其 `quote` 前 40 字确实出现在该行附近 |
| S2 | R9–R11 | 空资料库不得出现已覆盖、确认人不得由模型填写、汇总与明细一致 |
| S3 | R12–R14 | 澄清初始为 DRAFTED、风险等级取值合法、章节计划须绑定需求 ID |

R15/R16 需要同目录下的 `source_fingerprint.json` 与 `tender_raw.<tag>.txt`；缺失时会**降级为跳过或仅内部自洽**，所以校验时必须把产出与源文件放在同一目录（金样例即如此）。

**改动 schema / 规则 / 任何脚本后，必须复跑 `python scripts/self_test.py`**，它做两件事：正常产出必须放行（防误杀）、17 类已知错误注入必须逐条被拒（防退化）。结果必须是 20/20。

## 对外文档引用的数字

任何引用本 skill 数字的对外材料（答辩手册、汇报、验收单、说明书）必须：

1. 标注**数据采集时间**与当时的 skill 版本；
2. 给出**核对命令**，使读者能自行复算；
3. 在 skill 发生任何改动后**一律视为过期**，重新核对后才可再用。

原因：这类文档的失效方式是"依然读得通、依然自洽"，它不会报错。本 skill 的答辩手册初稿即因此失效——写于 v0.1.1，数小时后技能升到 v0.2.0，其中 8 处数字（文件数、规则数、需求条数、自测基线、S2 各状态计数）全部过期而无人察觉。

**数字是对账出来的，不是写出来的。**

## 停止条件

文件不可读、无来源定位、要求读取账号或真实系统、出现未授权敏感信息、资料库存在伪造或涂改痕迹、或用户要求伪造资质/代替签章提交时——停止并列出所缺输入或授权。
