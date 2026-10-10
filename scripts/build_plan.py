#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_plan.py — S3 辅助生成投标方案

消费 S1 的 tender_brief.json（可选叠加 S2 的 material_checklist.json），
输出得分规划、风险清单、澄清清单、章节规划与时间排期。

不生成正式标书正文，不代拟承诺内容，不代替签章与提交。

用法:
    python build_plan.py --brief <tender_brief.json> [--checklist <material_checklist.json>] \
        --output-dir <输出目录> --run-id <运行ID>
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIXED_BIDDER = "四川省科智汇川人工智能科技有限责任公司"

RISK_LEVEL_BY_CODE = {
    "SYMBOL_GAP": "FATAL",
    "SUBSTANTIVE_AMBIGUOUS": "FATAL",
    "GATE_MATERIAL_MISSING": "FATAL",
    "FORMAT_SOURCE_CONFLICT": "HIGH",
    "COUNT_MISMATCH": "HIGH",
    "EMPTY_MATERIAL_LIBRARY": "HIGH",
    "STAR_SCOPE": "MEDIUM",
    "PROFILE_MISSING": "HIGH",
    "MATERIALS_DIR_MISSING": "MEDIUM",
    "DEMO_RISK": "MEDIUM",
}

SEVERITY_WEIGHT = {"★": 100.0, "█": 50.0, "▲": 15.0, "●": 5.0, None: 1.0}


def parse_date(s):
    if not isinstance(s, str):
        return None
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def build_score_plan(data, checklist):
    reqs = data.get("requirements", [])
    pool = data.get("scoring_pool") or {}
    items = pool.get("items") or []

    status_by_req = {}
    if checklist:
        for it in checklist.get("data", {}).get("items", []):
            for rid in it.get("matched_requirement_ids", []):
                status_by_req[rid] = it.get("status")

    groups = {}
    for sym_item in items:
        sym = sym_item.get("symbol")
        per = sym_item.get("deduction_per_item", 0) or 0
        bucket = groups.setdefault(sym, {"symbol": sym, "count": 0, "per": per,
                                         "subtotal": 0.0, "reqs": []})
        bucket["count"] += sym_item.get("count", 0)
        bucket["subtotal"] += sym_item.get("count", 0) * per

    for r in reqs:
        sym = r.get("severity_symbol")
        if sym and sym in groups:
            groups[sym]["reqs"].append(r["requirement_id"])
        elif sym is None and r.get("deduction_per_item") is not None and "（无符号）" in groups:
            groups["（无符号）"]["reqs"].append(r["requirement_id"])

    plan = []
    for sym, g in groups.items():
        gap = [rid for rid in g["reqs"] if status_by_req.get(rid) in ("MISSING", "EXPIRED")]
        basis = (f"{g['count']} 条 × 单条扣 {g['per']} 分 = {g['subtotal']} 分"
                 + (f"；其中 {len(gap)} 条资料缺失" if gap else ""))
        plan.append({
            "priority": 0,
            "target": f"{sym} 类技术指标（{g['count']} 条，共 {g['subtotal']} 分）",
            "requirement_ids": g["reqs"],
            "potential_gain": round(g["subtotal"], 2),
            "basis": basis,
        })

    # 非符号化的评分项（如资格、业绩加分）。若某条目的分值等于整个分值池总额，
    # 说明它就是该池子的总述条目，已被符号分组覆盖，单列会导致分值重复计算。
    declared_total = pool.get("declared_total")
    other = [r for r in reqs if not r.get("severity_symbol")
             and r.get("category") == "SCORING"
             and not (declared_total is not None and r.get("score") == declared_total)]
    for r in other:
        plan.append({
            "priority": 0,
            "target": f"评分项 {r['requirement_id']}：{r['text'][:40]}",
            "requirement_ids": [r["requirement_id"]],
            "potential_gain": r.get("score"),
            "basis": "评标办法单独列示的评分项" + (f"（{r['score']} 分）" if r.get("score") is not None else ""),
        })

    plan.sort(key=lambda p: (-(p["potential_gain"] or 0), p["target"]))
    for i, p in enumerate(plan, 1):
        p["priority"] = i
    return plan


