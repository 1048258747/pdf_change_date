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
from .rules import ReplaceRule, apply_rules, find_rule_matches

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
    "scan_pdf_text",
    "apply_rules_to_pdf",
]


@dataclass
class Hit:
    """一处命中：某个文本容器里发现了日期或查找规则命中的文字。"""

    source: str
    page: int
    label: str
    text: str
    dates: list[str] = dc_field(default_factory=list)
    matches: list[str] = dc_field(default_factory=list)

    @property
    def found(self) -> list[str]:
        """命中的内容（日期模式下是日期，文字模式下是匹配到的原文）。"""
        return self.dates if self.dates else self.matches


@dataclass
class ScanResult:
    path: Path
    hits: list[Hit] = dc_field(default_factory=list)
    error: str | None = None

    @property
    def count(self) -> int:
        return sum(len(h.found) for h in self.hits)

    @property
    def date_count(self) -> int:
        """兼容旧名：日期模式下的日期总数。"""
        return self.count


@dataclass
class ApplyResult:
    path: Path
    output: Path | None = None
    replaced: int = 0
    texts: int = 0
    fields: int = 0
    annots: int = 0
    error: str | None = None
    warnings: list[str] = dc_field(default_factory=list)

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


def _make_font(buf: bytes) -> pymupdf.Font | None:
    try:
        return pymupdf.Font(fontbuffer=buf)
    except Exception:
        return None


def _font_covers(font: pymupdf.Font, text: str) -> bool:
    """字体是否包含 text 中所有字符的字形。"""
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


def _line_spans(page):
    """按行产出 (line, spans)，同一行内的 span 按 x 排序。"""
    try:
        data = page.get_text("rawdict")
    except Exception:
        return
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("chars")]
            if spans:
                yield line, sorted(spans, key=lambda s: s["bbox"][0])


def _is_horizontal(line) -> bool:
    """只处理水平书写的行；重绘不支持旋转文字，遇到就跳过。"""
    d = line.get("dir")
    if not d:
        return True
    try:
        return abs(float(d[0]) - 1.0) < 1e-3 and abs(float(d[1])) < 1e-3
    except (TypeError, ValueError):
        return False


def _extent(chars, start: int, end: int) -> float:
    """chars[start:end] 在 x 方向实际占用的宽度（按原件排布计算）。"""
    if start >= end or start >= len(chars):
        return 0.0
    x0 = chars[start]["origin"][0]
    if end < len(chars):
        x1 = chars[end]["origin"][0]
    else:
        x1 = max(ch["bbox"][2] for ch in chars[start:end])
    return max(0.0, x1 - x0)


def _rewrite_part(text: str, matches, start: int) -> str:
    """把 text[start:] 按 matches 改写（matches 都落在该 span 内）。"""
    part = text[start:]
    out: list[str] = []
    cursor = 0
    for m in matches:
        if m.start < start:
            continue
        out.append(part[cursor : m.start - start])
        out.append(m.new_text)
        cursor = m.end - start
    out.append(part[cursor:])
    return "".join(out)


def _char_key(ch) -> tuple:
    return (round(ch["origin"][0], 1), round(ch["origin"][1], 1), ch["c"])


# 判定「同一视觉行」的两个阈值：
# 基线差不超过 1pt 视为同一行；相邻 span 间距不超过 6pt 视为连续文字。
# 实测常见 PDF 里「标签→值」间距约 0~1pt，而表格列间距普遍 ≥17pt，
# 因此这样切分既能让值跟着标签位移，又不会把整行表格一起挪动。
_BASELINE_TOL = 1.0
_GAP_MAX = 6.0


def _editable_spans(page, blockers: list[pymupdf.Rect]) -> list[dict]:
    """页面上可编辑的正文 span：水平书写，且不在表单域/注释的显示区域内。"""
    out: list[dict] = []
    for line, spans in _line_spans(page):
        if not _is_horizontal(line):
            continue
        for span in spans:
            if _covered_by(pymupdf.Rect(span["bbox"]), blockers):
                continue
            out.append(span)
    return out


