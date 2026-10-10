#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py — 契约层 + 业务层校验

用法:
    python validate.py --stage S1|S2|S3 --input <json文件或目录> [--expected-project-id <ID>]

契约层: 优先用 jsonschema；不可用时退化到内置 mini 校验器（覆盖本 skill 用到的关键字）。
业务层: R1—R11 规则。

退出码: 0 通过 / 1 存在失败项 / 2 运行错误
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FIXED_BIDDER = "四川省科智汇川人工智能科技有限责任公司"
FILE_BY_STAGE = {"S1": "tender_brief.json", "S2": "material_checklist.json", "S3": "bid_plan.json"}


# --------------------------------------------------------------------------
# 内置 mini 校验器（JSON Schema 子集）
# --------------------------------------------------------------------------

def _is_type(instance, t):
    if t == "object":
        return isinstance(instance, dict)
    if t == "array":
        return isinstance(instance, list)
    if t == "string":
        return isinstance(instance, str)
    if t == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if t == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if t == "boolean":
        return isinstance(instance, bool)
    if t == "null":
        return instance is None
    return True


def _resolve(ref, docs, current_doc):
    if ref.startswith("#"):
        node, frag = current_doc, ref
    else:
        file_part, _, frag = ref.partition("#")
        key = "envelope" if "envelope" in file_part else "module"
        node = docs[key]
        frag = "#" + frag if frag else ""
    for token in [t for t in frag.lstrip("#/").split("/") if t]:
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            return None, node
        node = node[token]
    return node, node if not ref.startswith("#") else current_doc


