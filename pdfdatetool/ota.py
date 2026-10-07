"""OTA 自动更新。

流程：查询 GitHub Release 的最新版本 -> 与本地 __version__ 比对 ->
下载 Release 里附带的 exe -> 用批处理在程序退出后就地替换并重启。

只用标准库，不引入额外依赖。仅当程序以打包后的 exe 运行时才能自动替换；
源码方式运行时会退化为打开下载页面。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import REPO, __version__

__all__ = [
    "UpdateInfo",
    "UpdateError",
    "parse_version",
    "is_newer",
    "current_version",
    "is_frozen",
    "executable_path",
    "check_for_update",
    "download",
    "apply_update",
]

_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_UA = f"PDFDateTool/{__version__} (+https://github.com/{REPO})"
_TIMEOUT = 10.0
_CHUNK = 64 * 1024


class UpdateError(RuntimeError):
    """更新过程中的可预期错误（网络、解析、替换失败）。"""


@dataclass(frozen=True)
class UpdateInfo:
    """一个可用的新版本。"""

    version: str
    tag: str
    notes: str
    page_url: str
    published_at: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int = 0

    @property
    def has_asset(self) -> bool:
        return bool(self.asset_url)

    @property
    def size_text(self) -> str:
        if not self.asset_size:
            return "未知大小"
        return f"{self.asset_size / 1024 / 1024:.1f} MB"


def current_version() -> str:
    return __version__


def parse_version(text: str) -> tuple[int, ...]:
    """把 "v1.2.3" / "1.2" 之类的字符串解析成数字元组。"""
    parts = re.findall(r"\d+", str(text or ""))
    return tuple(int(p) for p in parts) if parts else (0,)


def is_newer(candidate: str, base: str) -> bool:
    """candidate 是否比 base 更新（按段比较，长度不同时补 0）。"""
    a, b = parse_version(candidate), parse_version(base)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path | None:
    """当前 exe 的路径；源码运行时返回 None。"""
    return Path(sys.executable) if is_frozen() else None


def _request(url: str, timeout: float) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"},
    )


def check_for_update(timeout: float = _TIMEOUT) -> UpdateInfo | None:
    """查询最新 Release。有新版本返回 UpdateInfo，否则返回 None。

    网络或接口异常时抛出 UpdateError。
    """
    try:
        with urllib.request.urlopen(_request(_API, timeout), timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError(f"GitHub 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise UpdateError(f"网络请求失败：{exc}") from exc

    tag = str(data.get("tag_name") or "").strip()
    version = tag.lstrip("vV") or str(data.get("name") or "").strip()
    if not version or not is_newer(version, __version__):
        return None

    asset_name = asset_url = None
    asset_size = 0
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".exe"):
            asset_name = name
            asset_url = str(asset.get("browser_download_url") or "") or None
            asset_size = int(asset.get("size") or 0)
            break

    return UpdateInfo(
        version=version,
        tag=tag or f"v{version}",
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or f"https://github.com/{REPO}/releases"),
        published_at=str(data.get("published_at") or ""),
        asset_name=asset_name,
        asset_url=asset_url,
        asset_size=asset_size,
    )


def download(
    url: str,
    dest: Path | str,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 60.0,
) -> Path:
    """下载 url 到 dest，progress(已下载字节, 总字节) 可用于显示进度。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"下载失败：{exc}") from exc
    return dest


def apply_update(new_exe: Path | str, target: Path | str | None = None) -> Path:
    """安排「退出后替换并重启」，返回临时批处理脚本路径。

    调用方应在返回后尽快退出进程，让脚本能覆盖被占用的 exe。
    仅支持 Windows + 打包后的 exe。
    """
    new_exe = Path(new_exe)
    if not new_exe.exists():
        raise UpdateError(f"更新包不存在：{new_exe}")

    target = Path(target) if target else executable_path()
    if target is None:
        raise UpdateError("当前以源码方式运行，没有可替换的 exe。")
    if sys.platform != "win32":
        raise UpdateError("自动替换目前只支持 Windows。")
    if not target.exists():
        raise UpdateError(f"找不到当前程序：{target}")

    script = Path(tempfile.gettempdir()) / f"pdfdate_update_{os.getpid()}.bat"
    text = (
        "@echo off\r\n"
        "setlocal\r\n"
        ":retry\r\n"
        f'copy /y "{new_exe}" "{target}" >nul 2>&1\r\n'
        "if errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto retry\r\n"
        ")\r\n"
        f'start "" "{target}"\r\n'
        'del "%~f0"\r\n'
    )
    try:
        script.write_bytes(text.encode("mbcs"))
    except UnicodeEncodeError:
        script.write_bytes(text.encode("utf-8"))

    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True)
    return script