def build_risks(data, issues, checklist):
    risks = []
    n = 0
    for it in issues:
        n += 1
        code = it.get("code", "UNKNOWN")
        risks.append({
            "risk_id": f"RISK-{n:03d}",
            "level": RISK_LEVEL_BY_CODE.get(code, "MEDIUM"),
            "description": f"[{code}] {it.get('message', '')}",
            "source_ref": it.get("source_ref", ""),
            "mitigation": it.get("action", ""),
            "owner": it.get("owner", "投标负责人"),
        })

    # 技术标得分风险：分值最高但资料缺失的类别
    if checklist:
        sc = checklist.get("data", {}).get("summary", {})
        if sc.get("gate_missing"):
            n += 1
            risks.append({
                "risk_id": f"RISK-{n:03d}",
                "level": "FATAL",
                "description": f"{sc['gate_missing']} 条准入级要求的材料缺失或过期",
                "source_ref": "material_checklist.json → data.summary.gate_missing",
                "mitigation": "按《资料缺口与补齐清单.md》逐条补齐；确认无法补齐的，需在投标决策阶段明确评估放弃风险",
                "owner": "投标负责人",
            })

    # 总分与承诺风险
    pool = data.get("scoring_pool") or {}
    if pool and pool.get("closed") is False:
        n += 1
        risks.append({
            "risk_id": f"RISK-{n:03d}",
            "level": "HIGH",
            "description": "分值池未闭合：声明总分与按条反推不一致，说明符号识别或扣分标准读取存在偏差",
            "source_ref": "tender_brief.json → data.scoring_pool",
            "mitigation": "回到评标办法原文逐条复核扣分标准与条数，修正后重跑 validate.py",
            "owner": "投标负责人",
        })
    return risks


def build_clarifications(issues, data):
    out = []
    for it in issues:
        if it.get("code") in ("SYMBOL_GAP", "COUNT_MISMATCH", "FORMAT_SOURCE_CONFLICT",
                              "SUBSTANTIVE_AMBIGUOUS"):
            out.append({
                "question": f"关于 {it.get('source_ref', '')}：{it.get('message', '')}",
                "source_ref": it.get("source_ref", ""),
                "deadline_hint": data.get("project", {}).get("deadline"),
                "status": "DRAFTED",
            })
    return out


def build_sections(data, checklist):
    reqs = data.get("requirements", [])
    buckets = {
        "QUALIFICATION": ("资格证明文件", []),
        "SCORING": ("技术响应与得分方案", []),
        "DELIVERY": ("服务实施方案", []),
    }
    fmt_reqs = []
    for r in reqs:
        cat = r.get("category")
        if cat in buckets:
            buckets[cat][1].append(r["requirement_id"])
        elif cat == "FORMAT":
            fmt_reqs.append(r["requirement_id"])

    # 报价类要求单独成章，不留在技术响应章内
    price_ids = [r["requirement_id"] for r in reqs
                 if r.get("category") == "SCORING" and "报价" in r.get("text", "")]
    buckets["SCORING"][1][:] = [i for i in buckets["SCORING"][1] if i not in price_ids]

    plan = [{"section_id": "SEC-001", "title": "投标函与投标函附录",
             "source_requirement_ids": fmt_reqs[:1] or [],
             "owner": "投标负责人", "status": "PLANNED"}]

    sid = 2
    for cat, (title, ids) in buckets.items():
        if not ids:
            continue
        plan.append({
            "section_id": f"SEC-{sid:03d}",
            "title": title,
            "source_requirement_ids": ids,
            "owner": "投标负责人",
            "status": "PLANNED",
        })
        sid += 1

    if fmt_reqs:
        plan.append({
            "section_id": f"SEC-{sid:03d}",
            "title": "投标文件编制规范自查（格式与签署要求）",
            "source_requirement_ids": fmt_reqs,
            "owner": "标书编制人",
            "status": "PLANNED",
        })
        sid += 1

    if price_ids:
        plan.append({
            "section_id": f"SEC-{sid:03d}",
            "title": "报价文件",
            "source_requirement_ids": price_ids,
            "owner": "商务负责人",
            "status": "PLANNED",
        })
        sid += 1
    return plan, fmt_reqs


def build_schedule(data):
    deadline = parse_date((data.get("project") or {}).get("deadline"))
    milestones = [
        ("资格材料齐备（营业执照、资质、财务、人员）", 10),
        ("技术方案初稿完成", 7),
        ("得分项逐条自查（对照 scoring_pool）", 5),
        ("全文校核：格式、签字、盖章、份数", 3),
        ("定稿打印、装订、密封、递交", 1),
    ]
    out = []
    if deadline:
        for name, back in milestones:
            d = deadline - timedelta(days=back)
            out.append({"milestone": name, "due_date": d.isoformat(),
                        "owner": "投标负责人", "basis": f"投标截止 {deadline.isoformat()} 前 {back} 天"})
        out.append({"milestone": "投标截止", "due_date": deadline.isoformat(),
                    "owner": "投标负责人", "basis": "招标文件载明截止时间"})
    else:
        for name, back in milestones:
            out.append({"milestone": name, "due_date": None,
                        "owner": "投标负责人", "basis": f"投标截止前 {back} 天（截止时间待确认）"})
    return out, deadline


