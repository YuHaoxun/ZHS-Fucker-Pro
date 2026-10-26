@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

echo ==========================================
echo   ZHS Fucker Pro - 首次运行
echo ==========================================
echo.

echo [1/3] 检查 Python ...
where python >nul 2>nul
if errorlevel 1 goto NOPYTHON
python --version
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)"
if errorlevel 1 goto OLDPYTHON

echo [2/3] 安装依赖（首次需要下载，请稍等）...
python -m pip install -r requirements.txt
if errorlevel 1 goto PIPFAIL
echo         依赖已就绪

echo [3/3] 启动图形界面 ...
start "" pythonw.exe gui.py
echo.
echo 完成。若窗口没有弹出来，改用下面这条命令看具体报错：
echo     python gui.py
echo.
timeout /t 5 >nul
exit /b 0

:NOPYTHON
echo.
echo [x] 没有找到 python 命令。
echo     请到 https://www.python.org/downloads/ 安装 Python 3.10 或更高版本，
echo     安装时务必勾选 "Add python.exe to PATH"，然后重新双击本文件。
echo.
pause
exit /b 1

:OLDPYTHON
echo.
echo [x] Python 版本太低，需要 3.10 及以上。当前版本：
python --version
echo.
pause
exit /b 1

:PIPFAIL
echo.
echo [x] 依赖安装失败。常见原因与对策：
echo     - 网络慢或超时，换国内镜像重试：
echo       python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo     - 公司/学校网络有限制，先连手机热点再试
echo.
pause
exit /b 1