def mini_validate(instance, schema, docs, current_doc, path="$"):
    errors = []
    if not isinstance(schema, dict):
        return errors

    if "$ref" in schema:
        target, new_doc = _resolve(schema["$ref"], docs, current_doc)
        if target is None:
            return [f"{path}: 无法解析 $ref {schema['$ref']}"]
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        errors += mini_validate(instance, target, docs, new_doc, path)
        if rest:
            errors += mini_validate(instance, rest, docs, current_doc, path)
        return errors

    for sub in schema.get("allOf", []):
        errors += mini_validate(instance, sub, docs, current_doc, path)

    if "oneOf" in schema:
        branches = schema["oneOf"]
        chosen = None
        if isinstance(instance, dict):
            for br in branches:
                const = br.get("properties", {}).get("module", {}).get("const")
                if const is not None and instance.get("module") == const:
                    chosen = br
                    break
        if chosen is None:
            best_err = None
            for br in branches:
                e = mini_validate(instance, br, docs, current_doc, path)
                if best_err is None or len(e) < len(best_err):
                    chosen, best_err = br, e
        errors += mini_validate(instance, chosen, docs, current_doc, path)

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: 期望常量 {schema['const']!r}，实际 {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: 取值 {instance!r} 不在枚举 {schema['enum']} 中")

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            errors.append(f"{path}: 类型应为 {schema['type']}，实际 {type(instance).__name__}")
            return errors

    if isinstance(instance, str) and "pattern" in schema:
        if not re.search(schema["pattern"], instance):
            errors.append(f"{path}: 值 {instance!r} 不匹配 pattern {schema['pattern']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: 缺少必填字段 {key}")
        props = schema.get("properties", {})
        for key, val in instance.items():
            if key in props:
                errors += mini_validate(val, props[key], docs, current_doc, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: 出现未声明字段 {key}")

    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            errors += mini_validate(item, schema["items"], docs, current_doc, f"{path}[{i}]")

    return errors


def _focus_branch(mod, doc):
    """把 allOf 里的 oneOf 收敛到与 doc.module 匹配的分支，使契约错误可读
    （否则 jsonschema 只会报「不满足任一分支」，无法定位到具体字段）。"""
    module = doc.get("module") if isinstance(doc, dict) else None
    if module is None:
        return None
    clone = json.loads(json.dumps(mod))
    for i, sub in enumerate(clone.get("allOf", [])):
        if "oneOf" not in sub:
            continue
        for br in sub["oneOf"]:
            if br.get("properties", {}).get("module", {}).get("const") == module:
                clone["allOf"][i] = br
                break
    return clone


def contract_check(doc, schema_dir):
    env = json.loads((schema_dir / "envelope.schema.json").read_text(encoding="utf-8"))
    mod = json.loads((schema_dir / "module.schema.json").read_text(encoding="utf-8"))
    docs = {"envelope": env, "module": mod}
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012

        registry = Registry().with_resources([
            (env["$id"], Resource.from_contents(env, DRAFT202012)),
            (mod["$id"], Resource.from_contents(mod, DRAFT202012)),
        ])
        errs = sorted(Draft202012Validator(mod, registry=registry).iter_errors(doc),
                      key=lambda e: list(e.path))
        if errs:
            focused = _focus_branch(mod, doc)
            if focused:
                errs2 = sorted(Draft202012Validator(focused, registry=registry).iter_errors(doc),
                               key=lambda e: list(e.path))
                if errs2 and len(errs2) <= len(errs):
                    errs = errs2
        return [f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errs], "jsonschema"
    except ImportError:
        return mini_validate(doc, mod, docs, mod), "mini"


# --------------------------------------------------------------------------
# 业务规则
# --------------------------------------------------------------------------

def _res(rid, name, ok, detail=""):
    return {"rule": rid, "name": name, "ok": bool(ok), "detail": detail}


STAGE_SYMBOLS = ("★", "▲", "█", "●")
RAW_NAME = "tender_raw.docx.txt"
FP_NAME = "source_fingerprint.json"


def _nrm(s):
    return re.sub(r"[\s\u3000]", "", s or "")


def _load_sib(base_dir, name):
    if not base_dir:
        return None
    p = Path(base_dir) / name
    return p if p.exists() else None


def _rules_r15_r16(data, reqs, base_dir, out):
    """R15 符号覆盖台账完整 / R16 locator 可回指源文本。

    这两条规则针对已发生的两类真实缺陷：
      - 源文本中的实质性符号条款被漏拆，而旧的 R5 只做 brief 内部自洽，无法发现；
      - locator 指向未随包分发的另一份抽取文件，行号体系不一致，溯源链断裂。
    """
    sr = data.get("symbol_reconciliation") or {}
    entries = (sr.get("coverage_ledger") or {}).get("entries") or []
    if not entries:
        out.append(_res("R15", "符号覆盖台账完整", False,
                        "symbol_reconciliation.coverage_ledger.entries 缺失；"
                        "S1 必须逐行登记源文本中每一处符号位置（COVERED / EXEMPT）"))
    else:
        errs, seen = [], set()
        byid = {r.get("requirement_id"): r for r in reqs}
        for e in entries:
            sym, ln, disp = e.get("symbol"), e.get("source_line"), e.get("disposition")
            key = (sym, ln)
            if key in seen:
                errs.append(f"重复登记 {sym}@{ln}")
            seen.add(key)
            if sym not in STAGE_SYMBOLS:
                errs.append(f"非法符号 {sym!r}@{ln}")
            if not isinstance(ln, int) or ln <= 0:
                errs.append(f"非法源行号 {ln!r}")
            if disp == "COVERED":
                rid = e.get("requirement_id")
                if rid not in byid:
                    errs.append(f"COVERED 指向不存在的需求 {rid!r}@{ln}")
                elif byid[rid].get("severity_symbol") != sym:
                    errs.append(f"COVERED 符号不符 {rid}@{ln}：台账{sym} / 需求"
                                f"{byid[rid].get('severity_symbol')}")
            elif disp == "EXEMPT":
                if not (e.get("reason") or "").strip():
                    errs.append(f"EXEMPT 未给出理由 {sym}@{ln}")
                if e.get("requirement_id"):
                    errs.append(f"EXEMPT 不应携带需求ID {sym}@{ln}")
            else:
                errs.append(f"非法处置 {disp!r}@{ln}")

        actual = Counter(r.get("severity_symbol") for r in reqs if r.get("severity_symbol"))
        covered = Counter(e["symbol"] for e in entries if e.get("disposition") == "COVERED")
        for sym in STAGE_SYMBOLS:
            if covered.get(sym, 0) != actual.get(sym, 0):
                errs.append(f"{sym} 台账已落位 {covered.get(sym, 0)} ≠ 需求中 {actual.get(sym, 0)}")

        scope = "（未找到 source_fingerprint.json，仅做内部一致性校验）"
        fp_path = _load_sib(base_dir, FP_NAME)
        if fp_path:
            fp = json.loads(fp_path.read_text(encoding="utf-8"))
            files = [f for f in fp.get("files", []) if f.get("symbol_lines")]
            # 只认与 locator 同一行号体系的那份 raw（默认 docx），
            # 否则会把 pdf 的行号混进来导致假缺口。
            primary = [f for f in files if f.get("raw_text_file") == RAW_NAME] or files[:1]
            src = set()
            for f in primary:
                for sym, lines in (f.get("symbol_lines") or {}).items():
                    if sym in STAGE_SYMBOLS:
                        src |= {(sym, l) for l in lines}
            if src:
                missing = sorted(src - seen)
                extra = sorted(seen - src)
                if missing:
                    errs.append(f"源文本 {len(missing)} 处符号未登记，如 {missing[:4]}")
                if extra:
                    errs.append(f"台账 {len(extra)} 处符号在源文本中不存在，如 {extra[:4]}")
                scope = f"源文本（{primary[0]['raw_text_file']}）{len(src)} 处符号全部登记"
        out.append(_res("R15", "符号覆盖台账完整", not errs,
                        scope if not errs else "；".join(errs[:5])))

    raw_path = _load_sib(base_dir, RAW_NAME)
    if raw_path is None:
        out.append(_res("R16", "locator 可回指源文本", True,
                        f"同目录未找到 {RAW_NAME}，跳过（无法核验溯源）"))
        return
    raw = [l.split("\t", 1)[1] if "\t" in l else l
           for l in raw_path.read_text(encoding="utf-8").split("\n") if l]
    bad = []
    for r in reqs:
        ev = r.get("evidence_refs") or []
        if not ev:
            continue
        rid = r.get("requirement_id")
        m = re.search(r"第(\d+)行", ev[0].get("locator") or "")
        q = _nrm(ev[0].get("quote"))
        if not m or not q:
            bad.append(f"{rid} 缺行号或引句")
            continue
        i = int(m.group(1))
        if not 1 <= i <= len(raw):
            bad.append(f"{rid} 行号 {i} 越界（共 {len(raw)} 行）")
            continue
        window = _nrm("".join(raw[max(0, i - 1):i + 3]))
        if q[:40] not in window and q[:20] not in window:
            bad.append(f"{rid} 引句不在第 {i} 行附近")
    out.append(_res("R16", "locator 可回指源文本", not bad,
                    f"{len(reqs)} 条均已锚定" if not bad
                    else f"{len(bad)} 条失准：{bad[:4]}"))


def rules_s1(doc, expected_pid, base_dir=None):
    data = doc.get("data", {})
    reqs = data.get("requirements", [])
    out = []

    ids = [r.get("requirement_id") for r in reqs]
    bad_fmt = [i for i in ids if not isinstance(i, str) or not re.match(r"^REQ-[0-9]{3}$", i)]
    dup = [i for i, c in Counter(ids).items() if c > 1]
    out.append(_res("R1", "需求ID格式与唯一性", not bad_fmt and not dup,
                    f"共{len(ids)}条" + (f"；格式异常{bad_fmt}" if bad_fmt else "")
                    + (f"；重复{dup}" if dup else "")))

    no_ev = [r.get("requirement_id") for r in reqs
             if not r.get("evidence_refs")
             or any(not (e.get("quote") or "").strip() for e in r.get("evidence_refs", []))]
    out.append(_res("R2", "证据引用完整性", not no_ev,
                    f"缺失/空引句: {no_ev[:8]}" if no_ev else f"{len(reqs)}条均有引句"))

    bad_score = [r.get("requirement_id") for r in reqs
                 if r.get("category") != "SCORING" and r.get("score") is not None]
    out.append(_res("R3", "非评分类不得带分值", not bad_score,
                    f"越界: {bad_score[:8]}" if bad_score else "无"))

    pool = data.get("scoring_pool") or {}
    items = pool.get("items") or []
    if items:
        computed = round(sum(i.get("count", 0) * i.get("deduction_per_item", 0) for i in items), 4)
        declared = pool.get("declared_total")
        formula = " + ".join(f"{i.get('count')}×{i.get('deduction_per_item')}" for i in items)
        closed = declared is not None and abs(computed - declared) < 1e-6
        detail = f"{formula} = {computed} / 声明 {declared}"
        if not closed and declared is not None:
            detail += f"（差额 {round(computed - declared, 4)}）"
        out.append(_res("R4", "分值池闭合", closed, detail))
    else:
        out.append(_res("R4", "分值池闭合", True, "本文件无分值池结构（跳过）"))

    actual = Counter(r["severity_symbol"] for r in reqs if r.get("severity_symbol"))
    recon = data.get("symbol_reconciliation") or {}
    recon_actual = {k: v for k, v in (recon.get("actual") or {}).items()}
    ok5 = True
    detail5 = f"实测 {dict(actual)}"
    for sym, cnt in actual.items():
        if recon_actual.get(sym, 0) != cnt:
            ok5 = False
            detail5 += f"；{sym} 对账写 {recon_actual.get(sym, 0)} 实际 {cnt}"
    for sym, cnt in recon_actual.items():
        if actual.get(sym, 0) != cnt:
            ok5 = False
            detail5 += f"；{sym} 声明 {cnt} 实际 {actual.get(sym, 0)}"
    pool_counts = {i.get("symbol"): i.get("count") for i in items}
    for sym, cnt in actual.items():
        if sym in pool_counts and pool_counts[sym] != cnt:
            ok5 = False
            detail5 += f"；{sym} 分值池写 {pool_counts[sym]} 实际 {cnt}"
    out.append(_res("R5", "符号条款对账", ok5, detail5))

    out.append(_res("R6", "未读范围已声明", "unread_sections" in data,
                    f"{len(data.get('unread_sections', []))} 项"))

    bidder_ok = doc.get("bidder_name") == FIXED_BIDDER
    pid_ok = (expected_pid is None) or (doc.get("project_id") == expected_pid)
    p_bidder = (data.get("project") or {}).get("bidder_name")
    inner_ok = p_bidder in (None, FIXED_BIDDER)
    out.append(_res("R7", "主体与项目ID一致", bidder_ok and pid_ok and inner_ok,
                    f"信封={doc.get('bidder_name')}"
                    + (f"；项目ID={doc.get('project_id')}" if pid_ok else "；项目ID与上游不符")
                    + ("" if inner_ok else f"；data.project.bidder_name={p_bidder}")))

    status, issues = doc.get("processing_status"), doc.get("issues", [])
    ok8 = not (status == "COMPLETE" and issues)
    out.append(_res("R8", "状态与issue一致性", ok8,
                    f"status={status}，issues={len(issues)}"
                    + ("（有issue却标COMPLETE）" if not ok8 else "")))

    _rules_r15_r16(data, reqs, base_dir, out)
    return out


def rules_s2(doc, expected_pid, base_dir=None):
    data = doc.get("data", {})
    items = data.get("items", [])
    idx = data.get("material_index", [])
    out = []

    falsy = [i.get("material_id") for i in items
             if not idx and i.get("status") in ("COVERED", "PARTIAL", "EXPIRED")]
    out.append(_res("R9", "空资料库不得出现已覆盖", not falsy,
                    f"资料库为空却有非MISSING条目: {falsy[:8]}" if falsy
                    else f"资料库{len(idx)}份，条目{len(items)}"))

    conf = data.get("confirmation") or {}
    ok10 = conf.get("confirmed_by") in (None, "")
    out.append(_res("R10", "确认人不得由模型填写", ok10,
                    f"confirmed_by={conf.get('confirmed_by')!r}"))

    s = data.get("summary", {})
    total = s.get("total_requirements")
    covered = sum(1 for i in items if i.get("status") == "COVERED")
    missing = sum(1 for i in items if i.get("status") == "MISSING")
    ok11 = (total == len(items)) and (s.get("covered") == covered) and (s.get("missing") == missing)
    out.append(_res("R11", "汇总与明细一致", ok11,
                    f"声明 total={total}/covered={s.get('covered')}/missing={s.get('missing')}"
                    f"；实际 {len(items)}/{covered}/{missing}"))

    out.append(_res("R7", "主体与项目ID一致", doc.get("bidder_name") == FIXED_BIDDER,
                    f"信封={doc.get('bidder_name')}"))
    out.append(_res("R8", "状态与issue一致性",
                    not (doc.get("processing_status") == "COMPLETE" and doc.get("issues")),
                    f"status={doc.get('processing_status')}，issues={len(doc.get('issues', []))}"))
    return out


def rules_s3(doc, expected_pid, base_dir=None):
    data = doc.get("data", {})
    out = []

    clar = data.get("clarification_items", [])
    bad = [c.get("question", "")[:24] for c in clar if c.get("status") != "DRAFTED"]
    out.append(_res("R12", "澄清清单初始状态为DRAFTED", not bad,
                    f"异常: {bad[:5]}" if bad else f"{len(clar)}项"))

    risks = data.get("risk_items", [])
    lv = {r.get("level") for r in risks}
    out.append(_res("R13", "风险等级取值合法", lv <= {"FATAL", "HIGH", "MEDIUM", "LOW"},
                    f"等级集合 {sorted(lv)}"))

    plan = data.get("section_plan", [])
    no_req = [p.get("section_id") for p in plan if not p.get("source_requirement_ids")]
    out.append(_res("R14", "章节计划须绑定需求ID", not no_req,
                    f"未绑定: {no_req[:8]}" if no_req else f"{len(plan)}章均已绑定"))

    out.append(_res("R7", "主体与项目ID一致", doc.get("bidder_name") == FIXED_BIDDER,
                    f"信封={doc.get('bidder_name')}"))
    out.append(_res("R8", "状态与issue一致性",
                    not (doc.get("processing_status") == "COMPLETE" and doc.get("issues")),
                    f"status={doc.get('processing_status')}，issues={len(doc.get('issues', []))}"))
    return out


def main():
    ap = argparse.ArgumentParser(description="科智汇川投标助手输出校验")
    ap.add_argument("--stage", required=True, choices=["S1", "S2", "S3"])
    ap.add_argument("--input", required=True, help="json 文件或包含该文件的目录")
    ap.add_argument("--expected-project-id", default=None)
    args = ap.parse_args()

    target = Path(args.input).resolve()
    if target.is_dir():
        base_dir = target
        target = target / FILE_BY_STAGE[args.stage]
    else:
        base_dir = target.parent
    if not target.exists():
        print(f"[ERROR] 找不到输入文件: {target}", file=sys.stderr)
        return 2

    doc = json.loads(target.read_text(encoding="utf-8"))
    schema_dir = Path(__file__).resolve().parent.parent / "schema"

    print(f"校验对象: {target.name}   阶段: {args.stage}\n")

    contract_errs, engine = contract_check(doc, schema_dir)
    print(f"[契约层] 引擎={engine}")
    if contract_errs:
        for e in contract_errs[:30]:
            print(f"   ✗ {e}")
        if len(contract_errs) > 30:
            print(f"   … 另有 {len(contract_errs) - 30} 条")
    else:
        print("   ✓ 结构合规")

    rules = {"S1": rules_s1, "S2": rules_s2, "S3": rules_s3}[args.stage]
    print("\n[业务层]")
    failures = 0
    for r in rules(doc, args.expected_project_id, base_dir):
        mark = "✓" if r["ok"] else "✗"
        if not r["ok"]:
            failures += 1
        print(f"   {mark} {r['rule']} {r['name']}: {r['detail']}")

    print()
    if contract_errs or failures:
        print(f"结果: 不通过（契约 {len(contract_errs)} 项 / 业务 {failures} 项）")
        return 1
    print("结果: 通过（退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
