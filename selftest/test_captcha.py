# -*- coding: utf-8 -*-
"""离线自测（一）：验证码「暂停 -> 人工解 -> 自动继续」的核心逻辑。

不联网：探测函数被替换成假的，只用本地管道验证进程间协议。

用法：python selftest\\test_captcha.py
"""
import io
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import gui                                   # 常量/解析（Tk 只在 main() 里建窗口）
import fucker as F
from fucker import Fucker

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  [OK] " if cond else "  [!!] ") + name + (("   " + str(extra)) if extra else ""))


class CaptureStdout:
    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = self.buf = io.StringIO()
        return self.buf

    def __exit__(self, *a):
        sys.stdout = self._old


print("== 1. 标记常量两侧一致 ==")
check("CAPTCHA_MARK 一致", gui.CAPTCHA_MARK == Fucker.CAPTCHA_MARK)
check("OK 标记一致", gui.CAPTCHA_OK_MARK == Fucker.CAPTCHA_OK_MARK)
check("TIMEOUT 标记一致", gui.CAPTCHA_TIMEOUT_MARK == Fucker.CAPTCHA_TIMEOUT_MARK)

print("== 2. 界面侧解析 parse_stream_piece ==")
check("进度帧 -> pct", gui.parse_stream_piece("Fucking video x |####    |  45.0%  done")[0] == "pct")
check("普通日志 -> log", gui.parse_stream_piece("Fucking chapter 第一章")[0] == "log")
check("空行 -> None", gui.parse_stream_piece("   ")[0] is None)
k, v = gui.parse_stream_piece('@@CAPTCHA@@{"url":"http://u","video":"v1","timeout":900}')
check("验证码标记 -> captcha + 解析出 url/video", k == "captcha" and v["url"] == "http://u" and v["video"] == "v1")
check("坏 JSON 不炸", gui.parse_stream_piece("@@CAPTCHA@@{不是json")[0] == "captcha")
check("OK 标记 -> captcha_ok", gui.parse_stream_piece("@@CAPTCHA_OK@@")[0] == "captcha_ok")
check("超时标记 -> captcha_timeout", gui.parse_stream_piece("@@CAPTCHA_TIMEOUT@@")[0] == "captcha_timeout")
# 进度条残留和标记挤在同一段（标点必须优先，否则界面弹不出窗口）
k, v = gui.parse_stream_piece('Fucking video x |##      |  30.0%  @@CAPTCHA@@{"url":"http://u"}')
check("进度条+标记挤一段也能认出", k == "captcha" and v.get("url") == "http://u", (k, v))
k, v = gui.parse_stream_piece('@@CAPTCHA@@{"url":"http://u"}Fucking video y')
check("标记后面粘着文本也能解析", k == "captcha" and v.get("url") == "http://u", (k, v))

print("== 3. 构造器下限 & captcha_wait=False 的旧行为 ==")
f0 = Fucker(captcha_timeout=0, captcha_interval=0, captcha_max_retry=0)
check("timeout 下限 30 秒", f0.captcha_timeout == 30, f0.captcha_timeout)
check("interval 下限 5 秒", f0.captcha_interval == 5, f0.captcha_interval)
check("max_retry 下限 1 次", f0.captcha_max_retry == 1, f0.captcha_max_retry)
f = Fucker(captcha_wait=False, captcha_timeout=60)
f._captchaCleared = lambda rid: (_ for _ in ()).throw(AssertionError("不该被调用"))
check("关闭等待后立即返回 False", f.waitCaptcha("RID", "v") is False)

print("== 4. 风控解除后自动返回 True ==")
f = Fucker()
f.captcha_timeout, f.captcha_interval = 8, 1      # 自测直接改属性，绕开构造器下限
state = {"n": 0}


def probe_later(rid):
    state["n"] += 1
    return state["n"] >= 3          # 第 3 次探测时用户已经解完


f._captchaCleared = probe_later
with CaptureStdout() as buf:
    t0 = time.time()
    res = f.waitCaptcha("RID123", "五四运动对陕西的影响")
    dur = time.time() - t0
