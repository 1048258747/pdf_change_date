"""打包后的冒烟检查：确认 exe 里 PyMuPDF 能真正读写 PDF。

打包出的 exe 是 --windowed 的，没有控制台，看不到报错。这个脚本自己造一个
带日期的临时 PDF，跑「扫描 → 替换 → 回读校验」，结果同时打印并写文件，
用来验证打包配置（--collect-all pymupdf）是否完整。

用法（先按同样参数打成控制台版）：
    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean --onefile ^
        --console --name FrozenCheck --collect-all pymupdf --paths . ^
        --distpath build\\frozen --workpath build\\frozenwork ^
        --specpath build\\frozenwork tools\\frozen_check.py
    build\\frozen\\FrozenCheck.exe [结果输出路径]
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from datetime import date
from pathlib import Path


def _make_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 120), "Invoice Date: 2026-10-05", fontsize=12)
    page.insert_text((72, 160), "Report Date: 2026/10/05", fontsize=12)
    doc.save(str(path))
    doc.close()


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("frozen_report.txt")
    lines: list[str] = []

    def note(text: str) -> None:
        lines.append(text)

    ok = False
    try:
        import pymupdf

        note(f"pymupdf  = {pymupdf.version}")
        note(f"frozen   = {bool(getattr(sys, 'frozen', False))}")

        from pdfdatetool.core import apply_to_pdf, scan_pdf

        work = Path(tempfile.mkdtemp(prefix="pdfdt_check_"))
        src, dst = work / "in.pdf", work / "out.pdf"
        _make_pdf(src)
        note(f"临时 PDF = {src}")

        scanned = scan_pdf(src)
        note(f"扫描     = {scanned.date_count} 个日期，err={scanned.error}")

        result = apply_to_pdf(src, dst, date(2025, 6, 2))
        note(f"替换     = ok={result.ok} replaced={result.replaced} err={result.error}")

        doc = pymupdf.open(str(dst))
        text = doc[0].get_text("text").replace(" ", "")
        doc.close()
        note(f"页面文字 = {text!r}")

        hit = "2025-06-02" in text and "2025/06/02" in text
        note(f"回读校验 = {'PASS' if hit else 'FAIL'}（原日期两位月/日，替换后保持两位）")
        ok = bool(result.ok and result.replaced >= 2 and hit)
    except Exception:
        note("EXCEPTION:\n" + traceback.format_exc())

    report = "\n".join(lines)
    print(report)
    try:
        out_path.write_text(report, encoding="utf-8")
    except OSError as exc:
        print(f"（写报告失败：{exc}）")
    print(f"\n结果：{'通过' if ok else '失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())