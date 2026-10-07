"""日期识别与格式化。

在任意文本中定位日期，并按“原格式”渲染替换后的新日期，
例如 "2024/5/1" -> "2025/6/2"，"2024年5月1日" -> "2025年6月2日"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

__all__ = ["DateMatch", "find_dates", "replace_dates", "count_dates"]


@dataclass(frozen=True)
class DateMatch:
    """文本中命中一个日期。"""

    start: int
    end: int
    year: int
    month: int
    day: int
    text: str
    render: Callable[[date], str]


# 2024年5月1日 / 2024 年 05 月 01 日
_CN = re.compile(
    r"(?<!\d)(?P<y>\d{4})(?P<g1>\s*年\s*)(?P<m>\d{1,2})(?P<g2>\s*月\s*)"
    r"(?P<d>\d{1,2})(?P<g3>\s*日)(?!\d)"
)

# 2024-05-01 / 2024/5/1 / 2024.05.01 / 05/01/2024
_NUM = re.compile(
    r"(?<!\d)(?P<a>\d{1,4})(?P<sep>[-/.])(?P<b>\d{1,2})(?P=sep)(?P<c>\d{1,4})(?!\d)"
)

# 20240501
_COMPACT = re.compile(
    r"(?<!\d)(?P<y>(?:19|20)\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])(?!\d)"
)


def _pad(value: int, original: str) -> str:
    """按原字段的补零宽度输出新值：'05' -> '06'，'5' -> '6'。"""
    return str(value).zfill(len(original)) if len(original) > 1 else str(value)


def _valid(year: int, month: int, day: int) -> bool:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def find_dates(text: str) -> list[DateMatch]:
    """返回文本中所有可识别的日期，按出现位置排序，互不重叠。"""
    if not text:
        return []

    found: list[DateMatch] = []

    for m in _CN.finditer(text):
        year, month, day = int(m["y"]), int(m["m"]), int(m["d"])
        if not _valid(year, month, day):
            continue
        g1, g2, g3 = m["g1"], m["g2"], m["g3"]
        om, od = m["m"], m["d"]

        def render(nd: date, g1=g1, g2=g2, g3=g3, om=om, od=od) -> str:
            return f"{nd.year}{g1}{_pad(nd.month, om)}{g2}{_pad(nd.day, od)}{g3}"

        found.append(DateMatch(m.start(), m.end(), year, month, day, m.group(0), render))

    for m in _NUM.finditer(text):
        a, b, c = m["a"], m["b"], m["c"]
        sep = m["sep"]
        if len(a) == 4:
            year, month, day = int(a), int(b), int(c)
            oy, om, od = a, b, c

            def render(nd: date, oy=oy, om=om, od=od, sep=sep) -> str:
                return f"{_pad(nd.year, oy)}{sep}{_pad(nd.month, om)}{sep}{_pad(nd.day, od)}"

        elif len(c) == 4:
            year, month, day = int(c), int(a), int(b)
            oy, om, od = c, a, b

            def render(nd: date, oy=oy, om=om, od=od, sep=sep) -> str:
                return f"{_pad(nd.month, om)}{sep}{_pad(nd.day, od)}{sep}{_pad(nd.year, oy)}"

        else:
            continue

        if not _valid(year, month, day):
            continue
        found.append(DateMatch(m.start(), m.end(), year, month, day, m.group(0), render))

    for m in _COMPACT.finditer(text):
        year, month, day = int(m["y"]), int(m["m"]), int(m["d"])
        if not _valid(year, month, day):
            continue
        om, od = m["m"], m["d"]

        def render(nd: date, om=om, od=od) -> str:
            return f"{nd.year}{_pad(nd.month, om)}{_pad(nd.day, od)}"

        found.append(DateMatch(m.start(), m.end(), year, month, day, m.group(0), render))

    found.sort(key=lambda x: (x.start, -(x.end - x.start)))
    result: list[DateMatch] = []
    last_end = -1
    for item in found:
        if item.start < last_end:
            continue
        result.append(item)
        last_end = item.end
    return result


def replace_dates(text: str, target: date) -> tuple[str, list[str]]:
    """把文本中的日期替换为 target，保持每个日期的原有格式。

    返回 (新文本, 被替换掉的原始日期字符串列表)。
    """
    matches = find_dates(text)
    if not matches:
        return text, []

    parts: list[str] = []
    changed: list[str] = []
    cursor = 0
    for mt in matches:
        parts.append(text[cursor:mt.start])
        parts.append(mt.render(target))
        cursor = mt.end
        changed.append(mt.text)
    parts.append(text[cursor:])
    return "".join(parts), changed


def count_dates(text: str) -> int:
    return len(find_dates(text))