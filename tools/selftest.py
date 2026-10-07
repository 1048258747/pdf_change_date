"""自测：验证日期识别、格式保持、PDF 表单域/注释改写全链路。

运行：
    .venv\\Scripts\\python.exe tools\\selftest.py
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import pymupdf  # noqa: E402

import make_samples  # noqa: E402
from pdfdatetool import batch  # noqa: E402
from pdfdatetool.core import apply_to_pdf, scan_pdf  # noqa: E402
from pdfdatetool.dates import find_dates, replace_dates  # noqa: E402

TARGET = date(2025, 6, 2)
failures: list[str] = []


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _page_text(path) -> str:
    doc = pymupdf.open(str(path))
    try:
        return "\n".join(p.get_text("text") for p in doc)
    finally:
        doc.close()


def check(label: str, got, expected) -> None:
    ok = got == expected
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {label}\n        got={got!r}\n        exp={expected!r}")
    if not ok:
        failures.append(label)


def test_date_engine() -> None:
    print("\n=== 1. 日期识别与格式保持 ===")
    cases = [
        ("2024年5月1日", "2025年6月2日"),
        ("2024 年 05 月 01 日", "2025 年 06 月 02 日"),
        ("2024/5/1", "2025/6/2"),
        ("2024-05-01", "2025-06-02"),
        ("2024.05.01", "2025.06.02"),
        ("05/01/2024", "06/02/2025"),
        ("20240501", "20250602"),
        ("签发日期：2024年5月1日（加盖公章）", "签发日期：2025年6月2日（加盖公章）"),
        ("EQ-2021-0087", "EQ-2021-0087"),
        ("版本 v2.4.1", "版本 v2.4.1"),
        ("2024-13-45", "2024-13-45"),
        ("编号 2024050", "编号 2024050"),
    ]
    for src, expected in cases:
        got, _ = replace_dates(src, TARGET)
        check(f"{src!r}", got, expected)

    multi = "起 2024-01-01 止 2024-12-31"
    got, changed = replace_dates(multi, TARGET)
    check("多日期同段", got, "起 2025-06-02 止 2025-06-02")
    check("多日期计数", len(changed), 2)


def test_pdf_pipeline() -> None:
    print("\n=== 2. 生成样例 ===")
    form = make_samples.make_form_sample()
    annot = make_samples.make_annot_sample()
    plain = make_samples.make_plain_sample()
    invoice = make_samples.make_invoice_sample()
    print(f"生成: {form.name}, {annot.name}, {plain.name}, {invoice.name}")

    print("\n=== 3. 扫描识别 ===")
    for p in (form, annot, plain, invoice):
        res = scan_pdf(p)
        print(f"  {p.name}: 命中 {len(res.hits)} 处 / {res.date_count} 个日期 err={res.error}")
        for h in res.hits:
            print(f"     - [{h.source}] p{h.page} {h.label}: {h.text!r} -> {h.dates}")

    check("sample_form 日期数", scan_pdf(form).date_count, 3)
    check("sample_form 不含正文层误报",
          [h.source for h in scan_pdf(form).hits], ["表单域"] * 3)
    check("sample_annot 日期数", scan_pdf(annot).date_count, 3)
    check("sample_annot 不含正文层误报",
          [h.source for h in scan_pdf(annot).hits], ["注释"] * 3)
    check("sample_no_date 日期数", scan_pdf(plain).date_count, 0)
    check("sample_invoice 日期数", scan_pdf(invoice).date_count, 7)

    inv = scan_pdf(invoice)
    check("sample_invoice 全部来自正文文字层",
          {h.source for h in inv.hits}, {"正文文字"})

    print("\n=== 4. 批量替换并另存 ===")
    out_dir = ROOT / "samples" / "_out"
    report = batch.run_batch([form, annot, plain, invoice], out_dir, TARGET)
    print(f"  共 {report.total} 个文件，成功 {report.succeeded}，改动 {report.touched}，替换 {report.replaced} 处日期")
    for r in report.results:
        print(f"     - {r.path.name}: ok={r.ok} replaced={r.replaced} "
              f"(正文{r.texts}/表单{r.fields}/注释{r.annots}) err={r.error}")

    check("批量成功数", report.succeeded, 4)
    check("批量替换总数", report.replaced, 13)

    print("\n=== 5. 输出内容校验 ===")
    must_have = {
        form: ["2025年6月2日", "2025/6/2", "20250602"],
        annot: ["2025年6月2日", "06/02/2025"],
        invoice: [
            "2025年6月2日", "2025-06-02", "06/02/2025",
            "2025/6/2", "2025.06.02", "20250602",
        ],
    }
    for src, values in must_have.items():
        out = out_dir / src.name
        text = _squash(_page_text(out))
        missing = [v for v in values if _squash(v) not in text]
        leaked = [v for v in ("2024", "20240501", "05/01/2024") if _squash(v) in text]
        check(f"{src.name} 页面文字", (missing, leaked), ([], []))
        print(f"        页面文字：{_squash(_page_text(out))[:90]!r}")

    # 便签注释的内容不在页面文字层，单独按扫描结果校验
    annot_out = scan_pdf(out_dir / annot.name)
    note = [h.text for h in annot_out.hits if "归档" in h.text]
    check("sample_annot 便签注释内容", note, ["归档日期 2025-06-02"])

    # 重绘后的日期仍可被正常检索（复制/搜索可用）
    doc = pymupdf.open(str(out_dir / invoice.name))
    found = doc[0].search_for("2025-06-02")
    doc.close()
    check("发票正文日期可检索", len(found) >= 1, True)

    check("无日期文件未改动", scan_pdf(out_dir / plain.name).date_count, 0)

    print("\n=== 6. 图片渲染抽检 ===")
    for src in (form, annot, invoice):
        for tag, path in (("改前", src), ("改后", out_dir / src.name)):
            doc = pymupdf.open(str(path))
            png = ROOT / "samples" / "_out" / f"{src.stem}_{tag}.png"
            doc[0].get_pixmap(dpi=110).save(str(png))
            doc.close()
            print(f"  已渲染 {png.relative_to(ROOT)}")


def test_ota_versions() -> None:
    print("\n=== 7. 版本比对与更新判定 ===")
    from pdfdatetool import REPO, __version__, ota

    check("parse_version('v1.2.3')", ota.parse_version("v1.2.3"), (1, 2, 3))
    check("parse_version('')", ota.parse_version(""), (0,))
    check("current_version 与包版本一致", ota.current_version(), __version__)
    check("仓库地址已配置", bool(REPO and "/" in REPO), True)
    check("is_frozen 源码下为 False", ota.is_frozen(), False)
    check("executable_path 源码下为 None", ota.executable_path(), None)

    for candidate, base, expected in (
        ("v1.2.0", "1.1.0", True),
        ("1.1.0", "1.1.0", False),
        ("1.0.9", "1.1.0", False),
        ("v1.10.0", "1.9.0", True),
        ("1.1", "1.1.0", False),
        ("2.0", "1.99.99", True),
        ("", "1.1.0", False),
    ):
        check(f"is_newer({candidate!r}, {base!r})", ota.is_newer(candidate, base), expected)

    print("\n=== 8. 更新替换的防御性分支 ===")
    missing = ROOT / "samples" / "_out" / "__not_exist__.exe"
    try:
        ota.apply_update(missing)
        raised = None
    except ota.UpdateError as exc:
        raised = str(exc)
    check("更新包不存在时抛错", bool(raised), True)

    probe = ROOT / "samples" / "_out" / "__probe__.exe"
    probe.write_bytes(b"stub")
    try:
        ota.apply_update(probe)
        raised = None
    except ota.UpdateError as exc:
        raised = str(exc)
    finally:
        probe.unlink(missing_ok=True)
    check("源码运行时拒绝自动替换", "源码" in (raised or ""), True)


def test_metadata() -> None:
    print("\n=== 9. 元数据与免责声明 ===")
    from pdfdatetool import DISCLAIMER, DISCLAIMER_ONELINE, DISCLAIMER_TITLE, __version__

    check("版本号形如 x.y.z", bool(re.fullmatch(r"\d+\.\d+\.\d+", __version__)), True)
    check("免责声明非空", len(DISCLAIMER.strip()) > 50, True)
    check("声明含禁止用途表述", "违法" in DISCLAIMER, True)
    check("声明含免责表述", "不负责" in DISCLAIMER or "责任" in DISCLAIMER, True)
    check("标题非空", bool(DISCLAIMER_TITLE.strip()), True)
    check("单行声明不含换行", "\n" not in DISCLAIMER_ONELINE, True)
    check("单行声明不含单引号", "'" not in DISCLAIMER_ONELINE, True)


def main() -> int:
    test_date_engine()
    test_pdf_pipeline()
    test_ota_versions()
    test_metadata()
    print("\n" + "=" * 50)
    if failures:
        print(f"结果：{len(failures)} 项失败")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())