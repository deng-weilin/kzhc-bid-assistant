#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验器元验证（self-test）—— 证明 validate.py 不是"永远通过"的摆设。

做法：拿合规的金样例产出，往里面注入已知错误，逐条确认 validate.py 拒收。
      任何一条注入没被抓住，本脚本退出码即为 1。

改动 schema / 校验规则 / 任何脚本后，必须复跑本脚本，且结果必须仍是"全部符合预期"。

用法：
    python scripts/self_test.py
    python scripts/self_test.py --example <金样例目录>

退出码：0 = 正常路径全通过 且 全部注入被拒；1 = 存在退化。
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXAMPLE = SKILL_ROOT / "examples" / "四川铁道职院AI课程建设_20261010"
VALIDATOR = SKILL_ROOT / "scripts" / "validate.py"


# ---------------------------------------------------------------- 注入用例
def build_cases(brief: dict, checklist: dict, plan: dict):
    """返回 [(编号, 名称, 期望阶段, 变动后的文档, 期望命中的规则关键字)]"""
    cases = []

    # --- S1 ---
    d = copy.deepcopy(brief)
    d["bidder_name"] = "某某科技有限公司"
    d["data"]["project"]["bidder_name"] = "某某科技有限公司"
    cases.append(("01", "篡改投标主体为别家公司", "S1", d, "R7"))

    d = copy.deepcopy(brief)
    d["data"]["scoring_pool"]["declared_total"] = 47.0
    cases.append(("02", "破坏分值池声明总分（48→47）", "S1", d, "R4"))

    d = copy.deepcopy(brief)
    for r in d["data"]["requirements"]:
        if r["category"] == "QUALIFICATION":
            r["score"] = 5.0
            break
    cases.append(("03", "给非评分类要求硬塞分值", "S1", d, "R3"))

    d = copy.deepcopy(brief)
    d["data"]["requirements"][0]["evidence_refs"] = []
    cases.append(("04", "抽掉一条要求的来源引证", "S1", d, "R2"))

    d = copy.deepcopy(brief)
    d["data"]["symbol_reconciliation"]["actual"]["▲"] = 99
    cases.append(("05", "符号对账写虚（▲ 12→99）", "S1", d, "R5"))

    d = copy.deepcopy(brief)
    d["data"]["requirements"][1]["requirement_id"] = d["data"]["requirements"][0]["requirement_id"]
    cases.append(("06", "制造重复的需求ID", "S1", d, "R1"))

    d = copy.deepcopy(brief)
    d["processing_status"] = "COMPLETE"  # 仍有 8 条 issue
    cases.append(("07", "有问题却标 COMPLETE", "S1", d, "R8"))

    # --- S2 ---
    d = copy.deepcopy(checklist)
    for it in d["data"]["items"]:
        if it["status"] == "MISSING":
            it["status"] = "COVERED"
            break
    cases.append(("08", "空资料库却判为已覆盖", "S2", d, "R9"))

    d = copy.deepcopy(checklist)
    d["data"]["confirmation"]["confirmed_by"] = "大模型"
    cases.append(("09", "模型代填人工确认人", "S2", d, "R10"))

    # --- S3 ---
    d = copy.deepcopy(plan)
    d["data"]["clarification_items"][0]["status"] = "SENT"
    cases.append(("10", "把待澄清问题预置成已发出", "S3", d, "R12"))

    d = copy.deepcopy(plan)
    d["data"]["section_plan"][0]["source_requirement_ids"] = []
    cases.append(("11", "章节计划不绑定需求ID", "S3", d, "R14"))

    d = copy.deepcopy(plan)
    d["data"]["risk_items"][0]["level"] = "SEVERE"
    cases.append(("12", "风险等级用非法取值", "S3", d, "R13"))

    # --- S1 · R15 符号覆盖台账（源文本 ↔ brief 对账）---
    d = copy.deepcopy(brief)
    d["data"]["symbol_reconciliation"].pop("coverage_ledger", None)
    cases.append(("13", "删除符号覆盖台账", "S1", d, "R15"))

    d = copy.deepcopy(brief)
    for r in d["data"]["requirements"]:
        if r["severity_symbol"] == "★":
            r["severity_symbol"] = None      # 台账仍写 27，需求侧少 1
            break
    cases.append(("14", "偷偷抹掉一条★且不更新台账", "S1", d, "R15"))

    d = copy.deepcopy(brief)
    d["data"]["symbol_reconciliation"]["coverage_ledger"]["entries"][0]["disposition"] = "EXEMPT"
    cases.append(("15", "把已落位登记改成EXEMPT逃避对账", "S1", d, "R15"))

    # --- S1 · R16 locator 可回指源文本 ---
    d = copy.deepcopy(brief)
    d["data"]["requirements"][0]["evidence_refs"][0]["locator"] = "第9000行 一、一般资格审查"
    cases.append(("16", "locator 行号越界", "S1", d, "R16"))

    d = copy.deepcopy(brief)
    d["data"]["requirements"][5]["evidence_refs"][0]["locator"] = "第1行 一、一般资格审查"
    cases.append(("17", "locator 指向错误行", "S1", d, "R16"))

    return cases