def main():
    ap = argparse.ArgumentParser(description="S3 投标方案建议")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--checklist", default=None)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    bp = Path(args.brief).resolve()
    brief = json.loads(bp.read_text(encoding="utf-8"))
    data = brief.get("data", {})

    checklist = None
    cl_path = None
    if args.checklist:
        cl_path = Path(args.checklist).resolve()
        if cl_path.exists():
            checklist = json.loads(cl_path.read_text(encoding="utf-8"))

    issues = list(brief.get("issues", []))
    if checklist:
        issues += checklist.get("issues", [])

    score_plan = build_score_plan(data, checklist)
    risks = build_risks(data, issues, checklist)
    clar = build_clarifications(issues, data)
    sections, fmt_reqs = build_sections(data, checklist)
    schedule, deadline = build_schedule(data)

    total_pool = (data.get("scoring_pool") or {}).get("declared_total")
    gate_missing = (checklist or {}).get("data", {}).get("summary", {}).get("gate_missing", 0)

    if gate_missing:
        plan_status = "BLOCKED" if gate_missing and not (checklist or {}).get("data", {}).get("summary", {}).get("covered") else "PARTIAL"
    elif data.get("unread_sections"):
        plan_status = "PARTIAL"
    else:
        plan_status = "DRAFT_READY"

    out_issues = []
    if not deadline:
        out_issues.append({
            "code": "DEADLINE_UNPARSED",
            "message": "未能从招标文件中解析出投标截止时间，排期里程碑暂无具体日期。",
            "source_ref": "data.project.deadline",
            "owner": "投标负责人",
            "action": "人工确认投标截止时间后填入 tender_brief.json 并重跑本步骤",
        })
    if not checklist:
        out_issues.append({
            "code": "CHECKLIST_ABSENT",
            "message": "未提供 S2 输出，本方案未纳入资料缺口维度，得分规划仅基于招标文件。",
            "source_ref": "--checklist 未传入",
            "owner": "投标负责人",
            "action": "先运行 S2 生成 material_checklist.json，再用 --checklist 重跑 S3",
        })

    doc = {
        "schema_version": "kzhc.bid-ops.v1",
        "module": "S3",
        "run_id": args.run_id,
        "project_id": brief.get("project_id"),
        "bidder_name": FIXED_BIDDER,
        "source_mode": "REAL_INPUT",
        "input_refs": [{
            "source_id": "SRC-BRIEF", "file_name": bp.name, "relative_path": bp.name,
            "kind": "tender_brief", "origin": "S1 output",
            "sha256": hashlib.sha256(bp.read_bytes()).hexdigest(), "version": "kzhc.bid-ops.v1",
        }] + ([{
            "source_id": "SRC-CHECKLIST", "file_name": cl_path.name, "relative_path": cl_path.name,
            "kind": "material_checklist", "origin": "S2 output",
            "sha256": hashlib.sha256(cl_path.read_bytes()).hexdigest(), "version": "kzhc.bid-ops.v1",
        }] if checklist else []),
        "processing_status": "REVIEW" if (issues or out_issues) else "COMPLETE",
        "issues": issues + out_issues,
        "data": {
            "score_plan": score_plan,
            "risk_items": risks,
            "clarification_items": clar,
            "section_plan": sections,
            "schedule": schedule,
            "plan_status": plan_status,
            "context": {
                "declared_total_pool": total_pool,
                "gate_missing": gate_missing,
                "deadline": deadline.isoformat() if deadline else None,
                "requirement_count": len(data.get("requirements", [])),
            },
        },
        "next_actions": [
            {"action": "就 clarification_items 中的问题向采购人/代理机构书面澄清（状态置 SENT 需人工操作）", "owner": "投标负责人"},
            {"action": "按 score_plan 优先级组织技术方案攻关顺序", "owner": "技术负责人"},
            {"action": "补齐资料缺口后重跑 S2 与 S3，直至 plan_status 达到 DRAFT_READY", "owner": "投标负责人"},
        ],
    }

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bid_plan.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "投标方案建议书.md").write_text(render_md(doc, brief, checklist), encoding="utf-8")

    print("=== S3 投标方案建议生成完成 ===")
    print(f"  得分规划条目   : {len(score_plan)}")
    print(f"  风险条目       : {len(risks)}（FATAL {sum(1 for r in risks if r['level'] == 'FATAL')}）")
    print(f"  澄清清单       : {len(clar)}")
    print(f"  章节规划       : {len(sections)}")
    print(f"  排期里程碑     : {len(schedule)}（截止时间 {'已解析' if deadline else '待确认'}）")
    print(f"  方案状态       : {plan_status}")
    print(f"  → {out_dir / 'bid_plan.json'}")
    print(f"  → {out_dir / '投标方案建议书.md'}")
    return 0


