# -*- coding: utf-8 -*-
"""跑全部自测（离线，不联网，不碰智慧树）。

用法：python selftest\\run_all.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["test_captcha.py", "test_gui_popup.py", "test_zhidao_retry.py", "test_e2e.py"]

failed = []
for t in TESTS:
    path = os.path.join(HERE, t)
    print("=" * 60)
    print(f"### {t}")
    print("=" * 60)
    r = subprocess.run([sys.executable, "-u", path], cwd=os.path.dirname(HERE))
    if r.returncode != 0:
        failed.append(t)

print("=" * 60)
if failed:
    print("有失败：", ", ".join(failed))
else:
    print("全部自测通过")
sys.exit(1 if failed else 0)