# ---------------------------------------------------------------- 执行
def run_validator(stage: str, target: Path):
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--stage", stage, "--input", str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    hits = [ln.strip() for ln in out.splitlines() if "✗" in ln]
    return proc.returncode, hits, out


def main() -> int:
    ap = argparse.ArgumentParser(description="校验器元验证")
    ap.add_argument("--example", default=str(DEFAULT_EXAMPLE), help="金样例目录")
    args = ap.parse_args()

    ex = Path(args.example)
    if not ex.is_dir():
        print(f"[错误] 金样例目录不存在：{ex}")
        return 1

    brief = json.loads((ex / "tender_brief.json").read_text(encoding="utf-8"))
    checklist = json.loads((ex / "material_checklist.json").read_text(encoding="utf-8"))
    plan = json.loads((ex / "bid_plan.json").read_text(encoding="utf-8"))

    print("=" * 74)
    print("校验器元验证  validate.py self-test")
    print(f"金样例：{ex.name}")
    print("=" * 74)

    failures = 0
    total = 0

    # ---------- A. 正常路径必须通过 ----------
    print("\n[A] 正常路径（合规产出必须放行）")
    for stage, fname in [("S1", "tender_brief.json"),
                         ("S2", "material_checklist.json"),
                         ("S3", "bid_plan.json")]:
        total += 1
        code, hits, _ = run_validator(stage, ex / fname)
        ok = (code == 0 and not hits)
        failures += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {stage} {fname:<24} 退出码={code}  误报={len(hits)} 条")

    # ---------- B. 注入必须被拒 ----------
    print("\n[B] 错误注入（必须逐条被拒）")
    cases = build_cases(brief, checklist, plan)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # R15 / R16 需要同目录下的源指纹与原文；不复制过去它们会退化为"跳过"，
        # 注入用例就会假通过。这里显式带上，确保两条规则真的在跑。
        for sib in ("source_fingerprint.json", "tender_raw.docx.txt"):
            if (ex / sib).exists():
                shutil.copy(ex / sib, tmp / sib)
        for num, name, stage, doc, expect in cases:
            total += 1
            path = tmp / f"case{num}_{stage}.json"
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            code, hits, out = run_validator(stage, path)
            caught = (code != 0)
            rule_named = any(expect in h for h in hits)
            ok = caught and rule_named
            failures += 0 if ok else 1
            mark = "✓" if ok else "✗"
            got = f"退出码={code}，命中 {len(hits)} 条"
            if caught and not rule_named:
                got += f"（但未见 {expect}，实际：{[h[:28] for h in hits[:2]]}）"
            print(f"  {mark} {num} {name:<26} [{stage}] {got}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 74)
    if failures == 0:
        print(f"结果：{total}/{total} 全部符合预期 —— 校验器有效。")
        print("含义：正常产出被放行，{n} 类已知错误全部被拒。".format(n=len(cases)))
        print("=" * 74)
        return 0
    print(f"结果：{total - failures}/{total} 符合预期，{failures} 项不符合预期。")
    print("含义：校验器已退化，可能放行了错误产出，或误杀了正常产出。")
    print("=" * 74)
    return 1


if __name__ == "__main__":
    sys.exit(main())