def _visual_lines(spans: list[dict]) -> list[list[dict]]:
    """把 span 归成「视觉行」。

    很多 PDF 会把同一视觉行上的每段文字导出成独立的 text line（例如
    「发票号码：」和号码本身），只按 rawdict 的行分组会漏掉它们。这里先按
    基线聚簇，再按较小水平间距切成连续片段——标签和值会连成一组，
    间距较大的表格单元格则保持独立。
    """
    ordered = sorted(spans, key=lambda s: (s["chars"][0]["origin"][1], s["bbox"][0]))
    rows: list[list[dict]] = []
    for span in ordered:
        y = span["chars"][0]["origin"][1]
        if rows and abs(rows[-1][0]["chars"][0]["origin"][1] - y) <= _BASELINE_TOL:
            rows[-1].append(span)
        else:
            rows.append([span])

    lines: list[list[dict]] = []
    for row in rows:
        row.sort(key=lambda s: s["bbox"][0])
        current = [row[0]]
        for prev, cur in zip(row, row[1:]):
            if cur["bbox"][0] - prev["bbox"][2] <= _GAP_MAX:
                current.append(cur)
            else:
                lines.append(current)
                current = [cur]
        lines.append(current)
    return lines


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
    """把 span 的字体还原成可用于重绘的字体名，并可按键字体重宽。

    字体来源（内嵌 / 系统同名 / 内置）与「注册到页面」是分开的两步：
    量宽只需要字体文件本身，注册要等 apply_redactions 之后再做——擦除会重建
    页面资源，提前注册的字体名会失效。
    """

    def __init__(self, doc):
        self.doc = doc
        self._sources: dict[tuple, tuple] = {}
        self._fonts: dict[tuple, pymupdf.Font | None] = {}
        self._names: dict[tuple, str] = {}
        self._seq = 0

    @staticmethod
    def _key(page, span, text: str) -> tuple:
        return (page.xref, span.get("font") or "", _has_cjk(text))

    def measure(self, page, span, text: str, size: float) -> float | None:
        """按将要使用的字体重绘 text 时的宽度；量不出来时返回 None。"""
        key = self._key(page, span, text)
        if key not in self._fonts:
            self._fonts[key] = self._make(self._source(page, span, text))
        font = self._fonts[key]
        if font is None:
            return None
        try:
            return float(font.text_length(text, size))
        except Exception:
            return None

    def resolve(self, page, span, text: str) -> str:
        """把字体注册到页面并返回可传给 insert_text 的字体名。"""
        key = self._key(page, span, text)
        cached = self._names.get(key)
        if cached:
            return cached

        source = self._source(page, span, text)
        self._seq += 1
        fontname = f"pdfdt{self._seq}"
        try:
            if source[0] == "buffer":
                page.insert_font(fontname=fontname, fontbuffer=source[1])
            elif source[0] == "file":
                page.insert_font(fontname=fontname, fontfile=str(source[1]))
            else:
                fontname = source[1]  # 内置字体直接用自带名字
        except Exception:
            fontname = source[1] if source[0] == "builtin" else "helv"
        self._names[key] = fontname
        return fontname

    def _source(self, page, span, text: str) -> tuple:
        key = self._key(page, span, text)
        cached = self._sources.get(key)
        if cached is None:
            cached = (
                self._embedded_source(page, span, text)
                or self._system_source(span)
                or self._builtin_source(span, text)
            )
            self._sources[key] = cached
        return cached

    @staticmethod
    def _make(source: tuple | None) -> pymupdf.Font | None:
        if not source:
            return None
        try:
            if source[0] == "buffer":
                return pymupdf.Font(fontbuffer=source[1])
            if source[0] == "file":
                return pymupdf.Font(fontfile=str(source[1]))
            return pymupdf.Font(source[1])
        except Exception:
            return None

    def _embedded_source(self, page, span, text: str) -> tuple | None:
        """复用 PDF 内嵌的字体——前提是它真的含有本次要绘制的所有字形。

        子集化过的内嵌字体只包含文档实际用到的字形，缺字时重绘会变成空白，
        因此这里逐个字符检查字形覆盖。
        """
        base = span.get("font") or ""
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
            if not buf:
                continue
            font = _make_font(buf)
            if font is None or not _font_covers(font, text):
                continue
            return ("buffer", buf)
        return None

    @staticmethod
    def _system_source(span) -> tuple | None:
        """用系统里的同名中文字体重绘，宽度与字形最接近原件。"""
        path = _system_font_file(span.get("font") or "")
        return ("file", path) if path else None

    @staticmethod
    def _builtin_source(span, text: str) -> tuple:
        base = (span.get("font") or "").lower()
        if _has_cjk(text):
            name = "china-ss" if any(
                k in base for k in ("song", "sun", "ming", "mincho", "batang", "serif")
            ) else "china-s"
        elif "symbol" in base:
            name = "symb"
        elif "zapf" in base or "dingbat" in base:
            name = "zadb"
        else:
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
            name = family + suffix
        return ("builtin", name)


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


