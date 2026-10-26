@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python 命令。
    echo 请先安装 Python 3.10 或更高版本，安装时务必勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)
python main.py %*
pause
