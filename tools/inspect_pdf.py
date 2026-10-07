"""诊断脚本：看清一个 PDF 里日期到底藏在哪。

当主程序提示「未发现日期」时，用本脚本查看该 PDF 的实际结构：
元数据、表单域、注释、以及页面文字层中出现的日期。

用法：
    .venv\\Scripts\\python.exe tools\\inspect_pdf.py "某个文件.pdf"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from pdfdatetool.dates import find_dates  # noqa: E402


def inspect(path: Path) -> int:
    print(f"\n{'=' * 70}\n文件：{path}\n{'=' * 70}")
    if not path.exists():
        print("文件不存在。")
        return 1

    doc = pymupdf.open(str(path))
    print(f"页数：{doc.page_count}    加密：{doc.needs_pass}")

    print("\n--- 元数据 DocInfo ---")
    meta = doc.metadata or {}
    for key in ("title", "author", "subject", "creationDate", "modDate", "producer"):
        print(f"  {key:14s}= {meta.get(key)!r}")

    field_total = annot_total = text_total = 0

    for pno in range(doc.page_count):
        page = doc[pno]
        print(f"\n--- 第 {pno + 1} 页 ---")

        try:
            widgets = list(page.widgets())
        except Exception:
            widgets = []
        print(f"  表单域：{len(widgets)} 个")
        for w in widgets:
            value = w.field_value or ""
            mark = "  <== 含日期" if find_dates(value) else ""
            print(f"    [{w.field_name or '(无名)'}] type={w.field_type_string!r} value={value!r}{mark}")
            field_total += 1

        try:
            annots = list(page.annots())
        except Exception:
            annots = []
        print(f"  注释：{len(annots)} 个")
        for a in annots:
            info = a.info or {}
            content = info.get("content", "") or ""
            t = a.type
            name = t[1] if isinstance(t, (list, tuple)) and len(t) == 2 else t
            mark = "  <== 含日期" if find_dates(content) else ""
            print(f"    [{name}] content={content!r}{mark}")
            annot_total += 1

        text = page.get_text("text")
        text_dates = find_dates(text)
        print(f"  页面文字层：{len(text)} 字符，其中日期 {len(text_dates)} 个")
        for d in text_dates[:10]:
            print(f"    {d.text!r}")
        text_total += len(text_dates)

    doc.close()

    print(f"\n{'=' * 70}")
    print(f"汇总：表单域 {field_total} 个，注释 {annot_total} 个，页面文字层日期 {text_total} 个")
    print("说明：")
    print("  - 表单域 / 注释 里的日期：可直接改写。")
    print("  - 页面文字层里的日期：可直接改写（数字版 PDF，如发票、报表）。")
    print("  - 若以上都没有，而页面上却能看到日期，说明日期是扫描图像上的像素，")
    print("    需要 OCR 定位 + 图像擦除重绘方案。")
    print("  - 元数据里的创建/修改时间本工具不改。")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    return inspect(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())