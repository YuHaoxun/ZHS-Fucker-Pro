import os
import sys
import win32com.client

# 全部相对本文件定位，换机器/换路径都不用改
BASE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(BASE, "app.ico")
GUI = os.path.join(BASE, "gui.py")

# 优先用当前解释器同目录的 pythonw.exe（无黑框），没有就退回 PATH 里的 pythonw
_exe_dir = os.path.dirname(os.path.abspath(sys.executable))
_candidate = os.path.join(_exe_dir, "pythonw.exe")
PYW = _candidate if os.path.isfile(_candidate) else "pythonw.exe"

if not os.path.isfile(GUI):
    raise SystemExit(f"找不到 gui.py，请在项目目录内运行本脚本：{GUI}")

desktop = os.path.join(os.path.expanduser("~"), "Desktop")
lnk = os.path.join(desktop, "ZHS Fucker 控制器.lnk")

if os.path.isfile(lnk):
    os.remove(lnk)

shell = win32com.client.Dispatch("WScript.Shell")
sc = shell.CreateShortCut(lnk)
sc.TargetPath = PYW
sc.Arguments = f'"{GUI}"'
sc.WorkingDirectory = BASE
sc.IconLocation = ICON + ",0"
sc.Description = "智慧树自动刷课控制器"
sc.WindowStyle = 1
sc.Save()

print("saved :", lnk)
print("exists:", os.path.isfile(lnk))
print("size  :", os.path.getsize(lnk) if os.path.isfile(lnk) else 0)

# 读回来验证
chk = shell.CreateShortCut(lnk)
print("--- verify ---")
print("Target :", chk.TargetPath)
print("Args   :", chk.Arguments)
print("WorkDir:", chk.WorkingDirectory)
print("Icon   :", chk.IconLocation)
print("Desc   :", chk.Description)