def _apply_text_layer_shift(
    page,
    resolver: _FontResolver,
    rules: list[ReplaceRule],
    blockers: list[pymupdf.Rect],
    pno: int = 0,
) -> tuple[int, int, list[str]]:
    """按查找/替换规则改写正文，并把同一行的后续文字整体顺移。

    一行里只要有一处改动，就把「第一处改动 → 行尾」整段擦除后重绘：新文字比
    原文宽就右移、窄就左移，后面的内容不会压字也不会留空。重绘时字体仍取自
    span 自身的 font / size / color，所以字形与原件保持一致。

    返回 (替换处数, 涉及行数, 告警列表)。
    """
    all_spans = _editable_spans(page, blockers)
    if not all_spans:
        return 0, 0, []

    redacts: list[pymupdf.Rect] = []
    draws: list[tuple[tuple, str, dict]] = []
    keep: set[tuple] = set()
    warnings: list[str] = []
    changed = 0
    lines_touched = 0

    for allowed in _visual_lines(all_spans):
        if not allowed:
            continue

        per_span: dict[int, list] = {}
        for si, span in enumerate(allowed):
            text = _span_text(span)
            hits = [m for m in find_rule_matches(text, rules) if m.new_text != m.text]
            if hits:
                per_span[si] = hits
        if not per_span:
            continue

        first_si = min(per_span)
        first_start = per_span[first_si][0].start
        changed += sum(len(v) for v in per_span.values())
        lines_touched += 1

        # 先算出「第一处改动 → 行尾」每一段的位移，再统一擦除重绘
        parts: list[tuple[dict, int, str, float, float]] = []
        shift = 0.0
        for si in range(first_si, len(allowed)):
            span = allowed[si]
            chars = span["chars"]
            text = _span_text(span)
            start = first_start if si == first_si else 0
            old_part = text[start:]
            new_part = _rewrite_part(text, per_span.get(si, []), start)
            old_w = _extent(chars, start, len(chars))
            new_w = resolver.measure(page, span, new_part, span.get("size", 11))
            if new_w is None:
                # 量不出宽度时按字符数等比估算，避免整行错位
                new_w = old_w * (len(new_part) / len(old_part)) if old_part else 0.0
            parts.append((span, start, new_part, shift, chars[start]["origin"][1]))
            shift += new_w - old_w

        last_span, last_start, *_ = parts[-1]
        last_right = max(ch["bbox"][2] for ch in last_span["chars"][last_start:])
        overflow = last_right + shift - page.rect.width
        if overflow > 0.5:
            src_text = per_span[first_si][0].text
            warnings.append(
                f"第 {pno + 1} 页「{src_text}」替换后超出页面右边界 {overflow:.1f}pt，"
                "右侧内容可能被裁切，建议缩短替换文字"
            )

        rect = None
        for span, start, _new, _dx, _y in parts:
            for ch in span["chars"][start:]:
                r = pymupdf.Rect(ch["bbox"])
                rect = r if rect is None else (rect | r)
                keep.add(_char_key(ch))
        for span, start, new_part, dx, y in parts:
            if not new_part:
                continue
            origin = (span["chars"][start]["origin"][0] + dx, y)
            draws.append((origin, new_part, span))
        if rect is not None and not rect.is_empty:
            redacts.append(rect)

    if not draws:
        return 0, 0, warnings

    victims: dict[tuple, tuple[tuple, str, dict]] = {}
    for rect in redacts:
        for other in all_spans:
            for ch in other["chars"]:
                key = _char_key(ch)
                if key in keep:
                    continue
                inter = pymupdf.Rect(ch["bbox"]) & rect
                if inter.is_empty or inter.is_infinite:
                    continue
                victims.setdefault(key, (ch["origin"], ch["c"], other))

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
    return changed, lines_touched, warnings


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


def _clean_rules(rules) -> list[ReplaceRule]:
    return [r for r in rules if r.find and not r.is_noop]


def scan_pdf_text(path: Path | str, rules, sources=None) -> ScanResult:
    """扫描单个 PDF，返回各层中命中查找规则的文字。"""
    path = Path(path)
    wanted = _normalize_sources(sources)
    result = ScanResult(path=path)
    rules = _clean_rules(rules)
    if not rules:
        result.error = "没有可用的查找/替换规则"
        return result

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
                for span in _raw_spans(page):
                    if _covered_by(pymupdf.Rect(span["bbox"]), blockers):
                        continue
                    text = _span_text(span)
                    hits = [m for m in find_rule_matches(text, rules) if m.new_text != m.text]
                    if hits:
                        label = f"{span.get('font', '?')} {span.get('size', 0):.1f}pt"
                        result.hits.append(
                            Hit(SOURCE_TEXT, pno + 1, label, text, matches=[m.text for m in hits])
                        )
            for target in _iter_object_targets(page, pno, wanted):
                text = target.text()
                hits = [m for m in find_rule_matches(text, rules) if m.new_text != m.text]
                if hits:
                    result.hits.append(
                        Hit(
                            source=target.kind,
                            page=pno + 1,
                            label=target.label(),
                            text=text,
                            matches=[m.text for m in hits],
                        )
                    )
    except Exception as exc:
        result.error = str(exc)
    finally:
        doc.close()
    return result


def apply_rules_to_pdf(src: Path | str, dst: Path | str, rules, sources=None) -> ApplyResult:
    """把 src 中命中规则的文字替换掉，另存为 dst。"""
    src, dst = Path(src), Path(dst)
    wanted = _normalize_sources(sources)
    result = ApplyResult(path=src)
    rules = _clean_rules(rules)
    if not rules:
        result.error = "没有可用的查找/替换规则"
        return result

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
                n, _lines, warns = _apply_text_layer_shift(
                    page, resolver, rules, blockers, pno
                )
                result.replaced += n
                result.texts += n
                result.warnings.extend(warns)

            for t in _iter_object_targets(page, pno, wanted):
                new_text, changed = apply_rules(t.text(), rules)
                if not changed:
                    continue
                t.write(new_text)
                result.replaced += len(changed)
                if t.kind == SOURCE_FIELD:
                    result.fields += 1
                else:
                    result.annots += 1

        dst.parent.mkdir(parents=True, exist_ok=True)
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