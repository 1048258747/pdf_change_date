"""GUI 冒烟测试：构建界面、填充列表、打开明细窗口、切换目标日期。

运行：
    .venv\\Scripts\\python.exe tools\\gui_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

from pdfdatetool.batch import collect_pdfs  # noqa: E402
from pdfdatetool.core import scan_pdf  # noqa: E402
from pdfdatetool.gui import App  # noqa: E402


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    app = App(root, autoupdate=False)
    root.update()
    print("界面构建成功")

    samples = ROOT / "samples"
    files = collect_pdfs([samples])
    app._add(files, samples)
    root.update()
    print(f"已加入 {len(files)} 个文件，列表行数 = {len(app.tree.get_children())}")

    for f in files:
        app.scans[f] = scan_pdf(f)
    app._rebuild_tree()
    root.update()

    print("列表内容：")
    for iid in app.tree.get_children():
        print("   ", app.tree.item(iid, "values"))

    rows = app.tree.get_children()
    if rows:
        app.tree.selection_set(rows[0])
        app.show_detail()
        root.update()
        print("明细窗口打开成功")

    app._shift(1, 0, 0)
    root.update()
    print(f"+1 年后的目标日期 = {app._target_date()}")

    app._shift(0, 0, 0)
    root.update()
    print(f"「今天」后的目标日期 = {app._target_date()}")

    root.destroy()
    print("GUI 冒烟测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())