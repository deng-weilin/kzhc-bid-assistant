# 科智汇川投标助手 · 作业手册

> 本手册是 SKILL.md 的展开版。SKILL.md 讲"做什么"，本手册讲"怎么做、在哪一步会出错"。

---

## 0. 开工前：三件事必须先定

| 事项 | 为什么 | 不做的后果 |
| --- | --- | --- |
| **身份与资质边界** | 本公司能提供什么、不能提供什么 | S2 会产出无法补齐的缺口清单 |
| **投标主体信息** | 营业执照名称必须逐字一致 | 资格性审查直接废标 |
| **文件版本基准** | 招标文件常同时给 docx 与 pdf，且规定"不一致以 pdf 为准" | 拆出来的条款不是最终版 |

`project.bidder_name` 固定写 `四川省科智汇川人工智能科技有限责任公司`，与其他文件出现的简称（如"科智汇川""本公司"）做一致性核对。

---

## 0.5 环境准备（换机器/首次使用必做）

脚本依赖 `pypdf`（抽 pdf）与 `python-docx`（抽 docx）。用无依赖的 `python` 直接跑时，docx 往往**仍能抽出**，pdf 才抛 `ModuleNotFoundError: No module named 'pypdf'` —— 于是产物里少一份 pdf 指纹却不易察觉，`format_diff` 那句"docx 与 pdf 均已抽取"就成了空话。

**先确认再动手**（本机托管虚拟环境已装好依赖）：

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" -c "import pypdf, docx, jsonschema; print('deps OK')"
# 缺依赖时： "$PY" -m pip install pypdf python-docx jsonschema
```

本文档后续所有 `python scripts/xxx.py` 一律替换为 `"$PY" scripts/xxx.py`。

---

## 1. S1 分析招标文件 · 七步

### 步骤 1：抽取与指纹

```bash
python scripts/extract_tender.py --input <招标文件路径> --output-dir <工作目录>
```

产出：

- `tender_raw.txt` — 带行号的纯文本，行号是后续一切溯源的基础
- `source_fingerprint.json` — sha256、页数、章节骨架、符号统计、格式清单
- 若输入同时含 docx 与 pdf，两者都跑，脚本会输出 `format_diff` 提示差异

**常见错误**：直接从 PDF 复制文本。PDF 复制会丢符号、断行、吞表格。必须走脚本。

### 步骤 2：章节骨架

把文件切成"游戏规则"和"工作内容"两块：

- **游戏规则**：资格条件、评标办法、投标人须知、格式要求 → 决定能不能投、怎么得分
- **工作内容**：技术/服务/采购需求 → 决定要交付什么

两者通常在不同章节。**缺任一块，拆解都不成立**。

### 步骤 3：交付物清单

抽工作内容章节的交付物。特别注意**数量语义**，三者不能混用：

| 原文写法 | `quantity_semantics` | 含义 |
| --- | --- | --- |
| "不少于 5 个" | `AT_LEAST` | 下限，多给不算错 |
| "5—8 个" | `RANGE` | 区间，超区间可能废标 |
| "共 20 个" | `EXACT` | 精确值，必须等于 |

### 步骤 4：符号严重度

见 @severity-symbols.md。核心动作：**统计实测条数 → 读评标办法的扣分标准 → 反推分值池闭合 → 逐行建覆盖台账**。

最后一步别省：分值池闭合只管 ▲●█ 的算术，**管不了 ★ 有没有拆全**。必须对源文本每一处符号逐行登记 `COVERED / EXEMPT`，由 R15 拿指纹实际比对。

### 步骤 5：评分表

分清三类，`mandatory` 与 `score` 分别记录：

- **资格门槛**（不满足即废标）→ `mandatory: true`
- **实质性要求**（★ 类，不满足即废标）→ `mandatory: true`
- **评分项**（少满足只扣分）→ `mandatory: false`，填 `score` 或 `deduction_per_item`

### 步骤 6：交叉核对

最少做四项对账，差异全部登记 `issues[]`：

1. 评标办法声明的符号条数 vs 实测条数
2. 序号连续性 vs 带符号条数（**漏标符号常在这里暴露**）
3. docx vs pdf 表述差异
4. **源文本符号逐行集合 vs 台账集合**（比前三条更根本：前三条都在产出内部打转，这条才会发现"整段没拆"）

### 步骤 7：校验与产出

```bash
python scripts/validate.py --stage S1 --input <输出目录>
```

不通过不交付。校验规则见 @interface-and-checks.md。

**校验时产出必须与 `source_fingerprint.json`、`tender_raw.<tag>.txt` 同目录**，否则 R15/R16 会退化为跳过，这次校验对覆盖度就没有证据。

---

## 2. S2 整理投标资料

### 流程

1. 扫描资料库目录，建立 `material_index.json`
2. 按 `requirement_id` 逐条匹配
3. 运行封装器：

```bash
python scripts/check_materials.py \
  --brief <tender_brief.json> \
  --materials-dir <资料库目录> \
  --profile <company_profile.yaml> \
  --output-dir <输出目录> \
  --run-id <运行ID>
```

### 状态定义

| 状态 | 含义 | 允许谁改 |
| --- | --- | --- |
| `COVERED` | 有明确资料，字段齐全且在有效期内 | 脚本判定 |
| `PARTIAL` | 有资料但字段不全（如缺编号、缺签字页） | 脚本判定 |
| `MISSING` | 资料库中不存在 | 脚本判定 |
| `EXPIRED` | 存在但已过期 | 脚本判定 |
| `REVIEW` | 内容不可读或存在冲突 | 仅人工 |

`confirmation.confirmed_by` 起始为空。**模型不得把自己写成确认人。**

### 阴性对照（必做）

资料库为空或未提供时跑一次 S2，`covered` / `partial` / `expired` **必须全部为 0**（格式类要求判 `REVIEW`，其余判 `MISSING`）。若出现任何非零值，说明存在幻觉，必须回查后再用。

> 本项目实测：`covered=0 / partial=0 / missing=96 / expired=0 / review=24`（共 120 条要求，其中格式类 24 条判 `REVIEW`）。
> 首次运行时曾出现 7 条 `partial` —— 原因是空模板 profile 中的空业绩条目被当成"已有业绩"。
> 这正是阴性对照的价值：**空输入必须产出空覆盖。**

---

## 3. S3 辅助生成投标方案

### 四块内容

1. **得分规划** `score_plan[]` — 按"分值 × 我方把握度"排序，给出攻关顺序
2. **风险清单** `risk_items[]` — 必须包含 S1 的全部 `issues[]`
3. **澄清清单** `clarification_items[]` — 状态起始只能是 `DRAFTED`
4. **章节规划 + 排期** `section_plan[]` / `schedule[]`

### 承诺类文字的写法

| 写法 | 判定 |
| --- | --- |
| "我方完全响应招标文件全部要求" | ❌ 禁止。无法核对，且一旦有一条不满足即构成虚假响应 |
| "我方响应 REQ-007（提供等保三级备案证明），证书见附件 3-2" | ✅ 逐条可核对 |

---

## 4. 全流程检查清单

- [ ] 投标主体名称逐字一致
- [ ] 输入文件 sha256 已记录
- [ ] docx 与 pdf 均已抽取并比对
- [ ] 未读章节已声明
- [ ] 每条要求都有来源定位
- [ ] 无凭空分值
- [ ] 分值池闭合
- [ ] 符号条数已对账
- [ ] 缺口表责任人已指定（不代表已补齐）
- [ ] `confirmation` 为空或仅由人工填写
- [ ] `processing_status` 未被解读为投标结论
