"""用 PyInstaller 把工具打包成单文件 exe（dist/PDFDateTool.exe）。

版本号取自 pdfdatetool.__version__，同时写进 exe 的文件属性，保证
「关于/属性」里看到的版本和 OTA 比对用的版本一致。

运行：
    .venv\\Scripts\\python.exe build_exe.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pdfdatetool import DISCLAIMER_ONELINE, __version__  # noqa: E402

NAME = "PDFDateTool"
ICON = ROOT / "assets" / "app.ico"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ENTRY = ROOT / "main.py"

_VERSION_INFO_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'080404b0',
          [StringStruct(u'CompanyName', u'1048258747'),
          StringStruct(u'FileDescription', u'PDF 日期 / 文字修改工具'),
          StringStruct(u'FileVersion', u'{version}'),
          StringStruct(u'InternalName', u'{name}'),
          StringStruct(u'OriginalFilename', u'{name}.exe'),
          StringStruct(u'ProductName', u'{name}'),
          StringStruct(u'ProductVersion', u'{version}'),
          StringStruct(u'Comments', u'{comments}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def write_version_file() -> Path:
    parts = [int(n) for n in re.findall(r"\d+", __version__)][:4]
    parts += [0] * (4 - len(parts))
    content = _VERSION_INFO_TEMPLATE.format(
        quad="(" + ", ".join(str(p) for p in parts) + ")",
        version=__version__,
        name=NAME,
        comments=DISCLAIMER_ONELINE.replace("'", " "),
    )
    path = BUILD / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    version_file = write_version_file()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        "--version-file", str(version_file),
        "--collect-all", "pymupdf",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
    ]
    if ICON.exists():
        cmd += ["--icon", str(ICON)]
    else:
        print(f"提示：未找到图标 {ICON}，将使用默认图标。")
    cmd.append(str(ENTRY))

    print(f"打包 {NAME} v{__version__} …")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("打包失败。")
        return result.returncode

    exe = DIST / f"{NAME}.exe"
    if not exe.exists():
        print(f"打包结束但没找到 {exe}")
        return 1
    print(f"完成：{exe}（{exe.stat().st_size / 1024 / 1024:.1f} MB）")

    shutil.rmtree(BUILD, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())