def render_md(doc, brief, checklist):
    d = doc["data"]
    p = brief.get("data", {}).get("project", {})
    ctx = d["context"]
    L = []
    L.append("# 投标方案建议书\n")
    L.append(f"> 本文件为辅助建议，**不构成投标决策，不得直接作为标书正文**。")
    L.append(f"> 所有\"承诺\"类内容须经人工逐条核对后方可使用。\n")
    L.append("## 一、项目概况\n")
    L.append(f"| 项 | 内容 |")
    L.append("| --- | --- |")
    L.append(f"| 项目名称 | {p.get('project_name', '—')} |")
    L.append(f"| 项目编号 | {doc['project_id']} |")
    L.append(f"| 采购人 | {p.get('tenderer_name') or '—'} |")
    L.append(f"| 代理机构 | {p.get('agency_name') or '—'} |")
    L.append(f"| 采购方式 | {p.get('procurement_type') or '—'} |")
    L.append(f"| 预算 | {p.get('budget') or '—'} |")
    L.append(f"| 投标截止 | {ctx.get('deadline') or '待确认'} |")
    L.append(f"| 投标主体 | {doc['bidder_name']} |")
    L.append(f"| 要求条数 | {ctx.get('requirement_count')} |")
    L.append(f"| 技术指标分值池 | {ctx.get('declared_total_pool')} 分 |")
    L.append(f"| ★ 准入级材料缺失 | {ctx.get('gate_missing')} |\n")

    L.append("## 二、得分规划（按潜在收益排序）\n")
    L.append("| 优先级 | 攻关目标 | 潜在分值 | 依据 | 关联要求 |")
    L.append("| --- | --- | --- | --- | --- |")
    for s in d["score_plan"]:
        ids = "、".join(s["requirement_ids"][:6]) + ("…" if len(s["requirement_ids"]) > 6 else "")
        gain = f"{s['potential_gain']}" if s.get("potential_gain") is not None else "—"
        L.append(f"| {s['priority']} | {s['target']} | {gain} | {s['basis']} | {ids or '—'} |")
    L.append("")

    L.append("## 三、风险清单\n")
    if not d["risk_items"]:
        L.append("_无_\n")
    else:
        L.append("| 编号 | 等级 | 风险 | 来源 | 处置 | 责任人 |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        order = {"FATAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        for r in sorted(d["risk_items"], key=lambda x: order.get(x["level"], 9)):
            L.append(f"| {r['risk_id']} | {r['level']} | {r['description']} | {r['source_ref']} | {r['mitigation']} | {r['owner']} |")
        L.append("")

    L.append("## 四、澄清清单（拟提交采购人/代理机构）\n")
    if not d["clarification_items"]:
        L.append("_无需澄清事项_\n")
    else:
        L.append("| # | 澄清问题 | 来源 | 状态 |")
        L.append("| --- | --- | --- | --- |")
        for i, c in enumerate(d["clarification_items"], 1):
            L.append(f"| {i} | {c['question']} | {c['source_ref']} | {c['status']} |")
        L.append("")
        L.append("> 澄清函须由本公司具名发出并加盖公章；本清单仅提供问题草稿。\n")

    L.append("## 五、响应章节规划\n")
    L.append("| 章节 | 标题 | 关联要求数 | 责任人 | 状态 |")
    L.append("| --- | --- | --- | --- | --- |")
    for s in d["section_plan"]:
        L.append(f"| {s['section_id']} | {s['title']} | {len(s['source_requirement_ids'])} | {s.get('owner') or '—'} | {s['status']} |")
    L.append("")

    L.append("## 六、时间排期\n")
    L.append("| 里程碑 | 截止日期 | 说明 |")
    L.append("| --- | --- | --- |")
    for s in d["schedule"]:
        L.append(f"| {s['milestone']} | {s.get('due_date') or '待确认'} | {s.get('basis', '')} |")
    L.append("")

    L.append("## 七、资料维度\n")
    if checklist:
        sc = checklist["data"]["summary"]
        L.append(f"资料库文件数 {len(checklist['data']['material_index'])}，"
                 f"覆盖 {sc['covered']} / 部分 {sc['partial']} / 缺失 {sc['missing']} / "
                 f"过期 {sc['expired']} / 待确认 {sc['review']}。详见《资料缺口与补齐清单.md》。\n")
    else:
        L.append("_本次未接入 S2 资料核对结果。_\n")

    L.append("## 八、待确认事项\n")
    if not doc["issues"]:
        L.append("_无_\n")
    else:
        for it in doc["issues"]:
            L.append(f"- **{it['code']}**（{it['owner']}）：{it['message']}\n  - 处理：{it['action']}")
        L.append("")

    L.append("---\n")
    L.append(f"方案状态：**{d['plan_status']}**　运行编号：{doc['run_id']}")
    L.append("\n> 状态含义：`DRAFT_READY` 可进入正式编制 / `PARTIAL` 存在未覆盖范围 / `BLOCKED` 存在准入级缺口。")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
