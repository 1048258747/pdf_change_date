@echo off
REM 带控制台的启动方式，出错时能看到报错信息，便于排查。
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 尚未安装依赖，请先双击运行 run.bat。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "%~dp0main.py"
echo.
echo 程序已退出（退出码 %errorlevel%）。
pause