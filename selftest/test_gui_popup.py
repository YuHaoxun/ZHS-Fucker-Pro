# -*- coding: utf-8 -*-
"""离线自测（二）：验证码弹窗能不能在真实 Tk 里建起来、按钮有没有把指令写进管道。

只建窗口、立刻销毁，不联网、不跑脚本。
用法：python selftest\\test_gui_popup.py
"""
import io
import os
import sys
import webbrowser

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tkinter as tk
import gui

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  [OK] " if cond else "  [!!] ") + name + (("   " + str(extra)) if extra else ""))


# 别真的把浏览器拉起来
opened = []
webbrowser.open = lambda url, *a, **k: (opened.append(url), True)[1]

root = tk.Tk()
root.withdraw()                      # 主窗口不显示
app = gui.App(root)


class FakeProc:
    """冒充子进程：只需要 poll / stdin。"""
    def __init__(self):
        self.stdin = io.BytesIO()
        self.returncode = None

    def poll(self):
        return None


print("== 1. 构造弹窗 ==")
app.log_queue.put(("captcha", {"url": "https://studyh5.zhihuishu.com/x?recruitAndCourseId=ABC",
                               "video": "五四运动对陕西的影响", "timeout": 900}))
app._poll_queue()
root.update()
win = app._captcha_win
check("弹窗创建成功", win is not None)
check("状态栏提示在等验证码", "验证码" in app.status_var.get(), app.status_var.get())
check("记下了课程页地址", "recruitAndCourseId=ABC" in app._captcha_url)
check("日志里有暂停提示", "要求验证码" in app.log_text.get("1.0", "end"))

print("== 2. 按钮 -> 管道 ==")
app.proc = FakeProc()
app.captcha_continue()
check("「我已解完」写入了 go", app.proc.stdin.getvalue() == b"go\n",
      app.proc.stdin.getvalue())
check("点完弹窗自动关掉", app._captcha_win is None)

app.proc = FakeProc()
app.log_queue.put(("captcha", {"url": "u", "video": "v", "timeout": 900}))
app._poll_queue()
root.update()
check("弹窗可以再次弹出", app._captcha_win is not None)
app.captcha_skip()
check("「放弃」写入了 skip", app.proc.stdin.getvalue() == b"skip\n",
      app.proc.stdin.getvalue())

print("== 3. 脚本说解开了 / 超时了 ==")
app.proc = FakeProc()
app.log_queue.put(("captcha", {"url": "u", "video": "v", "timeout": 900}))
app._poll_queue()
root.update()
app.log_queue.put(("captcha_ok", None))
app._poll_queue()
root.update()
check("OK 事件关掉弹窗", app._captcha_win is None)

app.proc = FakeProc()
app.log_queue.put(("captcha", {"url": "u", "video": "v", "timeout": 900}))
app._poll_queue()
root.update()
app.log_queue.put(("captcha_timeout", None))
app._poll_queue()
root.update()
check("超时事件关掉弹窗", app._captcha_win is None)

print("== 4. 脚本已退出时不能崩 ==")
app.proc = None
app.captcha_continue()          # 只应该打一行提示
check("脚本不在也不报错", True)

print("== 5. 任务结束时自动收掉弹窗 ==")
app.proc = FakeProc()
app.log_queue.put(("captcha", {"url": "u", "video": "v", "timeout": 900}))
app._poll_queue()
root.update()
app.log_queue.put(("done", 0))
app._poll_queue()
root.update()
check("done 之后弹窗关闭", app._captcha_win is None)

root.destroy()

print()
print(f"通过 {len(OK)} 项，失败 {len(BAD)} 项")
if BAD:
    for b in BAD:
        print("  -", b)
sys.exit(1 if BAD else 0)
