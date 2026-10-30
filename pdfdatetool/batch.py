"""批量处理：收集文件、统一替换日期、另存到输出目录。"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from .core import (
    ApplyResult,
    ScanResult,
    apply_rules_to_pdf,
    apply_to_pdf,
    scan_pdf,
    scan_pdf_text,
)

__all__ = ["collect_pdfs", "BatchReport", "run_batch", "scan_many"]


def collect_pdfs(paths: Iterable[Path | str]) -> list[Path]:
    """把文件与文件夹混合的输入展开为去重后的 PDF 文件列表。"""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf":
            files.append(p)

    seen: set[str] = set()
    result: list[Path] = []
    for p in files:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(p)
    return result


def scan_many(
    files: Iterable[Path],
    progress: Callable[[int, int, Path], None] | None = None,
    sources=None,
    rules=None,
) -> list[ScanResult]:
    """扫描一批文件。给了 rules 就按「查找/替换规则」扫描，否则扫描日期。"""
    files = list(files)
    results: list[ScanResult] = []
    for i, f in enumerate(files, 1):
        if rules:
            results.append(scan_pdf_text(f, rules=rules, sources=sources))
        else:
            results.append(scan_pdf(f, sources=sources))
        if progress:
            progress(i, len(files), f)
    return results


@dataclass
class BatchReport:
    results: list[ApplyResult] = dc_field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def touched(self) -> int:
        return sum(1 for r in self.results if r.ok and r.replaced > 0)

    @property
    def replaced(self) -> int:
        return sum(r.replaced for r in self.results)


def _output_path(src: Path, out_dir: Path, base_dir: Path | None, used: set[str]) -> Path:
    if base_dir is not None:
        try:
            rel = src.resolve().relative_to(Path(base_dir).resolve())
        except ValueError:
            rel = Path(src.name)
    else:
        rel = Path(src.name)

    candidate = out_dir / rel
    key = str(candidate.resolve()).lower()
    if key in used:
        stem, suffix, parent = candidate.stem, candidate.suffix, candidate.parent
        n = 2
        while True:
            candidate = parent / f"{stem} ({n}){suffix}"
            key = str(candidate.resolve()).lower()
            if key not in used:
                break
            n += 1
    used.add(key)
    return candidate


def run_batch(
    files: Iterable[Path],
    out_dir: Path | str,
    target: date | None = None,
    base_dir: Path | str | None = None,
    base_map: dict[Path, Path] | None = None,
    progress: Callable[[int, int, Path], None] | None = None,
    sources=None,
    rules=None,
) -> BatchReport:
    """对一批 PDF 另存到 out_dir。

    给了 rules 就按查找/替换规则改写文字，否则把日期统一改写为 target。
    base_map 可为每个源文件单独指定基准目录，用于在输出目录中保留子目录结构。
    """
    files = list(files)
    out_dir = Path(out_dir)
    report = BatchReport()
    used: set[str] = set()

    for i, src in enumerate(files, 1):
        base = (base_map or {}).get(Path(src))
        if base is None and base_dir:
            base = Path(base_dir)
        dst = _output_path(src, out_dir, base, used)
        if rules:
            report.results.append(apply_rules_to_pdf(src, dst, rules, sources=sources))
        else:
            report.results.append(apply_to_pdf(src, dst, target, sources=sources))
        if progress:
            progress(i, len(files), src)

    return report