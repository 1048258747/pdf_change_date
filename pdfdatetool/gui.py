"""tkinter 桌面界面。

流程：添加文件/文件夹 -> 扫描日期 -> 设定目标日期 -> 批量替换并另存到新目录。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
from datetime import date, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import DISCLAIMER, DISCLAIMER_TITLE, REPO, __version__, ota
from .batch import collect_pdfs, run_batch, scan_many
from .core import SOURCE_ANNOT, SOURCE_FIELD, SOURCE_TEXT, ScanResult
from .rules import ReplaceRule

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "output"

STATUS_PENDING = "未扫描"
STATUS_NODATE = "无日期"
STATUS_ERROR = "读取失败"


def _open_in_explorer(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App(ttk.Frame):
    def __init__(self, master: tk.Tk, *, autoupdate: bool = True):
        super().__init__(master, padding=12)
        self.pack(fill="both", expand=True)

        self.files: list[Path] = []
        self.base_map: dict[Path, Path] = {}
        self.scans: dict[Path, ScanResult] = {}
        self.busy = False
        self._events: queue.Queue = queue.Queue()

        self.out_var = tk.StringVar(value=str(DEFAULT_OUT))
        today = date.today()
        self.year_var = tk.IntVar(value=today.year)
        self.month_var = tk.IntVar(value=today.month)
        self.day_var = tk.IntVar(value=today.day)
        self.only_with_dates = tk.BooleanVar(value=True)
        self.mode_var = tk.StringVar(value="date")
        self.rules: list[ReplaceRule] = []
        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.source_vars = {
            SOURCE_TEXT: tk.BooleanVar(value=True),
            SOURCE_FIELD: tk.BooleanVar(value=True),
            SOURCE_ANNOT: tk.BooleanVar(value=True),
        }

        self._build()
        self.after(120, self._drain_events)
        self.log("准备就绪。先添加 PDF 文件或文件夹，再点「扫描日期」。")
        if autoupdate:
            self.after(1500, lambda: self._start_update_check(silent=True))

    # ---------------------------------------------------------------- 界面
    def _build(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Head.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Warn.TLabel", foreground="#b3541e")

        ttk.Label(self, text="PDF 日期 / 文字修改工具", style="Head.TLabel").pack(anchor="w")
        ttk.Label(
            self,
            text="改写 PDF 中「正文文字 / 表单域 / 注释」里的日期或指定文字，"
                 "保持原有字体与格式，另存到新目录。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 6))
        ttk.Label(
            self,
            text=f"【{DISCLAIMER_TITLE}】仅供技术学习与研究参考，请勿用于任何违法违规用途。",
            style="Warn.TLabel",
        ).pack(anchor="w", pady=(0, 10))

        # --- 文件操作行
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="添加文件夹", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(side="left")
        ttk.Button(bar, text="清空列表", command=self.clear_all).pack(side="left", padx=6)
        self.count_label = ttk.Label(bar, text="共 0 个文件", style="Hint.TLabel")
        self.count_label.pack(side="right")

        # --- 处理模式 + 目标日期 / 查找替换规则
        opts = ttk.LabelFrame(self, text="替换设置", padding=10)
        opts.pack(fill="x", pady=(10, 8))

        mode_row = ttk.Frame(opts)
        mode_row.pack(fill="x")
        ttk.Label(mode_row, text="处理模式").pack(side="left")
        ttk.Radiobutton(mode_row, text="日期替换", value="date", variable=self.mode_var,
                        command=self._on_mode_change).pack(side="left", padx=(12, 0))
        ttk.Radiobutton(mode_row, text="文字替换（查找 → 替换）", value="text",
                        variable=self.mode_var,
                        command=self._on_mode_change).pack(side="left", padx=(8, 0))

        self.mode_body = ttk.Frame(opts)
        self.mode_body.pack(fill="x", pady=(8, 0))

        self.date_frame = ttk.Frame(self.mode_body)
        self.date_frame.pack(fill="x")
        ttk.Label(self.date_frame, text="目标日期").pack(side="left")
        ttk.Spinbox(self.date_frame, from_=1900, to=2200, width=6,
                    textvariable=self.year_var).pack(side="left", padx=(6, 2))
        ttk.Label(self.date_frame, text="年").pack(side="left")
        ttk.Spinbox(self.date_frame, from_=1, to=12, width=4,
                    textvariable=self.month_var).pack(side="left", padx=(6, 2))
        ttk.Label(self.date_frame, text="月").pack(side="left")
        ttk.Spinbox(self.date_frame, from_=1, to=31, width=4,
                    textvariable=self.day_var).pack(side="left", padx=(6, 2))
        ttk.Label(self.date_frame, text="日").pack(side="left")

        ttk.Button(self.date_frame, text="今天", width=6,
                   command=lambda: self._shift(0, 0, 0)).pack(side="left", padx=(14, 4))
        ttk.Button(self.date_frame, text="+1 年", width=6,
                   command=lambda: self._shift(1, 0, 0)).pack(side="left", padx=2)
        ttk.Button(self.date_frame, text="+1 月", width=6,
                   command=lambda: self._shift(0, 1, 0)).pack(side="left", padx=2)
        ttk.Button(self.date_frame, text="+1 天", width=6,
                   command=lambda: self._shift(0, 0, 1)).pack(side="left", padx=2)

        self.text_frame = ttk.Frame(self.mode_body)
        self._build_rules_editor(self.text_frame)

        scope = ttk.Frame(opts)
        scope.pack(fill="x", pady=(8, 0))
        ttk.Label(scope, text="处理范围").pack(side="left")
        for name in (SOURCE_TEXT, SOURCE_FIELD, SOURCE_ANNOT):
            ttk.Checkbutton(scope, text=name, variable=self.source_vars[name]).pack(side="left", padx=(10, 0))
        ttk.Label(scope, text="（扫描图像上的像素日期无法处理）", style="Hint.TLabel").pack(side="right")

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="输出目录").pack(side="left")
        ttk.Entry(row2, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="选择…", command=self.choose_out).pack(side="left")
        ttk.Button(row2, text="打开", command=lambda: self._open_out()).pack(side="left", padx=(6, 0))

        row3 = ttk.Frame(opts)
        row3.pack(fill="x", pady=(8, 0))
        self.only_check = ttk.Checkbutton(
            row3, text="仅处理扫描到日期的文件（推荐，避免无谓复制）",
            variable=self.only_with_dates,
        )
        self.only_check.pack(side="left")
        ttk.Label(row3, text="原文件不会被修改", style="Hint.TLabel").pack(side="right")

        # --- 文件列表
        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        columns = ("name", "dates", "status", "path")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=9)
        for col, text, width, anchor in (
            ("name", "文件名", 260, "w"),
            ("dates", "日期数", 70, "center"),
            ("status", "状态", 110, "center"),
            ("path", "路径", 420, "w"),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "path"))
        vsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.show_detail)

        # --- 执行行
        run = ttk.Frame(self)
        run.pack(fill="x", pady=(10, 4))
        self.scan_btn = ttk.Button(run, text="扫描日期", command=self.do_scan)
        self.scan_btn.pack(side="left")
        self.run_btn = ttk.Button(run, text="开始替换", command=self.do_replace)
        self.run_btn.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(run, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.progress_label = ttk.Label(run, text="", width=14, style="Hint.TLabel")
        self.progress_label.pack(side="right")

        # --- 日志
        log_frame = ttk.LabelFrame(self, text="日志", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled",
                                background="#fbfbfb", relief="flat")
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_vsb.pack(side="right", fill="y")

        # --- 底栏：版本与更新
        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(6, 0))
        self.update_btn = ttk.Button(foot, text="检查更新", command=self.check_updates_manual)
        self.update_btn.pack(side="left")
        ttk.Button(foot, text="项目主页", command=self._open_homepage).pack(side="left", padx=6)
        ttk.Button(foot, text=DISCLAIMER_TITLE, command=self._show_disclaimer).pack(side="left")
        self.version_label = ttk.Label(foot, text=f"当前版本 v{__version__}", style="Hint.TLabel")
        self.version_label.pack(side="right")

        self._on_mode_change()

    def _build_rules_editor(self, parent: ttk.Frame) -> None:
        table = ttk.Frame(parent)
        table.pack(fill="x")
        cols = ("find", "replace")
        self.rule_tree = ttk.Treeview(table, columns=cols, show="headings", height=3)
        for col, text, width in (("find", "查找内容", 330), ("replace", "替换为", 330)):
            self.rule_tree.heading(col, text=text)
            self.rule_tree.column(col, width=width, anchor="w", stretch=True)
        sb = ttk.Scrollbar(table, orient="vertical", command=self.rule_tree.yview)
        self.rule_tree.configure(yscrollcommand=sb.set)
        self.rule_tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")

        form = ttk.Frame(parent)
        form.pack(fill="x", pady=(6, 0))
        ttk.Label(form, text="查找").pack(side="left")
        find_entry = ttk.Entry(form, textvariable=self.find_var, width=24)
        find_entry.pack(side="left", padx=(4, 10))
        find_entry.bind("<Return>", lambda _e: self.add_rule())
        ttk.Label(form, text="替换为").pack(side="left")
        repl_entry = ttk.Entry(form, textvariable=self.replace_var, width=24)
        repl_entry.pack(side="left", padx=(4, 10))
        repl_entry.bind("<Return>", lambda _e: self.add_rule())
        ttk.Button(form, text="添加规则", command=self.add_rule).pack(side="left")
        ttk.Button(form, text="删除选中", command=self.remove_rules).pack(side="left", padx=6)
        ttk.Button(form, text="清空规则", command=self.clear_rules).pack(side="left")

        ttk.Label(
            parent,
            text="查找内容需在同一段连续文字内；替换后同一行右侧内容会自动顺移，字体沿用原件。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _on_mode_change(self) -> None:
        text_mode = self.mode_var.get() == "text"
        self.date_frame.pack_forget()
        self.text_frame.pack_forget()
        (self.text_frame if text_mode else self.date_frame).pack(fill="x")
        self.scan_btn.configure(text="扫描文字" if text_mode else "扫描日期")
        self.tree.heading("dates", text="命中数" if text_mode else "日期数")
        self.only_check.configure(
            text="仅处理包含查找内容的文件（推荐，避免无谓复制）" if text_mode
            else "仅处理扫描到日期的文件（推荐，避免无谓复制）"
        )
        self._rebuild_tree()

    def add_rule(self) -> None:
        find = self.find_var.get()
        repl = self.replace_var.get()
        if not find:
            messagebox.showinfo("提示", "请填写「查找」内容。")
            return
        if find == repl:
            messagebox.showinfo("提示", "查找内容与替换内容相同，无需添加。")
            return
        for i, rule in enumerate(self.rules):
            if rule.find == find:
                self.rules[i] = ReplaceRule(find, repl)
                self.log(f"已更新规则：{find} → {repl}")
                break
        else:
            self.rules.append(ReplaceRule(find, repl))
            self.log(f"已添加规则：{find} → {repl}")
        self.find_var.set("")
        self.replace_var.set("")
        self._refresh_rules()

    def remove_rules(self) -> None:
        selected = sorted((int(i) for i in self.rule_tree.selection()), reverse=True)
        if not selected:
            return
        for i in selected:
            if 0 <= i < len(self.rules):
                del self.rules[i]
        self._refresh_rules()
        self.log("已删除选中的规则。")

    def clear_rules(self) -> None:
        if not self.rules:
            return
        self.rules.clear()
        self._refresh_rules()
        self.log("已清空规则。")

    def _refresh_rules(self) -> None:
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, rule in enumerate(self.rules):
            self.rule_tree.insert("", "end", iid=str(i), values=(rule.find, rule.replace))

    # ---------------------------------------------------------------- 工具
    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_count(self) -> None:
        self.count_label.configure(text=f"共 {len(self.files)} 个文件")

    def _target_date(self) -> date | None:
        try:
            return date(self.year_var.get(), self.month_var.get(), self.day_var.get())
        except (ValueError, tk.TclError):
            return None

    def _sources(self) -> list[str]:
        return [name for name, var in self.source_vars.items() if var.get()]

    def _shift(self, years: int, months: int, days: int) -> None:
        base = self._target_date() or date.today()
        if years == months == days == 0:
            new = date.today()
        else:
            y = base.year + years
            m = base.month + months
            y += (m - 1) // 12
            m = (m - 1) % 12 + 1
            new = date(y, m, min(base.day, 28))
            new = new + timedelta(days=days)
        self.year_var.set(new.year)
        self.month_var.set(new.month)
        self.day_var.set(new.day)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.scan_btn.configure(state=state)
        self.run_btn.configure(state=state)
        self.update_btn.configure(state=state)

    def _show_disclaimer(self) -> None:
        messagebox.showinfo(DISCLAIMER_TITLE, DISCLAIMER)

    # ---------------------------------------------------------------- 文件
    def _add(self, paths: list[Path], base: Path | None) -> None:
        existing = {str(p.resolve()).lower() for p in self.files}
        added = 0
        for p in paths:
            key = str(p.resolve()).lower()
            if key in existing:
                continue
            existing.add(key)
            self.files.append(p)
            if base is not None:
                self.base_map[p] = base
            added += 1
        self._refresh_count()
        self._rebuild_tree()
        if added:
            self.log(f"已添加 {added} 个文件。")

    def add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="选择 PDF 文件", filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
        )
        if chosen:
            self._add([Path(c) for c in chosen], None)

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择包含 PDF 的文件夹")
        if not folder:
            return
        base = Path(folder)
        found = collect_pdfs([base])
        if not found:
            messagebox.showinfo("提示", "该文件夹下没有找到 PDF 文件。")
            return
        self._add(found, base)
        self.log(f"从文件夹 {base} 中收集到 {len(found)} 个 PDF（输出将保留子目录结构）。")

    def remove_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        to_remove = {self.tree.set(iid, "path") for iid in selected}
        keep = []
        for p in self.files:
            if str(p) in to_remove:
                self.base_map.pop(p, None)
                self.scans.pop(p, None)
            else:
                keep.append(p)
        self.files = keep
        self._refresh_count()
        self._rebuild_tree()

    def clear_all(self) -> None:
        self.files.clear()
        self.base_map.clear()
        self.scans.clear()
        self._refresh_count()
        self._rebuild_tree()
        self.log("已清空文件列表。")

    def choose_out(self) -> None:
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.out_var.set(folder)

    def _open_out(self) -> None:
        path = Path(self.out_var.get())
        if path.exists():
            _open_in_explorer(path)
        else:
            messagebox.showinfo("提示", "输出目录还不存在，执行替换后会自动创建。")

    def _rebuild_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        text_mode = self.mode_var.get() == "text"
        for p in self.files:
            scan = self.scans.get(p)
            if scan is None:
                count, status = "-", STATUS_PENDING
            elif scan.error:
                count, status = "-", STATUS_ERROR
            elif scan.count == 0:
                count, status = "0", ("无命中" if text_mode else STATUS_NODATE)
            elif text_mode:
                count, status = str(scan.count), f"命中 {scan.count} 处"
            else:
                count, status = str(scan.count), f"找到 {scan.count} 个日期"
            self.tree.insert("", "end", values=(p.name, count, status, str(p)))

    # ---------------------------------------------------------------- 异步
    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    done, total, label = payload
                    self.progress.configure(value=done / total * 100 if total else 0)
                    self.progress_label.configure(text=f"{done}/{total}")
                elif kind == "scan_done":
                    self._on_scan_done(payload)
                elif kind == "scan_silent":
                    self.scans.update(payload)
                    self._rebuild_tree()
                elif kind == "replace_done":
                    self._on_replace_done(payload)
                elif kind == "update_found":
                    info, silent = payload
                    self._on_update_found(info, silent)
                elif kind == "update_none":
                    self._on_update_none(payload)
                elif kind == "update_error":
                    self._on_update_error(*payload)
                elif kind == "update_progress":
                    done, total = payload
                    if total:
                        self.progress.configure(value=done / total * 100)
                        self.progress_label.configure(text=f"{done * 100 // total}%")
                    else:
                        self.progress_label.configure(text=f"{done // 1024} KB")
                elif kind == "update_ready":
                    self._on_update_ready(payload)
                elif kind == "error":
                    self._set_busy(False)
                    self.log("发生错误：\n" + payload)
                    messagebox.showerror("错误", "任务执行失败，详情见日志。")
        except queue.Empty:
            pass
        self.after(120, self._drain_events)

    def _start(self, work) -> None:
        if self.busy:
            messagebox.showinfo("提示", "当前有任务在执行，请稍候。")
            return
        self._set_busy(True)
        self.progress.configure(value=0)
        self.progress_label.configure(text="")

        def runner():
            try:
                work()
            except Exception:
                self._events.put(("error", traceback.format_exc()))

        threading.Thread(target=runner, daemon=True).start()

    # ---------------------------------------------------------------- 扫描
    def do_scan(self) -> None:
        if not self.files:
            messagebox.showinfo("提示", "请先添加 PDF 文件。")
            return
        sources = self._sources()
        if not sources:
            messagebox.showinfo("提示", "请至少勾选一个「处理范围」。")
            return
        text_mode = self.mode_var.get() == "text"
        rules = None
        if text_mode:
            rules = list(self.rules)
            if not rules:
                messagebox.showinfo("提示", "请先添加至少一条查找/替换规则。")
                return
        files = list(self.files)
        self.log(f"开始扫描 {len(files)} 个文件（范围：{'、'.join(sources)}）…")

        def work():
            def on_progress(i, total, path):
                self._events.put(("progress", (i, total, path.name)))

            results = scan_many(files, on_progress, sources=sources, rules=rules)
            self._events.put(("scan_done", results))

        self._start(work)

    def _on_scan_done(self, results: list[ScanResult]) -> None:
        self._set_busy(False)
        text_mode = self.mode_var.get() == "text"
        total = 0
        for res in results:
            self.scans[res.path] = res
            if res.error:
                self.log(f"  [失败] {res.path.name}: {res.error}")
            elif res.count == 0:
                self.log(f"  [无命中] {res.path.name}")
            else:
                total += res.count
                detail = "；".join(f"{h.label}={h.found}" for h in res.hits)
                self.log(f"  [命中 {res.count} 处] {res.path.name}  →  {detail}")
        self._rebuild_tree()
        self.progress_label.configure(text="")
        self.log(f"扫描完成：共命中 {total} 处。双击列表行可查看明细。")
        if total == 0:
            if text_mode:
                messagebox.showwarning(
                    "未命中",
                    "在勾选的处理范围里没有找到「查找内容」。\n\n"
                    "请确认：\n"
                    "· 查找内容与原文完全一致（区分全角/半角、大小写）\n"
                    "· 该文字在同一段连续文字内，没有跨行或被拆成多段\n"
                    "· 「处理范围」已包含文字所在的位置\n\n"
                    "可用 tools\\inspect_pdf.py 查看 PDF 里的真实文字。",
                )
            else:
                messagebox.showwarning(
                    "未发现日期",
                    "在勾选的处理范围里没有识别到日期。\n\n"
                    "请确认「处理范围」已包含日期所在的位置：\n"
                    "· 正文文字 —— 数字版 PDF（发票、报表）\n"
                    "· 表单域 —— 可填写的表单框\n"
                    "· 注释 —— 批注 / 便签\n\n"
                    "如果日期是印在扫描图像上的像素（纯图片、无可选文字），"
                    "本工具无法改写，需要 OCR + 图像擦除方案。\n"
                    "可用 tools\\inspect_pdf.py 查看日期究竟在哪一层。",
                )

    # ---------------------------------------------------------------- 替换
    def do_replace(self) -> None:
        if not self.files:
            messagebox.showinfo("提示", "请先添加 PDF 文件。")
            return
        text_mode = self.mode_var.get() == "text"
        target = None
        rules = None
        if text_mode:
            rules = list(self.rules)
            if not rules:
                messagebox.showinfo("提示", "请先添加至少一条查找/替换规则。")
                return
        else:
            target = self._target_date()
            if target is None:
                messagebox.showerror("日期无效", "目标日期不是一个有效的日期，请检查年月日。")
                return
        sources = self._sources()
        if not sources:
            messagebox.showinfo("提示", "请至少勾选一个「处理范围」。")
            return

        out_dir = Path(self.out_var.get().strip() or str(DEFAULT_OUT))
        if out_dir.resolve() in {p.resolve() for p in self.files}:
            messagebox.showerror("路径冲突", "输出目录不能与某个源文件相同。")
            return

        files = list(self.files)
        pending = [p for p in files if p not in self.scans]
        if pending:
            self.log(f"检测到 {len(pending)} 个文件尚未扫描，先自动扫描…")

        if text_mode:
            rule_text = "\n".join(f"  {r.find} → {r.replace}" for r in rules)
            head = f"替换规则：\n{rule_text}"
        else:
            head = f"目标日期：{target.isoformat()}"

        if not messagebox.askokcancel(
            "确认替换",
            f"{head}\n"
            f"处理范围：{'、'.join(sources)}\n"
            f"输出目录：{out_dir}\n"
            f"待处理文件：{len(files)} 个\n\n"
            "原文件不会被修改，确认继续？",
        ):
            return

        if text_mode:
            self.log(f"开始文字替换（{len(rules)} 条规则），范围 {'、'.join(sources)}，输出到 {out_dir}")
        else:
            self.log(f"开始替换，目标日期 {target.isoformat()}，范围 {'、'.join(sources)}，输出到 {out_dir}")

        def work():
            scans = dict(self.scans)
            if pending:
                for res in scan_many(pending, sources=sources, rules=rules):
                    scans[res.path] = res
                self._events.put(("scan_silent", scans))

            todo = files
            if self.only_with_dates.get():
                todo = [p for p in files if scans.get(p) and not scans[p].error and scans[p].count > 0]
                skipped = len(files) - len(todo)
                if skipped:
                    self._events.put(("log", f"已跳过 {skipped} 个未命中的文件。"))

            if not todo:
                self._events.put(("log", "没有任何文件包含可替换的内容，任务结束。"))
                self._events.put(("replace_done", None))
                return

            def on_progress(i, total, path):
                self._events.put(("progress", (i, total, path.name)))

            report = run_batch(
                todo, out_dir, target,
                base_map=dict(self.base_map), progress=on_progress,
                sources=sources, rules=rules,
            )
            self._events.put(("replace_done", report))

        self._start(work)

    def _on_replace_done(self, report) -> None:
        self._set_busy(False)
        self.progress_label.configure(text="")
        if report is None:
            return
        for r in report.results:
            if r.ok:
                self.log(f"  [完成] {r.path.name} → 替换 {r.replaced} 处 "
                         f"(正文{r.texts}/表单{r.fields}/注释{r.annots}) → {r.output}")
                for w in r.warnings:
                    self.log(f"    [注意] {w}")
            else:
                self.log(f"  [失败] {r.path.name}: {r.error}")
        self.log(
            f"全部结束：{report.total} 个文件，成功 {report.succeeded}，失败 {report.failed}，"
            f"共替换 {report.replaced} 处。"
        )
        self.progress.configure(value=100)
        warn_total = sum(len(r.warnings) for r in report.results)
        extra = f"\n注意：{warn_total} 行内容超出页面边界，详见日志。" if warn_total else ""
        messagebox.showinfo(
            "完成",
            f"处理完成。\n\n文件总数：{report.total}\n成功：{report.succeeded}\n"
            f"失败：{report.failed}\n替换处数：{report.replaced} 处{extra}\n\n"
            f"输出目录：{self.out_var.get()}",
        )

    # ---------------------------------------------------------------- 更新
    def _open_homepage(self) -> None:
        webbrowser.open(f"https://github.com/{REPO}")

    def check_updates_manual(self) -> None:
        self._start_update_check(silent=False)

    def _start_update_check(self, silent: bool) -> None:
        if self.busy:
            if not silent:
                messagebox.showinfo("提示", "当前有任务在执行，请稍候。")
            return
        if not silent:
            self.log("正在检查更新…")
            self.update_btn.configure(state="disabled")

        def work():
            try:
                info = ota.check_for_update()
            except ota.UpdateError as exc:
                self._events.put(("update_error", (str(exc), silent)))
                return
            if info:
                self._events.put(("update_found", (info, silent)))
            else:
                self._events.put(("update_none", silent))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_found(self, info: ota.UpdateInfo, silent: bool) -> None:
        self.update_btn.configure(state="normal")
        self.log(f"发现新版本 {info.tag}（当前 v{__version__}）。")

        if not ota.is_frozen():
            if messagebox.askyesno(
                "发现新版本",
                f"发现新版本 {info.tag}（当前 v{__version__}）。\n\n"
                "当前以源码方式运行，无法自动替换。\n是否打开下载页面？",
            ):
                webbrowser.open(info.page_url)
            return

        if not info.has_asset:
            if messagebox.askyesno(
                "发现新版本",
                f"发现新版本 {info.tag}（当前 v{__version__}）。\n\n"
                "该 Release 未附带 exe 更新包。\n是否打开下载页面？",
            ):
                webbrowser.open(info.page_url)
            return

        if not messagebox.askyesno(
            "发现新版本",
            f"发现新版本 {info.tag}（当前 v{__version__}）。\n\n"
            f"更新包：{info.asset_name}（{info.size_text}）\n\n"
            "是否立即下载并更新？更新完成后程序会自动重启。",
        ):
            return
        self._do_update(info)

    def _on_update_none(self, silent: bool) -> None:
        self.update_btn.configure(state="normal")
        self.log(f"已是最新版本（v{__version__}）。")
        if not silent:
            messagebox.showinfo("检查更新", f"当前已是最新版本 v{__version__}。")

    def _on_update_error(self, message: str, silent: bool) -> None:
        self._set_busy(False)
        self.progress_label.configure(text="")
        self.log(f"检查更新失败：{message}")
        if not silent:
            messagebox.showwarning("检查更新", f"检查更新失败：\n{message}")

    def _do_update(self, info: ota.UpdateInfo) -> None:
        self._set_busy(True)
        self.progress.configure(value=0)
        self.progress_label.configure(text="")
        self.log(f"开始下载 {info.asset_name}（{info.size_text}）…")
        dest = Path(tempfile.gettempdir()) / str(info.asset_name)

        def work():
            try:
                ota.download(
                    info.asset_url,
                    dest,
                    lambda done, total: self._events.put(("update_progress", (done, total))),
                )
            except ota.UpdateError as exc:
                self._events.put(("update_error", (str(exc), False)))
                return
            self._events.put(("update_ready", dest))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_ready(self, new_exe: Path) -> None:
        self._set_busy(False)
        self.progress.configure(value=100)
        self.progress_label.configure(text="")
        self.log(f"下载完成：{new_exe}")
        try:
            ota.apply_update(new_exe)
        except ota.UpdateError as exc:
            self.log(f"自动替换失败：{exc}")
            messagebox.showerror("更新失败", str(exc))
            return
        self.log("更新程序已启动，本程序即将退出并自动重启。")
        messagebox.showinfo("更新", "更新程序已启动。\n本程序将退出，稍后自动完成替换并重新打开。")
        self.winfo_toplevel().destroy()

    # ---------------------------------------------------------------- 明细
    def show_detail(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        path_str = self.tree.set(selected[0], "path")
        path = Path(path_str)
        scan = self.scans.get(path)
        if scan is None:
            messagebox.showinfo("提示", "该文件尚未扫描，请先点「扫描」。")
            return

        text_mode = self.mode_var.get() == "text"
        win = tk.Toplevel(self)
        win.title(f"命中明细 - {path.name}")
        win.geometry("820x400")
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)

        if scan.error:
            ttk.Label(frame, text=f"读取失败：{scan.error}").pack(anchor="w")
            return

        if not scan.hits:
            ttk.Label(
                frame,
                text="未命中查找内容。" if text_mode else "未在正文文字 / 表单域 / 注释中发现日期。",
            ).pack(anchor="w")
            return

        cols = ("source", "page", "label", "text", "found")
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, text, width in (
            ("source", "来源", 80),
            ("page", "页码", 60),
            ("label", "字段 / 类型", 150),
            ("text", "原内容", 320),
            ("found", "命中内容", 180),
        ):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="w")
        for h in scan.hits:
            tree.insert("", "end", values=(h.source, h.page, h.label, h.text, "、".join(h.found)))
        tree.pack(fill="both", expand=True)


def main() -> int:
    root = tk.Tk()
    root.title(f"PDF 日期 / 文字修改工具 v{__version__}")
    root.geometry("1040x760")
    root.minsize(900, 660)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())