"""生成用于自测的样例 PDF。

样例刻意模拟「扫描件」：整页背景是一张位图（模拟扫描图像），
日期则放在 AcroForm 表单域和注释对象里，用来验证工具无需 OCR 即可改写。

运行：
    .venv\\Scripts\\python.exe tools\\make_samples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"


def _scanned_background() -> bytes:
    """渲染一张「纸质表单」位图，模拟扫描件。"""
    tmp = pymupdf.open()
    page = tmp.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=(0.85, 0.85, 0.82), fill=(0.97, 0.96, 0.93))

    page.insert_text((60, 90), "QUALITY INSPECTION RECORD", fontsize=18, fontname="hebo")
    page.insert_text((60, 115), "质量检验记录单", fontsize=13, fontname="china-s")
    page.draw_line((60, 130), (535, 130), color=(0.3, 0.3, 0.3), width=1)

    rows = [
        ("设备编号 / Device No.", "EQ-2021-0087"),
        ("检验员 / Inspector", "Zhang Wei"),
        ("检验日期 / Inspection Date", "(见下方填写框)"),
        ("审核日期 / Review Date", "(见下方批注)"),
        ("备注 / Remarks", "Sample document for testing."),
    ]
    y = 180
    for label, value in rows:
        page.insert_text((60, y), label, fontsize=11, fontname="china-s")
        page.insert_text((250, y), value, fontsize=11, fontname="helv")
        page.draw_line((60, y + 6), (535, y + 6), color=(0.6, 0.6, 0.6), width=0.5)
        y += 46

    page.insert_text((60, 760), "本单据由系统生成，扫描归档。", fontsize=9, fontname="china-s")

    pix = page.get_pixmap(dpi=120)
    data = pix.tobytes("png")
    tmp.close()
    return data


def _new_scanned_page(doc: pymupdf.Document, image: bytes) -> pymupdf.Page:
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=image)
    return page


def make_form_sample() -> Path:
    """表单域样例：日期放在 AcroForm 文本域里，格式各不相同。"""
    doc = pymupdf.open()
    image = _scanned_background()
    page = _new_scanned_page(doc, image)

    fields = [
        ("inspection_date", "2024年5月1日", (250, 285, 430, 310)),
        ("review_date", "2024/5/1", (250, 331, 430, 356)),
        ("archive_date", "20240501", (250, 377, 430, 402)),
    ]
    for name, value, rect in fields:
        w = pymupdf.Widget()
        w.field_name = name
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_value = value
        w.text_fontsize = 11
        w.rect = pymupdf.Rect(*rect)
        page.add_widget(w)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "sample_form.pdf"
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    return path


def make_annot_sample() -> Path:
    """注释样例：日期放在 FreeText 注释与便签注释里。"""
    doc = pymupdf.open()
    image = _scanned_background()
    page = _new_scanned_page(doc, image)

    a1 = page.add_freetext_annot(
        pymupdf.Rect(250, 285, 470, 312), "2024年5月1日", fontsize=11
    )
    a1.set_info(content="检验日期：2024年5月1日")
    a1.update()

    a2 = page.add_freetext_annot(
        pymupdf.Rect(250, 331, 470, 358), "05/01/2024", fontsize=11
    )
    a2.set_info(content="复核日期：05/01/2024")
    a2.update()

    a3 = page.add_text_annot(pymupdf.Point(500, 340), "归档日期 2024-05-01")
    a3.update()

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "sample_annot.pdf"
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    return path


def make_plain_sample() -> Path:
    """无日期样例：用于验证不会误改。"""
    doc = pymupdf.open()
    image = _scanned_background()
    page = _new_scanned_page(doc, image)

    w = pymupdf.Widget()
    w.field_name = "device_no"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_value = "EQ-2021-0087"
    w.text_fontsize = 11
    w.rect = pymupdf.Rect(250, 193, 430, 218)
    page.add_widget(w)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "sample_no_date.pdf"
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    return path


def make_invoice_sample() -> Path:
    """数字版发票样例：文字是可提取的正文层（非扫描件），日期散落在各处。

    用于验证「字符级定位 + 擦除 + 按原字体/字号/颜色重绘」这条路径。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    page.insert_text((70, 70), "增值税电子普通发票", fontsize=19, fontname="china-s",
                     color=(0.15, 0.15, 0.15))
    page.draw_line((70, 84), (525, 84), color=(0.4, 0.4, 0.4), width=1)

    page.insert_text((70, 120), "发票代码：044031900111", fontsize=11, fontname="china-s")
    page.insert_text((70, 142), "发票号码：12345678", fontsize=11, fontname="china-s")
    page.insert_text((70, 164), "开票日期：2024年5月1日", fontsize=11, fontname="china-s",
                     color=(0.0, 0.0, 0.6))

    page.insert_text((70, 210), "Invoice Date: 2024-05-01", fontsize=12, fontname="tiro")
    page.insert_text((70, 234), "Payment Due: 05/01/2024", fontsize=12, fontname="tiro")

    page.insert_text((70, 280), "项目明细", fontsize=12, fontname="china-s")
    page.draw_line((70, 290), (525, 290), color=(0.5, 0.5, 0.5), width=0.6)

    rows = [
        ("技术服务费", "2024/5/1", "1,200.00"),
        ("材料费", "2024.05.01", "860.00"),
        ("运输费", "20240501", "140.00"),
    ]
    y = 320
    for name, day, amount in rows:
        page.insert_text((75, y), name, fontsize=11, fontname="china-s")
        page.insert_text((260, y), day, fontsize=11, fontname="helv")
        page.insert_text((400, y), amount, fontsize=11, fontname="helv")
        y += 28

    page.insert_text((75, y + 20), "合计金额（大写）：贰仟贰佰元整", fontsize=11, fontname="china-s")
    page.insert_text((400, y + 20), "¥ 2,200.00", fontsize=13, fontname="hebo",
                     color=(0.6, 0.0, 0.0))
    page.insert_text((75, y + 60), "打印日期 2024-05-01 10:32:00", fontsize=9,
                     fontname="helv", color=(0.35, 0.35, 0.35))

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "sample_invoice.pdf"
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    return path


def main() -> int:
    paths = [
        make_form_sample(),
        make_annot_sample(),
        make_plain_sample(),
        make_invoice_sample(),
    ]
    for p in paths:
        print(f"已生成 {p.relative_to(ROOT)}  ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())