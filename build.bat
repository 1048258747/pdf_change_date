@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 首次打包，正在创建虚拟环境...
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo 创建虚拟环境失败。请确认已安装 Python 3.10 或更高版本，并已加入 PATH。
        pause
        exit /b 1
    )
)

echo [2/3] 安装运行与打包依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt -q
if errorlevel 1 (
    echo.
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo [3/3] 开始打包（首次较慢，请耐心等待）...
".venv\Scripts\python.exe" build_exe.py
echo.
if exist "dist\PDFDateTool.exe" (
    echo 打包完成：dist\PDFDateTool.exe
) else (
    echo 打包失败，请查看上方日志。
)
pause