out = buf.getvalue()
check("返回 True", res is True)
check("确实探测了 3 次", state["n"] == 3, state["n"])
check("输出了 OK 标记", Fucker.CAPTCHA_OK_MARK in out)
check("输出了课程页地址", "recruitAndCourseId=RID123" in out)
check("输出了卡住的视频名", "五四运动对陕西的影响" in out)
check("带 JSON 负载的暂停标记", Fucker.CAPTCHA_MARK + '{"url"' in out)
check("等待期间没有空转（>=2 秒）", dur >= 2, f"{dur:.1f}s")

print("== 5. 一直不解 -> 超时返回 False ==")
f = Fucker()
f.captcha_timeout, f.captcha_interval = 4, 1
f._captchaCleared = lambda rid: False
with CaptureStdout() as buf:
    t0 = time.time()
    res = f.waitCaptcha("RID", "v")
    dur = time.time() - t0
out = buf.getvalue()
check("返回 False", res is False)
check("输出了超时标记", Fucker.CAPTCHA_TIMEOUT_MARK in out)
check("没有输出 OK 标记", Fucker.CAPTCHA_OK_MARK not in out)
check("在 4~9 秒内结束", 4 <= dur <= 9, f"{dur:.1f}s")
check("中途有「还在等」的心跳行", "仍需要验证码" in out)

print("== 6. stdin 收到 go -> 立刻重试 ==")
f = Fucker()
f.captcha_timeout, f.captcha_interval = 4, 3
f._captchaCleared = lambda rid: False        # 探测一直失败，只看「继续」指令有没有被读到
old_stdin = sys.stdin
sys.stdin = io.StringIO("go\n")
try:
    with CaptureStdout() as buf:
        f.waitCaptcha("RID", "v")
    out = buf.getvalue()
finally:
    sys.stdin = old_stdin
check("读到了「继续」指令", "收到「继续」指令" in out)

print("== 7. stdin 收到 skip -> 立刻放弃 ==")
f = Fucker()
f.captcha_timeout, f.captcha_interval = 30, 5
f._captchaCleared = lambda rid: False
sys.stdin = io.StringIO("skip\n")
try:
    with CaptureStdout() as buf:
        t0 = time.time()
        res = f.waitCaptcha("RID", "v")
        dur = time.time() - t0
    out = buf.getvalue()
finally:
    sys.stdin = old_stdin
check("立刻返回 False", res is False)
check("没有等满 30 秒", dur < 5, f"{dur:.1f}s")
check("读了「放弃」指令", "收到「放弃」指令" in out)

print("== 8. GUI <-> 脚本 管道协议（真子进程）==")
child = r'''
import sys, json
print("@@CAPTCHA@@" + json.dumps({"url": "http://u", "video": "v", "timeout": 99}), flush=True)
line = sys.stdin.readline()
print("GOT:" + line.strip(), flush=True)
if line.strip() == "go":
    print("@@CAPTCHA_OK@@", flush=True)
'''
p = subprocess.Popen([sys.executable, "-u", "-c", child],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
first = p.stdout.readline().decode("utf-8", "replace")
kind, info = gui.parse_stream_piece(first)
check("子进程的暂停标记被识别", kind == "captcha" and info.get("video") == "v", first.strip())
p.stdin.write(b"go\n")                       # 等价于界面点「我已解完」
p.stdin.flush()
rest = p.communicate(timeout=10)[0].decode("utf-8", "replace")
check("脚本收到了 go", "GOT:go" in rest)
check("脚本发出 OK 标记", gui.CAPTCHA_OK_MARK in rest)
check("OK 标记被识别为 captcha_ok",
      gui.parse_stream_piece(gui.CAPTCHA_OK_MARK)[0] == "captcha_ok")

print()
print(f"通过 {len(OK)} 项，失败 {len(BAD)} 项")
if BAD:
    print("失败项：")
    for b in BAD:
        print("  -", b)
sys.exit(1 if BAD else 0)
