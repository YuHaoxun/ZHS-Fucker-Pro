# -*- coding: utf-8 -*-
"""离线自测（三）：fuckZhidaoCourse 遇到验证码时的「重试 / 放弃」分支。

用假的 ctx + 假的 fuckZhidaoVideo，不联网。
用法：python selftest\\test_zhidao_retry.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from fucker import Fucker, CaptchaException, TimeLimitExceeded
from ObjDict import ObjDict

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  [OK] " if cond else "  [!!] ") + name + (("   " + str(extra)) if extra else ""))


def make_ctx():
    return ObjDict({
        "course": {"courseInfo": {"name": "测试共享课", "enName": ""}},
        "chapters": {"videoChapterDtos": [
            {"name": "第一章", "videoLessons": [
                {"name": "第一节", "videoSmallLessons": [
                    {"name": "视频1", "videoId": "v1"},
                    {"name": "视频2", "videoId": "v2"},
                    {"name": "视频3", "videoId": "v3"},
                ]},
            ]},
        ]},
    }, default=None)


def build(video_behaviour, wait_result, max_retry=2, wait_calls=None):
    f = Fucker()
    f.tree_view = False                      # 别往测试输出里灌树形日志
    f.captcha_max_retry = max_retry
    f.getZhidaoContext = lambda rid, force=False: make_ctx()
    f.fuckZhidaoVideo = video_behaviour
    if callable(wait_result):
        f.waitCaptcha = wait_result
    else:
        def _wait(rid, name="", **kw):
            if wait_calls is not None:
                wait_calls.append(name)
            return wait_result
        f.waitCaptcha = _wait
    return f


print("== 1. 解掉验证码后重刷当前视频，后面的继续 ==")
log = []


def behav_ok(rid, vid):
    log.append(vid)
    if vid == "v1" and log.count("v1") < 2:
        raise CaptchaException("captcha required")
    return None


waits = []
f = build(behav_ok, True, max_retry=2, wait_calls=waits)
f.fuckZhidaoCourse("RID")
check("v1 被重试了一次", log.count("v1") == 2, log)
check("v2/v3 照常刷完", log[-2:] == ["v2", "v3"], log)
check("只等了一次验证码", waits == ["视频1"], waits)

print("== 2. 一直解不掉 -> 放弃整门课（旧行为）==")
log2 = []
f = build(lambda rid, vid: (log2.append(vid), (_ for _ in ()).throw(CaptchaException("x")))[1],
          False, max_retry=2)
f.fuckZhidaoCourse("RID")
check("只试了第一个视频", log2 == ["v1"], log2)

print("== 3. 解了但还是被风控 -> 最多重试 max_retry 次后放弃 ==")
log3 = []
f = build(lambda rid, vid: (log3.append(vid), (_ for _ in ()).throw(CaptchaException("x")))[1],
          True, max_retry=2)
f.fuckZhidaoCourse("RID")
check("v1 试了 1+2 次", log3.count("v1") == 3, log3)
check("没有继续碰 v2", "v2" not in log3, log3)

print("== 4. captcha_wait=False 时保持老逻辑（第一次就放弃）==")
log4 = []
f = build(lambda rid, vid: (log4.append(vid), (_ for _ in ()).throw(CaptchaException("x")))[1],
          False, max_retry=2)
f.captcha_wait = False
f.fuckZhidaoCourse("RID")
check("直接放弃", log4 == ["v1"], log4)

print("== 5. 普通异常不影响后续视频 ==")
log5 = []


def behav_err(rid, vid):
    log5.append(vid)
    if vid == "v2":
        raise ValueError("视频2挂了")


f = build(behav_err, True)
f.fuckZhidaoCourse("RID")
check("报错的视频跳过、v3 继续", log5 == ["v1", "v2", "v3"], log5)

print("== 6. 时限到了仍然直接退出整门课 ==")
log6 = []


def behav_limit(rid, vid):
    log6.append(vid)
    raise TimeLimitExceeded("30 minutes")


f = build(behav_limit, True)
f.fuckZhidaoCourse("RID")
check("时限中断后不再碰后面的视频", log6 == ["v1"], log6)

print()
print(f"通过 {len(OK)} 项，失败 {len(BAD)} 项")
if BAD:
    for b in BAD:
        print("  -", b)
sys.exit(1 if BAD else 0)
