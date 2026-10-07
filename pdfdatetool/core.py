"""PDF 日期读写核心。

支持三类可写位置：

1. **正文文字层**（数字版 PDF，如发票、报表）——定位日期所在的字符，
   擦除后按原字体 / 字号 / 颜色在原位重绘；
2. **表单域**（AcroForm 文本域）；
3. **注释**（批注、FreeText 文本框、便签）。

表单域与注释是 PDF 里的矢量对象，对扫描件同样有效；正文文字层要求 PDF
本身带有可提取的文字（纯图像扫描件没有文字层，改不了）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path

import pymupdf

from .dates import find_dates, replace_dates

SOURCE_TEXT = "正文文字"
SOURCE_FIELD = "表单域"
SOURCE_ANNOT = "注释"
ALL_SOURCES = (SOURCE_TEXT, SOURCE_FIELD, SOURCE_ANNOT)

# 重绘正文日期时优先复用系统里同名中文字体（如 SimSun / SimHei）。
# 这类字体同时含全角汉字与半角数字，能把「2026年09月28日」整体重绘得与原样一致，
# 且整串一次性写入，复制/搜索时日期仍是连续的。找不到时退回内置字体。
_SYS_FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

_SYS_FONT_MAP = (
    (("simsun", "song", "ming", "sun"), "simsun.ttc"),
    (("simhei", "heiti", "hei"), "simhei.ttf"),
    (("kaiti", "kai"), "simkai.ttf"),
    (("fangsong", "fang"), "simfang.ttf"),
    (("yahei", "msyh"), "msyh.ttc"),
)

__all__ = [
    "SOURCE_TEXT",
    "SOURCE_FIELD",
    "SOURCE_ANNOT",
    "ALL_SOURCES",
    "Hit",
    "ScanResult",
    "ApplyResult",
    "scan_pdf",
    "apply_to_pdf",
]


@dataclass
class Hit:
    """一处命中：某个文本容器里发现了日期。"""

    source: str
    page: int
    label: str
    text: str
    dates: list[str] = dc_field(default_factory=list)


@dataclass
class ScanResult:
    path: Path
    hits: list[Hit] = dc_field(default_factory=list)
    error: str | None = None

    @property
    def date_count(self) -> int:
        return sum(len(h.dates) for h in self.hits)


@dataclass
class ApplyResult:
    path: Path
    output: Path | None = None
    replaced: int = 0
    texts: int = 0
    fields: int = 0
    annots: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# --------------------------------------------------------------------- 通用
def _has_cjk(text: str) -> bool:
    for ch in text:
        if "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef":
            return True
    return False


def _norm_font(name: str) -> str:
    """规范化字体名以便比对：去掉子集前缀、空格与常见分隔符。"""
    return re.sub(r"[\s\-_,]", "", name.split("+")[-1]).lower()


def _system_font_file(base: str) -> Path | None:
    """按字体名在系统字体目录里找同名中文字体文件。"""
    if not base:
        return None
    low = base.lower()
    for keys, fname in _SYS_FONT_MAP:
        if any(k in low for k in keys):
            path = _SYS_FONT_DIR / fname
            if path.exists():
                return path
    return None


def _font_covers(buf: bytes, text: str) -> bool:
    """内嵌字体是否包含 text 中所有字符的字形。"""
    try:
        font = pymupdf.Font(fontbuffer=buf)
    except Exception:
        return False
    for ch in set(text):
        if ch.isspace():
            continue
        try:
            if not font.has_glyph(ord(ch)):
                return False
        except Exception:
            return False
    return True


def _open(path: Path):
    doc = pymupdf.open(str(path))
    if doc.needs_pass:
        doc.close()
        raise ValueError("PDF 已加密，需要密码才能打开")
    return doc


# ------------------------------------------------------- 正文文字层（发票类）
def _raw_spans(page):
    """逐 span 产出正文文字，char 级信息用于精确定位。"""
    try:
        data = page.get_text("rawdict")
    except Exception:
        return
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("chars"):
                    yield span


def _span_text(span) -> str:
    return "".join(ch["c"] for ch in span["chars"])


def _range_rect(span, start: int, end: int) -> pymupdf.Rect:
    chars = span["chars"][start:end]
    rect = pymupdf.Rect(chars[0]["bbox"])
    for ch in chars[1:]:
        rect |= pymupdf.Rect(ch["bbox"])
    return rect


def _object_rects(page) -> list[pymupdf.Rect]:
    """表单域与注释自身的显示区域。

    这些区域里的文字由对象自己管理（外观流），也会被 get_text() 提取出来，
    必须从正文文字层里排除，否则同一处日期会被改写两次、出现重影。
    """
    rects: list[pymupdf.Rect] = []
    try:
        for w in page.widgets():
            if w.rect:
                rects.append(pymupdf.Rect(w.rect))
    except Exception:
        pass
    try:
        for a in page.annots():
            if a.rect:
                rects.append(pymupdf.Rect(a.rect))
    except Exception:
        pass
    return rects


def _covered_by(rect: pymupdf.Rect, blockers: list[pymupdf.Rect]) -> bool:
    """span 是否主要落在某个对象的显示区域内。"""
    area = abs(rect.get_area())
    if area <= 0:
        return False
    for b in blockers:
        inter = rect & b
        if inter.is_empty or inter.is_infinite:
            continue
        if abs(inter.get_area()) >= area * 0.6:
            return True
    return False


def _scan_text_layer(page, pno: int, blockers: list[pymupdf.Rect]) -> list[Hit]:
    hits: list[Hit] = []
    for span in _raw_spans(page):
        text = _span_text(span)
        matches = find_dates(text)
        if not matches:
            continue
        if _covered_by(pymupdf.Rect(span["bbox"]), blockers):
            continue
        label = f"{span.get('font', '?')} {span.get('size', 0):.1f}pt"
        hits.append(Hit(SOURCE_TEXT, pno + 1, label, text, [m.text for m in matches]))
    return hits


class _FontResolver:
    """把 span 的字体还原成可用于重绘的字体名。"""

    def __init__(self, doc):
        self.doc = doc
        self._cache: dict[tuple, str] = {}
        self._seq = 0

    def resolve(self, page, span, text: str) -> str:
        base = span.get("font") or ""
        key = (page.xref, base, _has_cjk(text))
        cached = self._cache.get(key)
        if cached:
            return cached
        name = (
            self._from_embedded(page, base, text)
            or self._system(page, base)
            or self._fallback(span, text)
        )
        self._cache[key] = name
        return name

    def _system(self, page, base: str) -> str | None:
        """用系统里的同名中文字体重绘，宽度与字形最接近原件。"""
        path = _system_font_file(base)
        if not path:
            return None
        self._seq += 1
        fontname = f"pdfsys{self._seq}"
        try:
            page.insert_font(fontname=fontname, fontfile=str(path))
        except Exception:
            return None
        return fontname

    def _from_embedded(self, page, base: str, text: str) -> str | None:
        """复用 PDF 内嵌的字体——前提是它真的含有本次要绘制的所有字形。

        子集化过的内嵌字体只包含文档实际用到的字形，缺字时重绘会变成空白，
        因此这里逐个字符检查字形覆盖。
        """
        if not base:
            return None
        target = _norm_font(base)
        try:
            fonts = page.get_fonts(full=True)
        except Exception:
            return None
        for entry in fonts:
            if len(entry) < 4:
                continue
            xref, ext, bfname = entry[0], entry[1], entry[3]
            if not bfname or not ext or ext == "n/a":
                continue
            found = _norm_font(bfname)
            if not found or not (found == target or target in found or found in target):
                continue
            try:
                _name, _ext, _sub, buf = self.doc.extract_font(xref)
            except Exception:
                continue
            if not buf or not _font_covers(buf, text):
                continue
            self._seq += 1
            fontname = f"pdfdt{self._seq}"
            try:
                page.insert_font(fontname=fontname, fontbuffer=buf)
            except Exception:
                continue
            return fontname
        return None

    @staticmethod
    def _fallback(span, text: str) -> str:
        base = (span.get("font") or "").lower()
        if _has_cjk(text):
            if any(k in base for k in ("song", "sun", "ming", "mincho", "batang", "serif")):
                return "china-ss"
            return "china-s"

        if "symbol" in base:
            return "symb"
        if "zapf" in base or "dingbat" in base:
            return "zadb"

        if "courier" in base or "mono" in base:
            family = "cour"
        elif any(
            k in base
            for k in ("times", "serif", "georgia", "song", "sun", "ming", "mincho", "batang")
        ):
            family = "tiro"
        else:
            family = "helv"

        flags = span.get("flags", 0)
        bold, italic = bool(flags & 16), bool(flags & 2)
        suffix = "bi" if (bold and italic) else "bo" if bold else "it" if italic else ""
        if family == "cour" and suffix == "bi":
            suffix = "bo"
        return family + suffix


def _apply_text_layer(
    page, resolver: _FontResolver, target: date, blockers: list[pymupdf.Rect]
) -> tuple[int, int]:
    """擦除并重绘正文文字层中的日期，返回 (替换日期处数, 涉及 span 数)。

    整段日期一次性擦除、一次性重绘，保证复制/搜索时日期是连续的字符串。
    擦除矩形按“相交即删”生效，可能连带删掉紧邻且字符框与之重叠的字符
    （典型是全角标点），因此先记下这些「受害者」，擦除后原位补回。
    """
    spans = [
        s for s in _raw_spans(page) if not _covered_by(pymupdf.Rect(s["bbox"]), blockers)
    ]

    redacts: list[pymupdf.Rect] = []
    draws: list[tuple[tuple, str, dict]] = []
    victims: dict[tuple, tuple[tuple, str, dict]] = {}
    changed = 0

    for span in spans:
        text = _span_text(span)
        for mt in find_dates(text):
            new_text = mt.render(target)
            if new_text == mt.text:
                continue
            changed += 1
            rect = _range_rect(span, mt.start, mt.end)
            redacts.append(rect)
            draws.append((span["chars"][mt.start]["origin"], new_text, span))
            for other in spans:
                for idx, ch in enumerate(other["chars"]):
                    if other is span and mt.start <= idx < mt.end:
                        continue
                    inter = pymupdf.Rect(ch["bbox"]) & rect
                    if inter.is_empty or inter.is_infinite:
                        continue
                    key = (round(ch["origin"][0], 1), round(ch["origin"][1], 1), ch["c"])
                    victims.setdefault(key, (ch["origin"], ch["c"], other))

    if not draws:
        return 0, 0

    for rect in redacts:
        page.add_redact_annot(rect)
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )

    items = draws + list(victims.values())
    items.sort(key=lambda it: (round(it[0][1], 1), it[0][0]))
    for origin, txt, span in items:
        fontname = resolver.resolve(page, span, txt)
        page.insert_text(
            origin,
            txt,
            fontname=fontname,
            fontsize=span.get("size", 11),
            color=pymupdf.sRGB_to_pdf(span.get("color", 0)),
        )
    return changed, len({id(s) for _, _, s in draws})


# --------------------------------------------------- 表单域 / 注释（对象层）
class _Target:
    kind = ""
    page = 0

    def text(self) -> str:
        raise NotImplementedError

    def label(self) -> str:
        raise NotImplementedError

    def write(self, value: str) -> None:
        raise NotImplementedError


class _FieldTarget(_Target):
    kind = SOURCE_FIELD

    def __init__(self, widget, page: int):
        self.widget = widget
        self.page = page

    def text(self) -> str:
        return self.widget.field_value or ""

    def label(self) -> str:
        return self.widget.field_name or "(未命名字段)"

    def write(self, value: str) -> None:
        self.widget.field_value = value
        self.widget.update()


class _AnnotTarget(_Target):
    kind = SOURCE_ANNOT

    def __init__(self, annot, page: int):
        self.annot = annot
        self.page = page

    def text(self) -> str:
        info = self.annot.info or {}
        return info.get("content", "") or ""

    def label(self) -> str:
        return _annot_type_name(self.annot)

    def write(self, value: str) -> None:
        self.annot.set_info(content=value)
        self.annot.update()


def _annot_type_name(annot) -> str:
    t = annot.type
    if isinstance(t, (list, tuple)) and len(t) == 2:
        return str(t[1])
    return str(t)


def _iter_object_targets(page, pno: int, sources: set[str]):
    if SOURCE_FIELD in sources:
        try:
            widgets = list(page.widgets())
        except Exception:
            widgets = []
        for w in widgets:
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
                yield _FieldTarget(w, pno)

    if SOURCE_ANNOT in sources:
        try:
            annots = list(page.annots())
        except Exception:
            annots = []
        for a in annots:
            if a.type and _annot_type_name(a) == "Widget":
                # 表单域已在 widgets() 中处理，避免重复
                continue
            yield _AnnotTarget(a, pno)


# ------------------------------------------------------------------- 对外接口
def _normalize_sources(sources) -> set[str]:
    if sources is None:
        return set(ALL_SOURCES)
    chosen = set(sources)
    return chosen or set(ALL_SOURCES)


def scan_pdf(path: Path | str, sources=None) -> ScanResult:
    """扫描单个 PDF，返回各层中发现的日期。"""
    path = Path(path)
    wanted = _normalize_sources(sources)
    result = ScanResult(path=path)
    try:
        doc = _open(path)
    except Exception as exc:
        result.error = str(exc)
        return result

    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            blockers = _object_rects(page)
            if SOURCE_TEXT in wanted:
                result.hits.extend(_scan_text_layer(page, pno, blockers))
            for target in _iter_object_targets(page, pno, wanted):
                text = target.text()
                matches = find_dates(text)
                if matches:
                    result.hits.append(
                        Hit(
                            source=target.kind,
                            page=pno + 1,
                            label=target.label(),
                            text=text,
                            dates=[m.text for m in matches],
                        )
                    )
    except Exception as exc:
        result.error = str(exc)
    finally:
        doc.close()
    return result


def apply_to_pdf(src: Path | str, dst: Path | str, target: date, sources=None) -> ApplyResult:
    """把 src 中各层的日期改写为 target，另存为 dst。"""
    src, dst = Path(src), Path(dst)
    wanted = _normalize_sources(sources)
    result = ApplyResult(path=src)
    try:
        doc = _open(src)
    except Exception as exc:
        result.error = str(exc)
        return result

    try:
        resolver = _FontResolver(doc)
        for pno in range(doc.page_count):
            page = doc[pno]
            blockers = _object_rects(page)

            if SOURCE_TEXT in wanted:
                n, _spans = _apply_text_layer(page, resolver, target, blockers)
                result.replaced += n
                result.texts += n

            for t in _iter_object_targets(page, pno, wanted):
                new_text, changed = replace_dates(t.text(), target)
                if not changed:
                    continue
                t.write(new_text)
                result.replaced += len(changed)
                if t.kind == SOURCE_FIELD:
                    result.fields += 1
                else:
                    result.annots += 1

        dst.parent.mkdir(parents=True, exist_ok=True)
        # 重绘用的系统字体是全量字库（十几 MB），保存前子集化，只保留用到的字形。
        try:
            doc.subset_fonts()
        except Exception:
            pass
        doc.save(str(dst), garbage=4, deflate=True)
        result.output = dst
    except Exception as exc:
        result.error = str(exc)
    finally:
        doc.close()
    return result