# 公司资料填报指南（零幻觉规则）

> **本文件是本 skill 安全性的核心。** 投标场景下，模型一旦"顺手补全"了公司资质或业绩，代理机构核查、评审专家质疑、乃至中标后无法履约，后果都由本公司承担。因此这里采用最严格的数据边界。

## 铁律

1. **公司事实只能有三个来源**：
   - `company_profile.yaml`（人工填报）
   - 用户明确指定并授权的资料目录
   - 用户在对话中明确给出的信息
2. **缺失一律记 `MISSING`。** 不允许推断、类比、举例、"一般情况下"、行业惯例填充。
3. **内容不可读就记 `REVIEW`。** 不猜文件里写了什么。
4. **不认证真伪。** 本 skill 只做"有没有、对不对得上"，不做"是不是真的"。
5. **不代填人名。** 拟任人员、授权代表、项目经理等姓名必须由人工提供。

## 为什么这条特别重要

培训演示资料永远是正确的，所以模型从来不需要"拒绝"。真实场景里，资料库通常是**残缺**的：

- 有营业执照，没有近三年财务审计报告
- 有人员证书，但证书已过期
- 有业绩合同，但甲方名称与业绩表不一致

这些**"缺"本身就是最重要的产出**——它决定了这次投标要不要继续、哪些材料要外采或提前办。被幻觉填平之后，这份价值就没了。

## `company_profile.yaml` 结构

见 @../templates/company_profile.template.yaml。字段分七类：

| 分类 | 内容 | 常对应要求类别 |
| --- | --- | --- |
| `identity` | 名称、统一社会信用代码、注册地址、法定代表人、成立日期、注册资本 | QUALIFICATION |
| `qualifications` | 资质证书：名称、编号、发证机关、有效期、是否在有效期内 | QUALIFICATION / SCORING |
| `financials` | 近三年财务指标、审计报告情况 | QUALIFICATION / SCORING |
| `performance` | 业绩：项目名称、甲方、金额、合同期、证明材料 | SCORING |
| `personnel` | 人员：姓名、岗位、职称、执业资格、社保情况 | QUALIFICATION / SCORING |
| `ip_assets` | 软件著作权、专利、科研成果 | SCORING |
| `capability` | 产品功能、平台能力、可现场演示的模块 | SCORING / DELIVERY |

## 填报要求

- 每个字段填 **`value` + `evidence`**（证明材料文件名或路径）。**没有 evidence 的 value 一律视为未填。**
- 有效期字段用 `YYYY-MM-DD`。**过期即 `EXPIRED`，不允许"预计已续期"。**
- 名称类字段（公司名、项目名、人员名）逐字照抄证件原文，不做简称、不做整齐化。
- `null` 表示未知，与空字符串区别对待：`null` → `MISSING`，空字符串 → `REVIEW`（说明填了但没填内容）。

## 首次使用

资料库为空时：

```bash
python scripts/check_materials.py --brief <tender_brief.json> --materials-dir <空目录> --profile <空 profile> --output-dir <输出目录> --run-id <ID>
```

预期结果：`covered`、`partial`、`expired` **三项必须全部为 0**；`missing + review == total_requirements`（其中 `review` 全部来自格式类要求，格式类不消耗公司资料）；`gate_missing` 等于全部准入级（`mandatory` 或 ★）要求数。

**这就是阴性对照。** 若 `covered`/`partial`/`expired` 出现任何非零值，说明脚本或模型在幻觉，必须修复后再用。

> 实测记录：本项目 120 条要求、空资料库、空模板 profile 下结果为
> `covered=0 / partial=0 / missing=96 / expired=0 / review=24`，`gate_missing=48`。
