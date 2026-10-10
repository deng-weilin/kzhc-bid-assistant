#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_tender.py — S1 输入准备

把招标/磋商文件（docx / pdf）抽成「带行号的纯文本 + 指纹」，作为一切溯源的基础。
不判断内容含义，只做确定性抽取与统计。

用法:
    python extract_tender.py --input <文件> [--input <文件2>] --output-dir <目录> [--tag <标签>]

产出（输出目录内）:
    tender_raw.<tag>.txt          带行号文本，每行 "L0001\\t正文"
    source_fingerprint.json       指纹、章节骨架、符号统计、页行偏移
    format_diff.json              （多个输入时）格式差异比对
"""

import argparse
import hashlib
import html
import json
import re
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SYMBOLS = ["★", "▲", "█", "●", "☆", "△", "◆", "■"]
CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百]+\s*[章节部分]")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_docx(path: Path):
    """抽取 docx 正文。表格行内单元格用制表符分隔，段落各占一行。"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")

    xml = re.sub(r"</w:p>\s*</w:tc>", "\x01", xml)
    xml = xml.replace("</w:tc>", "\x01")
    xml = xml.replace("</w:tr>", "\n")
    xml = xml.replace("</w:p>", "\n")
    xml = xml.replace("<w:tab/>", "\t")
    xml = xml.replace("<w:br/>", "\n")
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    text = text.replace("\x01", "\t")

    lines = []
    for raw in text.split("\n"):
        s = re.sub(r"[ \u3000]+", " ", raw).strip()
        if s:
            lines.append(s)

    meta = {
        "has_numbering_xml": any(n.endswith("numbering.xml") for n in names),
        "has_footnotes_xml": any(n.endswith("footnotes.xml") for n in names),
        "zip_entry_count": len(names),
    }
    page_map = [None] * len(lines)
    return lines, meta, page_map


