@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [1/2] 首次运行，正在创建虚拟环境...
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo 创建虚拟环境失败。请确认已安装 Python 3.10 或更高版本，并已加入 PATH。
        pause
        exit /b 1
    )
    echo [2/2] 正在安装依赖 PyMuPDF...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0