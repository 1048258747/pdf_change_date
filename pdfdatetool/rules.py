"""查找/替换规则。

在任意文本（正文 span、表单域、注释）中定位用户指定的文字，并给出替换结果。
与日期模块相互独立：日期按「原格式」渲染，文字替换按用户给的原文替换。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ReplaceRule", "RuleMatch", "find_rule_matches", "apply_rules", "count_rule_matches"]


@dataclass(frozen=True)
class ReplaceRule:
    """一条「查找 → 替换」规则。"""

    find: str
    replace: str
    case_sensitive: bool = True

    def __post_init__(self) -> None:
        if not self.find:
            raise ValueError("查找内容不能为空")

    @property
    def is_noop(self) -> bool:
        return self.find == self.replace


@dataclass(frozen=True)
class RuleMatch:
    """一处规则命中。"""

    start: int
    end: int
    text: str
    new_text: str


def find_rule_matches(text: str, rules: list[ReplaceRule]) -> list[RuleMatch]:
    """返回 text 中所有规则命中，互不重叠，按位置排序。

    同一位置有多条规则命中时，优先保留「查找内容更长」的那条，
    避免短词把长词切碎（例如同时配了「发票」和「发票号码」）。
    """
    if not text or not rules:
        return []

    found: list[RuleMatch] = []
    for rule in rules:
        if rule.is_noop:
            continue
        if rule.case_sensitive:
            hay, needle = text, rule.find
        else:
            hay, needle = text.lower(), rule.find.lower()
        start = 0
        while True:
            i = hay.find(needle, start)
            if i < 0:
                break
            found.append(RuleMatch(i, i + len(needle), text[i : i + len(needle)], rule.replace))
            start = i + len(needle)

    found.sort(key=lambda m: (m.start, -(m.end - m.start)))
    result: list[RuleMatch] = []
    last_end = -1
    for m in found:
        if m.start < last_end:
            continue
        result.append(m)
        last_end = m.end
    return result


def apply_rules(text: str, rules: list[ReplaceRule]) -> tuple[str, list[str]]:
    """把 text 按规则改写，返回 (新文本, 被替换掉的原文列表)。"""
    matches = find_rule_matches(text, rules)
    if not matches:
        return text, []

    parts: list[str] = []
    changed: list[str] = []
    cursor = 0
    for m in matches:
        parts.append(text[cursor : m.start])
        parts.append(m.new_text)
        cursor = m.end
        changed.append(m.text)
    parts.append(text[cursor:])
    return "".join(parts), changed


def count_rule_matches(text: str, rules: list[ReplaceRule]) -> int:
    return len(find_rule_matches(text, rules))