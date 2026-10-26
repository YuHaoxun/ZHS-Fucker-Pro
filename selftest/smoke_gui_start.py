# -*- coding: utf-8 -*-
"""冒烟测试：真的用 pythonw 起一次 gui.py，确认没在启动阶段就挂掉。

只杀自己启动的那个 PID，不碰用户已经开着的实例。
"""
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = sys.executable

p = subprocess.Popen([PYW, os.path.join(BASE, "gui.py")], cwd=BASE)
time.sleep(4)
alive = p.poll() is None
print("启动 4 秒后仍在运行" if alive else f"进程已退出，退出码 {p.returncode}")
if alive:
    p.terminate()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()
    print("已关闭这个自测实例")
sys.exit(0 if alive else 1)