def read_pdf(path: Path):
    """抽取 pdf 文本。需要 pypdf。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 pypdf，无法抽取 pdf：pip install pypdf") from exc

    reader = PdfReader(str(path))
    lines, page_map, offsets = [], [], []
    for i, page in enumerate(reader.pages):
        offsets.append(len(lines) + 1)
        txt = page.extract_text() or ""
        for raw in txt.split("\n"):
            s = re.sub(r"[ \u3000]+", " ", raw).strip()
            if s:
                lines.append(s)
                page_map.append(i + 1)
    meta = {"page_count": len(reader.pages), "page_line_offsets": offsets}
    return lines, meta, page_map


def build_skeleton(lines):
    return [
        {"line": i + 1, "heading": ln}
        for i, ln in enumerate(lines)
        if CHAPTER_RE.match(ln)
    ]


def count_symbols(lines):
    out = {}
    for sym in SYMBOLS:
        hits = [i + 1 for i, ln in enumerate(lines) if sym in ln]
        if hits:
            out[sym] = {"count": len(hits), "lines": hits}
    return out


def main():
    ap = argparse.ArgumentParser(description="招标文件抽取与指纹")
    ap.add_argument("--input", action="append", required=True, help="可重复传入")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--tag", default=None, help="输出文件名标签，默认取扩展名")
    args = ap.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = {
        "schema_version": "kzhc.tender-fingerprint.v1",
        "files": [],
        "primary": None,
        "warnings": [],
    }
    per_format = {}

    for p in args.input:
        path = Path(p).resolve()
        if not path.exists():
            print(f"[ERROR] 文件不存在: {path}", file=sys.stderr)
            return 1
        suffix = path.suffix.lower().lstrip(".")
        tag = args.tag or suffix

        if suffix == "docx":
            lines, meta, page_map = read_docx(path)
        elif suffix == "pdf":
            lines, meta, page_map = read_pdf(path)
        else:
            print(f"[ERROR] 不支持的格式: {suffix}（只支持 docx / pdf）", file=sys.stderr)
            return 1

        raw_path = out_dir / f"tender_raw.{tag}.txt"
        with open(raw_path, "w", encoding="utf-8") as f:
            for i, ln in enumerate(lines, 1):
                f.write(f"L{i:04d}\t{ln}\n")

        symbols = count_symbols(lines)
        skeleton = build_skeleton(lines)
        entry = {
            "tag": tag,
            "file_name": path.name,
            "relative_path": path.name,
            "kind": "tender_document",
            "origin": "user_supplied",
            "sha256": sha256_of(path),
            "version": None,
            "size_bytes": path.stat().st_size,
            "line_count": len(lines),
            "char_count": sum(len(l) for l in lines),
            "raw_text_file": raw_path.name,
            "meta": meta,
            "chapter_skeleton": skeleton,
            "symbol_stats": {k: v["count"] for k, v in symbols.items()},
            "symbol_lines": {k: v["lines"] for k, v in symbols.items()},
        }
        if page_map and any(page_map):
            entry["line_page_map_available"] = True
        fingerprint["files"].append(entry)
        per_format[tag] = {
            "line_count": len(lines),
            "symbol_stats": {k: v["count"] for k, v in symbols.items()},
            "chapters": [s["heading"] for s in skeleton],
        }

    # 单一 docx + 单一 pdf 时的差异比对
    if "docx" in per_format and "pdf" in per_format:
        d, f = per_format["docx"], per_format["pdf"]
        diff = {
            "line_count": {"docx": d["line_count"], "pdf": f["line_count"],
                           "delta": d["line_count"] - f["line_count"]},
            "symbol_stats": {
                sym: {"docx": d["symbol_stats"].get(sym, 0), "pdf": f["symbol_stats"].get(sym, 0)}
                for sym in sorted(set(d["symbol_stats"]) | set(f["symbol_stats"]))
            },
            "chapters_only_in_docx": [c for c in d["chapters"] if c not in f["chapters"]],
            "chapters_only_in_pdf": [c for c in f["chapters"] if c not in d["chapters"]],
        }
        mismatch = [s for s, v in diff["symbol_stats"].items() if v["docx"] != v["pdf"]]
        diff["symbol_mismatch"] = mismatch
        diff["verdict"] = (
            "SAME" if not mismatch and not diff["chapters_only_in_docx"] and not diff["chapters_only_in_pdf"]
            else "DIFF_FOUND"
        )
        with open(out_dir / "format_diff.json", "w", encoding="utf-8") as fh:
            json.dump(diff, fh, ensure_ascii=False, indent=2)
        if diff["verdict"] == "DIFF_FOUND":
            fingerprint["warnings"].append(
                "docx 与 pdf 抽取结果存在差异，见 format_diff.json；"
                "若招标文件规定以某一格式为准，须以该格式复核关键条款。"
            )

    # 声明「以 pdf 为准」类条款的自动探测
    for entry in fingerprint["files"]:
        raw_path = out_dir / entry["raw_text_file"]
        text = raw_path.read_text(encoding="utf-8")
        for m in re.finditer(r".{0,40}(以\s*pdf.{0,10}为准|pdf.{0,6}格式.{0,10}为准).{0,40}", text):
            fingerprint["warnings"].append(
                f"检测到格式优先级声明（{entry['tag']}）：…{m.group(0).strip()}…"
            )

    if len(fingerprint["files"]) >= 1:
        fingerprint["primary"] = fingerprint["files"][0]["tag"]

    fp_path = out_dir / "source_fingerprint.json"
    with open(fp_path, "w", encoding="utf-8") as fh:
        json.dump(fingerprint, fh, ensure_ascii=False, indent=2)

    print("=== 抽取完成 ===")
    for e in fingerprint["files"]:
        print(f"  [{e['tag']}] {e['file_name']}")
        print(f"       行数 {e['line_count']} / 字符 {e['char_count']}")
        print(f"       sha256 {e['sha256'][:16]}…")
        print(f"       章节 {len(e['chapter_skeleton'])} 处 / 符号 {e['symbol_stats']}")
        print(f"       → {e['raw_text_file']}")
    for w in fingerprint["warnings"]:
        print(f"  [WARN] {w}")
    print(f"  → {fp_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
