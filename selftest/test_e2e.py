# -*- coding: utf-8 -*-
"""离线自测（四）：端到端 —— 真 GUI + 真子进程，只把 main.py 换成假的（不联网）。

验证整条链路：脚本抛暂停标记 -> 界面弹窗 -> 点「我已解完」-> 子进程收到 go -> 继续 -> 收尾。
用法：python selftest\\test_e2e.py
"""
import os
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)

import tkinter as tk
import gui

FAKE = os.path.join(HERE, "_fake_main.py")
OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  [OK] " if cond else "  [!!] ") + name + (("   " + str(extra)) if extra else ""))


with open(FAKE, "w", encoding="utf-8") as f:
    f.write('''# -*- coding: utf-8 -*-
import sys, json
print("Fucking Zhidao course: 测试课", flush=True)
print("Fucking video 视频X |####      |  30.0%  ", end="", flush=True)
print("@@CAPTCHA@@" + json.dumps({"url": "https://fake.example/study?recruitAndCourseId=FAKE",
                               "video": "视频X", "timeout": 60}), flush=True)
line = sys.stdin.readline()
print("GOT:" + line.strip(), flush=True)
if line.strip() == "go":
    print("@@CAPTCHA_OK@@", flush=True)
print("Fucking lesson 第一节 刷完了", flush=True)
''')

webbrowser.open = lambda url, *a, **k: True     # 别真开浏览器
gui.MAIN_PY = FAKE                              # 用假脚本冒充 main.py

root = tk.Tk()
root.withdraw()
app = gui.App(root)


def pump(seconds, stop_when=None):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        if stop_when and stop_when():
            return True
        time.sleep(0.05)
    return False


def cap_win():
    return getattr(app, "_captcha_win", None)


print("== 端到端：脚本暂停 -> 弹窗 -> 点继续 -> 脚本接着跑 ==")
app.run_main([])
pump(2, stop_when=lambda: app.proc is not None)
check("子进程起来了", app.proc is not None)

appeared = pump(8, stop_when=lambda: cap_win() is not None)
check("检测到暂停并弹出验证码窗口", appeared)
if appeared:
    app.captcha_continue()

finished = pump(8, stop_when=lambda: app.proc is None)
check("子进程正常退出", finished)

log = app.log_text.get("1.0", "end")
check("脚本确实收到 go", "GOT:go" in log)
check("验证码窗口已关闭", cap_win() is None)
check("日志里有「已通知脚本」记录", "已通知脚本" in log)
check("脚本退出后状态是完成", app.status_var.get() == "完成", app.status_var.get())

root.destroy()
try:
    os.remove(FAKE)
except Exception:
    pass

print()
print(f"通过 {len(OK)} 项，失败 {len(BAD)} 项")
if BAD:
    for b in BAD:
        print("  -", b)
sys.exit(1 if BAD else 0)
