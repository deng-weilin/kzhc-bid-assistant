#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_materials.py — S2 整理投标资料（确定性封装器）

把 S1 的要求清单与本公司资料库逐条比对，输出「有什么 / 缺什么 / 谁去补」。

核心安全规则（零幻觉）:
  1. 只有 company_profile.yaml 中显式填报且带 evidence 的字段，才可能判为 COVERED。
  2. 资料目录里仅「文件名疑似命中」的材料，一律 REVIEW（提示人工确认后登记），
     绝不凭文件名断言资质存在。
  3. 资料库为空时，输出必须 100% 为缺失。

用法:
    python check_materials.py --brief <tender_brief.json> \
        --materials-dir <资料库目录> --profile <company_profile.yaml> \
        --output-dir <输出目录> --run-id <运行ID>
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIXED_BIDDER = "四川省科智汇川人工智能科技有限责任公司"

# --------------------------------------------------------------------------
# 极简 YAML 解析（只覆盖本 skill 模板用到的子集）
# --------------------------------------------------------------------------

def _strip_comment(line):
    out, q = [], None
    for ch in line:
        if q:
            out.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _split_commas(s):
    out, buf, q = [], [], None
    for ch in s:
        if q:
            buf.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def _scalar(s):
    s = s.strip()
    if s in ("null", "~", ""):
        return None
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [] if not inner else [_scalar(x) for x in _split_commas(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        d = {}
        for part in _split_commas(inner):
            k, _, v = part.partition(":")
            d[k.strip()] = _scalar(v)
        return d
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return s


def _parse_block(lines, i, indent):
    is_list = lines[i][1].startswith("- ")
    container = [] if is_list else {}
    while i < len(lines):
        ind, content = lines[i]
        if ind < indent:
            break
        if ind > indent:
            break
        if is_list:
            if not content.startswith("- "):
                break
            item_text = content[2:].strip()
            if ":" in item_text and not item_text.startswith(("\"", "'")):
                sub = [(indent + 2, item_text)]
                j = i + 1
                while j < len(lines) and lines[j][0] > indent:
                    sub.append(lines[j])
                    j += 1
                val, _ = _parse_block(sub, 0, indent + 2)
                container.append(val)
                i = j
            else:
                container.append(_scalar(item_text))
                i += 1
        else:
            if content.startswith("- "):
                break
            key, _, val = content.partition(":")
            key, val = key.strip(), val.strip()
            i += 1
            if val == "":
                sub, j = [], i
                while j < len(lines) and lines[j][0] > indent:
                    sub.append(lines[j])
                    j += 1
                if sub:
                    child, _ = _parse_block(sub, 0, sub[0][0])
                    container[key] = child
                    i = j
                else:
                    container[key] = None
            else:
                container[key] = _scalar(val)
    return container, i


def load_profile(path):
    if path is None:
        return {}, None
    p = Path(path)
    if not p.exists():
        return {}, f"company_profile.yaml 不存在：{p}"
    raw = p.read_text(encoding="utf-8")
    lines = []
    for ln in raw.split("\n"):
        s = _strip_comment(ln)
        if not s.strip():
            continue
        lines.append((len(s) - len(s.lstrip(" ")), s.strip()))
    if not lines:
        return {}, "company_profile.yaml 为空"
    data, _ = _parse_block(lines, 0, lines[0][0])
    return (data or {}), None


# --------------------------------------------------------------------------
# 资料类型识别
# --------------------------------------------------------------------------

MATERIAL_TYPES = {
    "IDENTITY_LICENSE": "主体证照（营业执照/统一社会信用代码）",
    "QUALIFICATION_CERT": "资质认证证书",
    "FINANCIAL_AUDIT": "财务与审计材料",
    "PERFORMANCE": "业绩与类似项目证明",
    "PERSONNEL": "人员与执业资格材料",
    "IP_ASSET": "知识产权与科研成果",
    "COMMITMENT": "承诺函/声明/授权书",
    "CAPABILITY": "产品与平台能力材料",
    "DEMO": "现场演示/答辩准备",
    "SAMPLE": "样品/样片/样例",
    "FORMAT_RULE": "格式与装订规范",
    "OTHER": "未归类的其他材料",
}

KEYWORD_RULES = [
    ("IDENTITY_LICENSE", ["营业执照", "统一社会信用代码", "法定代表人", "注册地址", "注册资本", "经营范围", "开户许可"]),
    ("QUALIFICATION_CERT", ["等级保护", "等保", "认证证书", "资质证书", "管理体系", "ISO", "备案证明", "信息安全"]),
    ("FINANCIAL_AUDIT", ["审计报告", "财务", "资产负债", "营业收入", "净利润", "纳税", "社保", "验资"]),
    ("PERFORMANCE", ["业绩", "案例", "类似项目", "同类项目", "中标通知", "履约", "合同复印件", "成果案例"]),
    ("PERSONNEL", ["项目经理", "项目负责人", "职称", "执业资格", "人员配置", "团队", "资格证", "学历"]),
    ("IP_ASSET", ["软件著作权", "著作权证书", "专利", "知识产权", "科研成果", "论文"]),
    ("COMMITMENT", ["承诺函", "承诺书", "声明函", "授权委托", "保密协议"]),
    ("DEMO", ["现场演示", "逐一演示", "演示", "答辩", "述标"]),
    ("SAMPLE", ["样品", "样片", "样例", "样章", "试看", "样课"]),
    ("CAPABILITY", ["平台", "系统功能", "功能截图", "课程资源", "开发能力", "运行环境", "虚拟仿真"]),
    ("FORMAT_RULE", ["装订", "密封", "签署", "签字", "盖章", "电子签章", "页码", "封面", "目录", "份数", "正本", "副本", "格式"]),
]

# 资料目录里的文件名关键词（仅作线索，命中只给 REVIEW）
FILE_HINTS = {
    "IDENTITY_LICENSE": ["营业执照", "执照", "统一社会信用"],
    "QUALIFICATION_CERT": ["认证", "资质", "等保", "备案", "证书", "管理体系"],
    "FINANCIAL_AUDIT": ["审计", "财务", "报表", "纳税", "社保"],
    "PERFORMANCE": ["业绩", "案例", "合同", "中标", "履约"],
    "PERSONNEL": ["人员", "证书", "职称", "社保", "简历"],
    "IP_ASSET": ["著作权", "专利", "软著", "知识产权"],
    "COMMITMENT": ["承诺", "声明", "授权"],
    "DEMO": ["演示", "答辩"],
    "SAMPLE": ["样品", "样片", "样例", "样课"],
    "CAPABILITY": ["产品", "平台", "功能", "介绍", "方案"],
}


def classify(req):
    cat = req.get("category")
    text = req.get("text", "")
    if cat == "FORMAT":
        return "FORMAT_RULE"
    for mtype, kws in KEYWORD_RULES:
        if any(k in text for k in kws):
            return mtype
    return {"QUALIFICATION": "QUALIFICATION_CERT",
            "SCORING": "CAPABILITY",
            "DELIVERY": "CAPABILITY"}.get(cat, "OTHER")


# --------------------------------------------------------------------------
# 存在性判定
# --------------------------------------------------------------------------

def _today():
    return date.today()


def count_filled(node):
    """递归统计 profile 中已填写的标量字段数（用于区分「文件已解析」与「内容已填报」）。"""
    if isinstance(node, dict):
        return sum(count_filled(v) for k, v in node.items() if k != "schema_version")
    if isinstance(node, list):
        return sum(count_filled(v) for v in node)
    return 0 if node in (None, "", [], {}) else 1


def _parse_date(s):
    if not isinstance(s, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _has_evidence(entry, *keys):
    if not isinstance(entry, dict):
        return False
    vals = [entry.get(k) for k in keys]
    filled = any(v not in (None, "", [], {}) for v in vals)
    ev = entry.get("evidence")
    return filled and bool(ev)


def eval_type(mtype, profile, file_names):
    """返回 (status, matched_refs, reason, gap_detail)"""
    ident = profile.get("identity") or {}
    quals = profile.get("qualifications") or []
    fins = profile.get("financials") or {}
    perf = profile.get("performance") or []
    pers = profile.get("personnel") or []
    ips = profile.get("ip_assets") or []
    caps = profile.get("capability") or []
    hint_files = [f for f in file_names
                  if any(k in f for k in FILE_HINTS.get(mtype, []))]

    if mtype == "FORMAT_RULE":
        return ("REVIEW", [], "格式与装订规范类要求，不消耗公司资质资料",
                "由标书编制环节按招标格式要求落实；编制完成后逐项自查")

    if mtype == "IDENTITY_LICENSE":
        keys = ["unified_social_credit_code", "legal_representative",
                "registered_address", "registered_capital", "business_scope"]
        if _has_evidence(ident, *keys) and ident.get("evidence"):
            return ("COVERED", ["profile.identity"], "profile 已填报主体信息并附证明材料", None)
        if hint_files:
            return ("REVIEW", hint_files, "资料目录存在疑似主体证照文件，profile 未登记",
                    "核对该文件并登记到 profile.identity（含营业执照名称逐字校对）")
        return ("MISSING", [], "profile.identity 未填报或未附证明材料",
                "上传营业执照扫描件，逐字抄录统一社会信用代码、法定代表人、注册地址、注册资本")

    if mtype == "QUALIFICATION_CERT":
        fresh = [q for q in quals if isinstance(q, dict) and q.get("evidence")]
        if fresh:
            expired = []
            for q in fresh:
                d = _parse_date(q.get("valid_until"))
                if d and d < _today():
                    expired.append(q.get("name") or "未命名证书")
            if expired:
                return ("EXPIRED", [f"profile.qualifications:{n}" for n in expired],
                        f"证书已过期：{'、'.join(expired)}", "补办或换发证书后再用于投标")
            return ("COVERED", ["profile.qualifications"], f"已登记 {len(fresh)} 项资质证书", None)
        if hint_files:
            return ("REVIEW", hint_files, "资料目录存在疑似资质文件，profile 未登记",
                    "核对证书名称/编号/发证机关/有效期后登记到 profile.qualifications")
        return ("MISSING", [], "profile.qualifications 为空",
                "梳理本公司全部资质证书，逐项登记名称、编号、发证机关、有效期")

    if mtype == "FINANCIAL_AUDIT":
        if fins.get("audit_report_available") is True and fins.get("evidence"):
            return ("COVERED", ["profile.financials"], "已提供审计报告证明材料", None)
        if hint_files:
            return ("REVIEW", hint_files, "资料目录存在疑似财务材料，profile 未登记",
                    "核对报告年度与指标后登记到 profile.financials")
        return ("MISSING", [], "profile.financials 未填报或未附审计报告",
                "准备近三年财务审计报告或财务报表，登记年度与关键指标")

    if mtype == "PERFORMANCE":
        ok = [x for x in perf if isinstance(x, dict) and _has_evidence(x, "project_name", "client_name") ]
        if ok:
            return ("COVERED", ["profile.performance"], f"已登记 {len(ok)} 项业绩（含证明材料）", None)
        filled = [x for x in perf if isinstance(x, dict) and any(
            x.get(k) not in (None, "", [], {})
            for k in ("project_name", "client_name", "contract_amount", "period"))]
        if filled:
            return ("PARTIAL", ["profile.performance"],
                    f"{len(filled)} 项业绩条目缺少项目名、甲方或证明材料",
                    "补齐合同/中标通知书扫描件，核对甲方名称与业绩表一致")
        if hint_files:
            return ("REVIEW", hint_files, "资料目录存在疑似业绩材料，profile 未登记",
                    "按项目逐条登记名称、甲方、金额、期间，并关联证明材料")
        return ("MISSING", [], "profile.performance 为空",
                "整理近三年类似项目业绩，逐项登记并附合同或中标通知书")

    if mtype == "PERSONNEL":
        ok = [x for x in pers if isinstance(x, dict) and x.get("name")]
        if ok:
            return ("COVERED", ["profile.personnel"], f"已登记 {len(ok)} 名拟投入人员", None)
        if hint_files:
            return ("REVIEW", hint_files, "资料目录存在疑似人员材料，profile 未登记",
                    "登记人员姓名、拟任角色、职称、执业资格与社保情况")
        return ("MISSING", [], "profile.personnel 为空",
                "确定拟投入人员名单，登记姓名/角色/职称/证书编号/社保月数")

    if mtype == "IP_ASSET":
        ok = [x for x in ips if isinstance(x, dict) and x.get("name")]
        if ok:
            return ("COVERED", ["profile.ip_assets"], f"已登记 {len(ok)} 项知识产权", None)
        if hint_files:
            return ("REVIEW", hint_files, "资料目录存在疑似软著/专利材料，profile 未登记",
                    "登记证书名称、编号与登记日期，核对与招标主题的关联性")
        return ("MISSING", [], "profile.ip_assets 为空",
                "整理软件著作权、专利等知识产权清单及证书扫描件")

    if mtype == "COMMITMENT":
        if hint_files:
            return ("REVIEW", hint_files, "存在承诺函类文件，需按招标格式逐项核对",
                    "对照招标要求的承诺事项逐条核对，缺项需补写并加盖公章")
        return ("MISSING", [], "资料目录无承诺函类文件",
                "按招标文件格式要求起草承诺函（格式类材料通常可自制），需法定代表人签署并盖章")

    if mtype == "CAPABILITY":
        ok = [x for x in caps if isinstance(x, dict) and x.get("item")]
        if ok:
            return ("COVERED", ["profile.capability"], f"已登记 {len(ok)} 项能力条目", None)
        if hint_files:
            return ("REVIEW", hint_files, "资料目录存在疑似产品/能力材料，profile 未登记",
                    "登记产品功能、平台能力，并标注可否现场演示")
        return ("MISSING", [], "profile.capability 为空",
                "梳理可响应本项目技术要求的平台功能与案例，准备功能截图或演示环境")

    if mtype == "DEMO":
        ready = [x for x in caps if isinstance(x, dict) and x.get("demo_ready") is True]
        if ready:
            return ("COVERED", ["profile.capability:demo_ready"], f"{len(ready)} 项可现场演示", None)
        return ("MISSING", [], "无可现场演示的能力项登记",
                "确定演示环境与演示脚本，预留演练与设备调试时间")

    if mtype == "SAMPLE":
        if hint_files:
            return ("REVIEW", hint_files, "存在疑似样例文件，需人工确认是否满足数量与规格要求",
                    "核对样例的数量语义（下限/区间/精确值）与规格要求")
        return ("MISSING", [], "资料目录无样品/样例文件",
                "按招标数量要求准备样例并核对数量语义")

    if hint_files:
        return ("REVIEW", hint_files, "资料目录存在疑似相关文件，待人工归类", "人工确认后归类登记")
    return ("MISSING", [], "未识别到对应资料", "人工判断该要求所需材料并补入资料库")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def scan_materials(root):
    if root is None:
        return [], None
    p = Path(root)
    if not p.exists() or not p.is_dir():
        return [], f"资料目录不存在：{p}"
    idx = []
    for i, f in enumerate(sorted(p.rglob("*")), 1):
        if not f.is_file() or f.name.startswith("."):
            continue
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        idx.append({
            "material_id": f"MAT-{i:03d}",
            "file_name": f.name,
            "relative_path": str(f.relative_to(p)).replace("\\", "/"),
            "suffix": f.suffix.lower().lstrip("."),
            "size_bytes": f.stat().st_size,
            "sha256": h.hexdigest(),
        })
    return idx, None


def main():
    ap = argparse.ArgumentParser(description="S2 投标资料核对")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--materials-dir", default=None)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    brief_path = Path(args.brief).resolve()
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    data1 = brief.get("data", {})
    reqs = data1.get("requirements", [])
    project_id = brief.get("project_id")

    profile, profile_warn = load_profile(args.profile)
    materials, mat_warn = scan_materials(args.materials_dir)
    file_names = [m["file_name"] for m in materials]

    issues, next_actions = [], []
    if profile_warn:
        issues.append({"code": "PROFILE_MISSING", "message": profile_warn,
                       "source_ref": str(args.profile), "owner": "投标负责人",
                       "action": "从 templates/company_profile.template.yaml 复制并如实填报"})
    if mat_warn:
        issues.append({"code": "MATERIALS_DIR_MISSING", "message": mat_warn,
                       "source_ref": str(args.materials_dir), "owner": "投标负责人",
                       "action": "建立资料库目录并放入本公司真实材料"})

    items, per_req_types = [], {}
    for req in reqs:
        rid = req.get("requirement_id")
        mtype = classify(req)
        per_req_types[rid] = mtype
        status, refs, reason, gap = eval_type(mtype, profile, file_names)
        sev = "GATE" if req.get("mandatory") or req.get("severity_symbol") == "★" else (
            "SCORING" if req.get("category") == "SCORING" or req.get("severity_symbol") else "NORMAL")
        items.append({
            "material_id": f"MATREQ-{rid.split('-')[-1]}",
            "material_name": MATERIAL_TYPES.get(mtype, mtype),
            "material_type": mtype,
            "matched_requirement_ids": [rid],
            "matched_asset_refs": refs,
            "status": status,
            "severity": sev,
            "reason": reason,
            "gap_detail": gap,
            "owner": None if status == "COVERED" else "投标负责人",
            "reviewer": None,
        })

    c = Counter(i["status"] for i in items)
    gate_missing = sum(1 for i in items if i["severity"] == "GATE" and i["status"] in ("MISSING", "EXPIRED"))
    summary = {
        "total_requirements": len(items),
        "covered": c.get("COVERED", 0),
        "partial": c.get("PARTIAL", 0),
        "missing": c.get("MISSING", 0),
        "expired": c.get("EXPIRED", 0),
        "review": c.get("REVIEW", 0),
        "gate_missing": gate_missing,
    }

    if gate_missing:
        issues.append({
            "code": "GATE_MATERIAL_MISSING",
            "message": f"{gate_missing} 条准入级要求（mandatory / ★）对应的资料缺失或过期，按现状投标存在废标风险。",
            "source_ref": "data.items（severity=GATE 且 status∈{MISSING,EXPIRED}）",
            "owner": "投标负责人",
            "action": "逐条补齐准入级材料或评估放弃投标；不得以其他材料类比替代。",
        })
    if not materials:
        issues.append({
            "code": "EMPTY_MATERIAL_LIBRARY",
            "message": "资料库为空，本次核对结果为阴性对照（全部缺失），不代表本公司真实缺口。",
            "source_ref": str(args.materials_dir),
            "owner": "投标负责人",
            "action": "放入真实材料后重跑本步骤。",
        })
    if profile and count_filled(profile) <= 1:
        issues.append({
            "code": "PROFILE_NOT_FILLED",
            "message": "company_profile.yaml 已解析但几乎无有效内容（仅主体名称），所有依赖公司资质的条目均判为缺失。",
            "source_ref": str(args.profile),
            "owner": "投标负责人",
            "action": "按 references/company-profile-guide.md 逐项如实填报，并附证明材料文件名。",
        })

    status = "REVIEW" if issues else "COMPLETE"
    doc = {
        "schema_version": "kzhc.bid-ops.v1",
        "module": "S2",
        "run_id": args.run_id,
        "project_id": project_id,
        "bidder_name": FIXED_BIDDER,
        "source_mode": "REAL_INPUT",
        "input_refs": [{
            "source_id": "SRC-BRIEF",
            "file_name": brief_path.name,
            "relative_path": brief_path.name,
            "kind": "tender_brief",
            "origin": "S1 output",
            "sha256": hashlib.sha256(brief_path.read_bytes()).hexdigest(),
            "version": "kzhc.bid-ops.v1",
        }],
        "processing_status": status,
        "issues": issues,
        "data": {
            "profile_ref": {
                "path": str(args.profile) if args.profile else None,
                "loaded": bool(profile),
                "filled_field_count": count_filled(profile),
                "identity_company_name": ((profile.get("identity") or {}).get("company_name")),
                "filled_by": profile.get("filled_by"),
            },
            "materials_scanned": [{"path": str(args.materials_dir), "file_count": len(materials)}] if args.materials_dir else [],
            "material_index": materials,
            "items": items,
            "summary": summary,
            "confirmation": {"confirmed_by": None, "confirmed_at": None},
        },
        "next_actions": [
            {"action": "按 资料缺口与补齐清单.md 的顺序补齐材料", "owner": "投标负责人"},
            {"action": "补齐后重跑 check_materials.py，直至 gate_missing 为 0 或明确放弃投标", "owner": "投标负责人"},
        ],
    }

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "material_checklist.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "资料缺口与补齐清单.md").write_text(
        render_md(doc, per_req_types, reqs), encoding="utf-8")

    print("=== S2 资料核对完成 ===")
    print(f"  资料库文件数       : {len(materials)}")
    print(f"  profile 是否加载   : {bool(profile)}")
    print(f"  要求条目           : {summary['total_requirements']}")
    print(f"  已覆盖/部分/缺失/过期/待确认: "
          f"{summary['covered']}/{summary['partial']}/{summary['missing']}/{summary['expired']}/{summary['review']}")
    print(f"  ★ 准入级缺失       : {summary['gate_missing']}")
    print(f"  状态               : {status}")
    print(f"  → {out_dir / 'material_checklist.json'}")
    print(f"  → {out_dir / '资料缺口与补齐清单.md'}")
    return 0


def render_md(doc, per_req_types, reqs):
    s = doc["data"]["summary"]
    L = []
    L.append("# 资料缺口与补齐清单\n")
    L.append(f"- 投标主体：{FIXED_BIDDER}")
    L.append(f"- 项目 ID：{doc['project_id']}    运行编号：{doc['run_id']}")
    L.append(f"- 资料库文件数：{len(doc['data']['material_index'])}\n")
    L.append("## 汇总\n")
    L.append("| 指标 | 数量 |")
    L.append("| --- | --- |")
    L.append(f"| 要求条目 | {s['total_requirements']} |")
    L.append(f"| 已覆盖 COVERED | {s['covered']} |")
    L.append(f"| 部分 PARTIAL | {s['partial']} |")
    L.append(f"| 缺失 MISSING | {s['missing']} |")
    L.append(f"| 过期 EXPIRED | {s['expired']} |")
    L.append(f"| 待确认 REVIEW | {s['review']} |")
    L.append(f"| **准入级缺失（★/强制）** | **{s['gate_missing']}** |\n")

    req_by_id = {r["requirement_id"]: r for r in reqs}
    order = {"GATE": 0, "SCORING": 1, "NORMAL": 2}
    ordered = sorted(doc["data"]["items"], key=lambda i: (order.get(i["severity"], 9), i["material_id"]))

    L.append("## 缺口明细（按重要性排序）\n")
    L.append("| 要求ID | 等级 | 所需材料类型 | 状态 | 说明 | 补齐路径 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for i in ordered:
        if i["status"] == "COVERED":
            continue
        L.append(f"| {i['matched_requirement_ids'][0]} | {i['severity']} | "
                 f"{i['material_name']} | {i['status']} | {i['reason']} | {i['gap_detail'] or '-'} |")
    L.append("")

    L.append("## 已覆盖项\n")
    cov = [i for i in ordered if i["status"] == "COVERED"]
    if not cov:
        L.append("_无_（资料库为空或 profile 未填报）\n")
    else:
        L.append("| 要求ID | 材料类型 | 依据 |")
        L.append("| --- | --- | --- |")
        for i in cov:
            L.append(f"| {i['matched_requirement_ids'][0]} | {i['material_name']} | "
                     f"{'、'.join(i['matched_asset_refs']) or '-'} |")
        L.append("")

    L.append("## 待确认事项\n")
    if not doc["issues"]:
        L.append("_无_\n")
    else:
        for it in doc["issues"]:
            L.append(f"- **{it['code']}**（{it['owner']}）：{it['message']}\n  - 处理：{it['action']}")
        L.append("")

    L.append("## 材料目录\n")
    if not doc["data"]["material_index"]:
        L.append("_资料库为空_\n")
    else:
        L.append("| 编号 | 文件名 | 大小 | sha256 |")
        L.append("| --- | --- | --- | --- |")
        for m in doc["data"]["material_index"]:
            L.append(f"| {m['material_id']} | {m['relative_path']} | {m['size_bytes']} | {m['sha256'][:12]}… |")

    L.append("\n---\n")
    L.append("> 本清单仅说明「资料库中是否存在可核对材料」，**不构成资质真伪认定**。")
    L.append("> 确认人一栏留空，须由人工填写。")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
