#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZHS Fucker Pro 桌面启动器
把刷课、答题这些常用操作包成一个图形界面。

依赖：项目自带的 requirements.txt（Pillow / requests / pycryptodome / tiktoken / OpenAI）
运行：python gui.py
"""

import os
import sys
import re
import json
import subprocess
import threading
import queue
import time
import io

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# ------------------------------------------------------------------
# 路径与常量
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
EXECUTION_PATH = os.path.join(BASE_DIR, "execution.json")
AI_PATH = os.path.join(BASE_DIR, "ai_courses.json")
COOKIES_PATH = os.path.join(BASE_DIR, "cookies.json")
QR_DIR = os.path.join(BASE_DIR, "qr")
LOG_DIR = os.path.join(BASE_DIR, "logs")
MAIN_PY = os.path.join(BASE_DIR, "main.py")
SHARED_TESTS_PY = os.path.join(BASE_DIR, "shared_tests.py")

# 优先用当前解释器；本机系统 Python 已装齐依赖且带 tkinter
PYTHON = sys.executable

APP_TITLE = "ZHS Fucker Pro"

# 日志区最多保留的行数。脚本跑几小时会刷出几十万行，
# 不设上限的话 Text widget 会越来越卡（每次插入都要重算布局）。
LOG_MAX_LINES = 1200

# 进度条帧的特征：  "...|####      |  45.0%  done"
# 脚本用 \r 原地刷新进度条，一秒一帧、不带换行。
# 直接塞进日志会让界面卡死，这里识别出来只取百分比。
_PROG_RE = re.compile(r"\|[#\s]{4,}\|\s*([\d.]+)\s*%")

# 验证码风控的进程间约定（fucker.py 里那几个常量必须与这里一致）
CAPTCHA_ROOT_MARK = "@@CAPTCHA"          # 用来在输出里「找到」标记
CAPTCHA_MARK = "@@CAPTCHA@@"
CAPTCHA_OK_MARK = "@@CAPTCHA_OK@@"
CAPTCHA_TIMEOUT_MARK = "@@CAPTCHA_TIMEOUT@@"

# 合法的课程 ID：32/64 位十六进制（共享课 secret），或纯数字（AI 课）
_HEX_ID_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
_NUM_ID_RE = re.compile(r"^\d{3,}$")
_ID_NOISE = {"id", "ID", "共享课", "AI课", "翻转课", "课程", "course", "class"}


def parse_stream_piece(piece):
    """把子进程输出里的一「段」分类。

    返回 `(kind, payload)`：

    * `("pct", 45.0)` —— 进度条帧（只保留百分比，不然日志会被刷屏）
    * `("captcha", {...})` / `("captcha_ok", None)` / `("captcha_timeout", None)`
      —— 验证码风控事件（由 `fucker.py` 的等待逻辑抛出）
    * `("log", "原文")` —— 普通日志
    * `(None, None)` —— 空段，忽略

    抽成独立函数是为了能离线自测（GUI 的读取线程里没法单测）。
    """
    piece = (piece or "").strip()
    if not piece:
        return None, None
    # 标记优先：进度条是用 \r 原地刷新的，万一它和后面的输出挤在同一段里，
    # 先按进度帧吞掉就再也弹不出验证码窗口了（静默失败）。
    idx = piece.find(CAPTCHA_ROOT_MARK)
    if idx > 0:
        piece = piece[idx:]
    if piece.startswith(CAPTCHA_TIMEOUT_MARK):
        return "captcha_timeout", None
    if piece.startswith(CAPTCHA_OK_MARK):
        return "captcha_ok", None
    if piece.startswith(CAPTCHA_MARK):
        raw_json = piece[len(CAPTCHA_MARK):].strip()
        if raw_json.startswith("{"):          # 后面若还粘着别的文本，就切到最后一个 }
            end = raw_json.rfind("}")
            if end >= 0:
                raw_json = raw_json[:end + 1]
        try:
            info = json.loads(raw_json)
        except Exception:
            info = {}
        return "captcha", info
    m = _PROG_RE.search(piece)
    if m:
        try:
            return "pct", float(m.group(1))
        except ValueError:
            return None, None
    return "log", piece


def parse_course_ids(text):
    """从用户输入里提取真正的课程 ID。

    用户很自然会把课程列表整行粘进来，例如：
        [共享课] 某门共享学分课   id 0123456789abcdef0123456789abcdef
    直接空格切分后，脚本会拿到 "[共享课]"、"某门共享学分课" 这种非 ID 的
    字符串，服务端返回 400 NumberFormatException 整条任务失败。
    这里只挑出像 ID 的 token，其余当噪声丢掉。
    """
    found, dropped = [], []
    for tok in re.split(r"[\s,;、，|]+", text):
        tok = tok.strip().strip("[]（）()")
        if not tok:
            continue
        if tok in _ID_NOISE:
            continue
        if _HEX_ID_RE.match(tok) or _NUM_ID_RE.match(tok):
            if tok not in found:
                found.append(tok)
        else:
            dropped.append(tok)
    return found, dropped


# ------------------------------------------------------------------
# 配置读写
# ------------------------------------------------------------------
def load_config():
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


def read_text_auto(path):
    """读文本：优先 UTF-8，失败回退 GBK。

    脚本在 Windows 上用系统默认编码（cp936）写过 execution.json，
    这里做兼容，避免 GUI 因编码不一致直接解析失败、课程列表空白。
    返回 (文本, 实际编码)。
    """
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


# ------------------------------------------------------------------
# 主界面
# ------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self._on_done = None
        self._cmd_queue = []
        self.course_items = []
        self._log_lines = 0
        self._progress_pct = None
        self.log_queue = queue.Queue()
        self.config = load_config()
        # 清单只在「门数发生变化」时才重新播报，否则每次刷新状态都刷一行出来
        self._last_sig = None
        # 任务队列的统计：每个任务的标签 / 耗时 / 退出码
        self._seq_stats = []
        self._seq_start = 0.0
        self._cur_task = None

        root.title(APP_TITLE)
        root.geometry("880x640")
        root.minsize(780, 560)

        # 窗口图标
        ico = os.path.join(BASE_DIR, "app.ico")
        if os.path.isfile(ico):
            try:
                root.iconbitmap(ico)
            except Exception:
                pass

        self._build_styles()
        self._build_ui()

        self._poll_queue()
        self._refresh_status()
        self._start_watch_qr()

    # --------------------------------------------------------------
    # 样式
    # --------------------------------------------------------------
    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 6))
        style.configure("Go.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 8))
        style.configure("TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("TCheckbutton", font=("Microsoft YaHei UI", 9))

    # --------------------------------------------------------------
    # 界面构建
    # --------------------------------------------------------------
    def _build_ui(self):
        # ---- 顶部：状态条 ----
        top = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        top.pack(fill="x")

        ttk.Label(top, text="状态：", font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(
            top, textvariable=self.status_var,
            font=("Microsoft YaHei UI", 9, "bold"), foreground="#1a7f37")
        self.status_label.pack(side="left")

        ttk.Button(top, text="AI 答题设置", command=self.open_ai_settings).pack(side="right", padx=8)
        self.login_state_var = tk.StringVar()
        self.login_state_label = ttk.Label(top, textvariable=self.login_state_var)
        self.login_state_label.pack(side="right")

        # ---- 中部：左右分栏 ----
        mid = ttk.Frame(self.root, padding=(12, 4, 12, 4))
        mid.pack(fill="both", expand=True)
        mid.columnconfigure(0, weight=3, uniform="col")
        mid.columnconfigure(1, weight=2, uniform="col")
        mid.rowconfigure(0, weight=1)

        self._build_left(mid)
        self._build_right(mid)

        # ---- 底部：日志 ----
        self._build_log()

    # -- 左栏：操作区 --
    def _build_left(self, parent):
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # 1. 登录
        g1 = ttk.LabelFrame(left, text="  1. 登录  ", padding=(12, 8, 12, 12))
        g1.pack(fill="x", pady=(0, 10))

        ttk.Label(g1, text="仅支持手机扫码登录（用「知到」App，账号密码已失效）。",
                  foreground="#8a6d00").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(g1)
        row.pack(fill="x")
        ttk.Button(row, text="扫码登录", style="Go.TButton",
                   command=self.do_login).pack(side="left")
        ttk.Button(row, text="重新扫码（清凭证）",
                   command=self.relogin).pack(side="left", padx=(8, 0))
        row2 = ttk.Frame(g1)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Button(row2, text="打开二维码文件夹",
                   command=self.open_qr_dir).pack(side="left")
        ttk.Button(row2, text="清除登录状态",
                   command=self.clear_cookies).pack(side="left", padx=(8, 0))

        # 2. 课程
        g2 = ttk.LabelFrame(left, text="  2. 课程  ", padding=(12, 8, 12, 12))
        g2.pack(fill="x", pady=(0, 10))

        r1 = ttk.Frame(g2)
        r1.pack(fill="x", pady=(0, 6))
        ttk.Button(r1, text="拉取课程清单",
                   command=lambda: self.run_main(["--fetch"], on_done=self._after_fetch)
                   ).pack(side="left")
        ttk.Button(r1, text="刷新清单",
                   command=self.refresh_exec).pack(side="left", padx=(8, 0))
        ttk.Button(r1, text="编辑 AI 课清单",
                   command=self.open_ai_external).pack(side="left", padx=(8, 0))
        ttk.Button(r1, text="共享课平时测试",
                   command=self.open_shared_test).pack(side="left", padx=(8, 0))

        ttk.Label(g2, text="勾选要刷的课程（共享/翻转课来自 execution.json，AI 课来自 ai_courses.json）："
                  ).pack(anchor="w")

        listwrap = ttk.Frame(g2)
        listwrap.pack(fill="both", expand=True, pady=(4, 0))
        self.course_list = tk.Listbox(listwrap, selectmode="extended", height=6,
                                      font=("Microsoft YaHei UI", 9),
                                      activestyle="none")
        sb = ttk.Scrollbar(listwrap, orient="vertical", command=self.course_list.yview)
        self.course_list.configure(yscrollcommand=sb.set)
        self.course_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        r2 = ttk.Frame(g2)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Button(r2, text="开始刷选中的课",
                   style="Go.TButton",
                   command=self.run_selected_courses).pack(side="left")

        self.var_ai_resources_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(g2, text="AI课仅学习资源（不答题）", variable=self.var_ai_resources_only).pack(anchor="w", pady=(6, 0))

        # 3. 高级
        g3 = ttk.LabelFrame(left, text="  3. 可选设置  ", padding=(12, 8, 12, 12))
        g3.pack(fill="x")

        def spin_row(parent, label, var, from_, to, width=6, tip=""):
            f = ttk.Frame(parent)
            f.pack(fill="x", pady=3)
            ttk.Label(f, text=label, width=14).pack(side="left")
            sp = ttk.Spinbox(f, from_=from_, to=to, textvariable=var, width=width,
                             font=("Microsoft YaHei UI", 9))
            sp.pack(side="left")
            if tip:
                ttk.Label(f, text=tip, foreground="#888").pack(side="left", padx=(8, 0))
            return f

        self.var_speed = tk.StringVar(value="3")
        self.var_limit = tk.StringVar(value="0")
        self.var_course_ids = tk.StringVar(value="")

        spin_row(g3, "播放速度", self.var_speed, 1, 999, 6, "真实耗时 ≈ 视频时长 ÷ 该值")
        spin_row(g3, "单节限时(分)", self.var_limit, 0, 600, 6, "0 = 不限")

        pre = ttk.Frame(g3)
        pre.pack(fill="x", pady=(2, 4))
        ttk.Label(pre, text="快捷", width=14).pack(side="left")
        for label, val in (("1.5 最稳", "1.5"), ("2", "2"), ("3", "3"),
                           ("5", "5"), ("8 激进", "8")):
            ttk.Button(pre, text=label, width=8,
                       command=lambda v=val: self.var_speed.set(v)).pack(side="left", padx=(0, 4))
        ttk.Label(g3, text="默认 3：约 3 倍速。数值越高越快，但进度跳变越明显，"
                           "被判定异常的风险也越高（1.5 等同站点播放器上限，最保守）。",
                  foreground="#888", wraplength=380, justify="left").pack(anchor="w")

        f = ttk.Frame(g3)
        f.pack(fill="x", pady=3)
        ttk.Label(f, text="指定课程 ID", width=14).pack(side="left")
        ttk.Entry(f, textvariable=self.var_course_ids,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True)
        ttk.Label(g3, text="只填 ID 本身（会自动剔除非 ID 文字），填了就忽略上面的勾选。",
                  foreground="#888", wraplength=380, justify="left").pack(anchor="w")

    # -- 右栏：清单预览 --
    def _build_right(self, parent):
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        g = ttk.LabelFrame(right, text="  课程清单预览  ", padding=(10, 8, 10, 10))
        g.pack(fill="both", expand=True)

        self.exec_text = scrolledtext.ScrolledText(
            g, wrap="none", font=("Consolas", 9), width=30, height=20)
        self.exec_text.pack(fill="both", expand=True)

        bar = ttk.Frame(g)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="保存清单修改", command=self.save_exec).pack(side="left")
        ttk.Button(bar, text="用记事本打开", command=self.open_exec_external).pack(side="left", padx=(8, 0))

    # -- 日志区 --
    def _build_log(self):
        wrap = ttk.Frame(self.root, padding=(12, 4, 12, 12))
        wrap.pack(fill="both", expand=False)

        head = ttk.Frame(wrap)
        head.pack(fill="x")
        ttk.Label(head, text="运行日志", font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        ttk.Button(head, text="清空", command=self.clear_log).pack(side="right")
        self.stop_btn = ttk.Button(head, text="停止", command=self.stop_proc, state="disabled")
        self.stop_btn.pack(side="right", padx=(0, 8))

        self.log_text = scrolledtext.ScrolledText(
            wrap, height=12, font=("Consolas", 9), wrap="word",
            background="#1e1e1e", foreground="#d4d4d4", insertbackground="#d4d4d4")
        self.log_text.pack(fill="both", expand=True, pady=(6, 0))
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------------
    # 日志
    # --------------------------------------------------------------
    def log(self, msg, tag=None):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n", tag or ())
        self.log_text.configure(state="disabled")
        self._log_lines += 1
        # 超过上限就从头砍一批，避免控件无限膨胀
        if self._log_lines > LOG_MAX_LINES:
            cut = self._log_lines - LOG_MAX_LINES
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", f"{cut + 1}.0")
            self.log_text.configure(state="disabled")
            self._log_lines = LOG_MAX_LINES

    def log_batch(self, msgs):
        """批量写日志：一次性插入再滚动一次。

        逐行插入 + 每行 see("end") 会让 Tk 每行都重算布局并重绘，
        脚本高频输出时界面直接卡住。
        """
        if not msgs:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "\n".join(msgs) + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")
        self._log_lines += len(msgs)
        if self._log_lines > LOG_MAX_LINES:
            cut = self._log_lines - LOG_MAX_LINES
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", f"{cut + 1}.0")
            self.log_text.configure(state="disabled")
            self._log_lines = LOG_MAX_LINES

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log_lines = 0

    # --------------------------------------------------------------
    # 进程执行
    # --------------------------------------------------------------
    def run_main(self, args, on_done=None, script=MAIN_PY):
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showwarning(APP_TITLE, "已有任务在运行，请先停止或等待完成。")
            return False

        cmd = [PYTHON, "-u", script] + list(args)
        self.log(f"$ {' '.join(cmd[1:])}")
        self.set_status("运行中…", "#b8860b")
        self.stop_btn.configure(state="normal")
        self._on_done = on_done
        self._progress_pct = None

        def handle_line(raw):
            """把子进程的一「段」输出转成日志。

            进度条用 \\r 原地刷新，所以一段里可能混着几十个进度帧。
            这里只保留有意义的文本，进度帧折成一个百分比给状态栏。
            """
            text = raw.decode("utf-8", errors="replace")
            keep, pct = [], None
            for piece in text.split("\r"):
                kind, val = parse_stream_piece(piece)
                if kind is None:
                    continue
                if kind == "pct":
                    pct = val                     # 进度帧只取百分比，不进日志
                elif kind == "log":
                    keep.append(val)
                else:
                    self.log_queue.put((kind, val))   # 验证码事件交给界面处理
            if pct is not None:
                self.log_queue.put(("pct", pct))
            for k in keep:
                self.log_queue.put(("log", k))

        def worker():
            try:
                child_env = os.environ.copy()
                child_env["PYTHONIOENCODING"] = "utf-8"
                child_env["PYTHONUTF8"] = "1"
                self.proc = subprocess.Popen(
                    cmd, cwd=BASE_DIR, env=child_env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    # 管道而不是 DEVNULL：遇到验证码风控时脚本会暂停，
                    # 界面上的「我已解完」/「放弃」按钮要靠这根管道把指令送进去。
                    stdin=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                stream = self.proc.stdout
                # readline() 要等到 \n 才返回，而进度条只发 \r，
                # 结果整段视频的进度条会堆积成几百 KB 的单行一次性涌入。
                # 这里按 \r 和 \n 同时切分：进度帧即时处理掉，
                # 只有切到最后那段不完整的才留在 buf 里等下次。
                buf = b""
                while True:
                    chunk = stream.read1(8192) if hasattr(stream, "read1") \
                        else stream.readline()
                    if not chunk:
                        break
                    buf += chunk
                    parts = re.split(rb"[\r\n]", buf)
                    buf = parts.pop()          # 末段可能不完整，留着
                    for p in parts:
                        handle_line(p)
                if buf:
                    handle_line(buf)
                self.proc.wait()
                code = self.proc.returncode
                self.log_queue.put(("done", code))
            except Exception as e:
                self.log_queue.put(("log", f"[错误] {e}"))
                self.log_queue.put(("done", -1))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _poll_queue(self):
        pending = []
        done_code = None
        pct = None
        try:
            # 上限 200 条：一下子插入几千行同样会让界面失去响应
            for _ in range(200):
                kind, val = self.log_queue.get_nowait()
                if kind == "log":
                    pending.append(val)
                elif kind == "qr":
                    self.show_qr_popup(val)
                elif kind == "captcha":
                    self.show_captcha_popup(val)
                elif kind == "captcha_ok":
                    self.log("[验证码] 已解除，脚本继续刷课。")
                    self.close_captcha_popup()
                elif kind == "captcha_timeout":
                    self.log("[验证码] 等待结束（超时或被放弃），脚本将不再重试这个视频。")
                    self.close_captcha_popup()
                elif kind == "pct":
                    pct = val
                elif kind == "done":
                    done_code = val
        except queue.Empty:
            pass

        if pending:
            self.log_batch(pending)
        if pct is not None:
            self._progress_pct = pct
            self.set_status(f"运行中… {pct:.0f}%", "#b8860b")

        if done_code is not None:
            self.stop_btn.configure(state="disabled")
            self.proc = None
            self._progress_pct = None
            self.close_captcha_popup()      # 脚本都退出了，验证码窗口不该留着
            if done_code == 0:
                self.set_status("完成", "#1a7f37")
            else:
                self.set_status(f"结束（代码 {done_code}）", "#c0392b")
            cb, self._on_done = self._on_done, None
            # 先跑回调（它可能要接着排下一个任务 / 打印结果），再刷新状态。
            # 反过来的话，「已加载 N 门课程」会正好插在「全部任务已结束」前面，
            # 看起来像是「刚识别到课程就结束了」。
            if cb:
                try:
                    cb(done_code)
                except Exception as e:
                    self.log(f"[回调出错] {e}")
            self._refresh_status()

        self.root.after(120, self._poll_queue)

    def stop_proc(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.log("[已请求停止]")

    def set_status(self, text, color="#1a7f37"):
        self.status_var.set(text)
        self.status_label.configure(foreground=color)

    # --------------------------------------------------------------
    # 各功能
    # --------------------------------------------------------------
    def do_login(self):
        self.log("=== 开始扫码登录 ===")
        n = self._clear_qr_files()
        if n:
            self.log(f"[已清理 {n} 张过期二维码]")

        if os.path.isfile(COOKIES_PATH):
            self.log("[提示] 检测到已保存的登录状态。脚本会优先用它自动登录，")
            self.log("        这时**不会生成二维码** —— 看到 “Successfully recovered from saved cookies” 就说明已经登录好了。")
            self.log("        想要重新扫一次码，请点「重新扫码（清凭证）」。")
        else:
            self.log("二维码会存到 qr 文件夹，并自动弹窗显示。")
            self.log("手机端：打开知到 App → 底部「我的」→ 右上角「扫一扫」。")
            self.log("注意：必须用知到 App 扫，微信扫无效。")

        self.run_main(["-q"], on_done=self._after_login)

    def relogin(self):
        """强制重新扫码：先删掉 cookies.json 再走扫码流程。"""
        if os.path.isfile(COOKIES_PATH):
            if not messagebox.askyesno(
                    APP_TITLE,
                    "重新扫码会先删除已保存的登录状态，\n删除后必须重新用知到 App 扫码才能用。\n\n继续？"):
                return
            try:
                os.remove(COOKIES_PATH)
                self.log("[已清除 cookies.json，将强制生成新二维码]")
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"删除失败：{e}")
                return
        self._refresh_status()
        self.do_login()

    def _clear_qr_files(self):
        """清空 qr 目录里的旧二维码，同时重置监听基线。"""
        n = 0
        if os.path.isdir(QR_DIR):
            for f in os.listdir(QR_DIR):
                if f.lower().endswith(".png"):
                    try:
                        os.remove(os.path.join(QR_DIR, f))
                        n += 1
                    except Exception:
                        pass
        self._seen_qr = set()
        return n

    def _after_login(self, code):
        if code != 0:
            self.log("[登录结束但退出码非 0] 看上面日志的报错行。")
            return
        if os.path.isfile(COOKIES_PATH):
            self.log("[登录成功] cookies.json 已保存，右上角应显示「已保存」。")
            if not os.path.isfile(EXECUTION_PATH):
                self.log("[自动] 接着拉取课程清单…")
                self.run_main(["--fetch"], on_done=self._after_fetch)
            else:
                self.log("下一步：勾选左侧课程 → 点「开始刷选中的课」。")
        else:
            self.log("[注意] 没有生成 cookies.json —— 说明登录没走完。")
            self.log("       常见原因：二维码没扫 / 手机上没点「确认登录」/ 知到 App 自己还没登录。")

    def _after_fetch(self, code):
        has = os.path.isfile(EXECUTION_PATH)
        self.refresh_exec()
        if code == 0 and has:
            self.log("[清单已就绪] 左侧勾选课程，然后点「开始刷选中的课」。")
        else:
            self.log("[拉取失败] 检查网络，或确认登录状态（可重新登录一次）。")

    def open_qr_dir(self):
        os.makedirs(QR_DIR, exist_ok=True)
        os.startfile(QR_DIR)

    def clear_cookies(self):
        if not os.path.isfile(COOKIES_PATH):
            messagebox.showinfo(APP_TITLE, "当前没有保存的登录状态。")
            return
        if messagebox.askyesno(APP_TITLE, "确定要清除已保存的登录状态吗？\n下次需要重新扫码。"):
            try:
                os.remove(COOKIES_PATH)
                self.log("[已清除 cookies.json]")
                self._refresh_status()
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"删除失败：{e}")

    def open_ai_settings(self):
        from urllib.parse import urlsplit
        win = tk.Toplevel(self.root)
        win.title("AI 答题设置 — OpenAI 兼容接口")
        win.transient(self.root)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        cfg = load_config()
        ai = cfg.get("ai", {})
        op = ai.get("openai", {})
        enabled = tk.BooleanVar(value=ai.get("enabled", False))
        ttk.Checkbutton(box, text="启用 AI 课自动答题", variable=enabled).grid(row=0, columnspan=2, sticky="w")
        values = {}
        for row, (key, label, default) in enumerate([
            ("api_base", "接口地址", ""), ("model_name", "模型名称", ""), ("api_key", "API Key", "")
        ], 1):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=6)
            values[key] = tk.StringVar(value=op.get(key, default))
            ttk.Entry(box, textvariable=values[key], width=52, show="*" if key == "api_key" else "").grid(row=row, column=1)
        ttk.Label(box, text="支持根地址、/v1 或完整 /chat/completions 地址。\n密钥保存在本机 config.json；答题失败会停止交卷。").grid(row=4, columnspan=2, sticky="w", pady=10)
        def save():
            data = {k: v.get().strip() for k, v in values.items()}
            if enabled.get():
                parsed = urlsplit(data["api_base"])
                if not all(data.values()) or parsed.scheme not in ("http", "https") or not parsed.netloc:
                    messagebox.showerror(APP_TITLE, "请填写有效接口地址、模型名和密钥。", parent=win)
                    return
            fresh = load_config()
            section = fresh.setdefault("ai", {})
            section.update(enabled=enabled.get(), use_zhidao_ai=False, use_stream=False)
            section.setdefault("openai", {}).update(data)
            save_config(fresh)
            self.config = fresh
            self.log("[AI 设置已保存，下一次任务生效]")
            win.destroy()
        ttk.Button(box, text="保存", command=save).grid(row=5, column=1, sticky="e")

    def open_shared_test(self):
        win = tk.Toplevel(self.root)
        win.title("普通共享课平时测试")
        win.transient(self.root)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="粘贴本课程任意一份测试作答页链接（包含 #/webExamList/dohomework/）：").pack(anchor="w")
        last_url = ""
        try:
            with open(os.path.join(BASE_DIR, "shared_test_settings.json"), encoding="utf-8") as saved:
                last_url = json.load(saved).get("last_url", "")
        except (ValueError, OSError):
            pass
        url = tk.StringVar(value=last_url)
        ttk.Entry(box, textvariable=url, width=95).pack(fill="x", pady=8)
        batch = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="处理本课程全部未提交的普通平时测试", variable=batch).pack(anchor="w", pady=4)
        ttk.Label(box, text="支持选择、多选、判断题。AI 可能答错，成绩以平台为准。\n先读取检查登录和试卷；AI 完成会保存、读回核验并交卷。\n安全验证需在网页亲自完成后重新运行，已提交章节不会重做。\n未知题型或保存不确定时停止交卷。").pack(anchor="w")
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(12, 0))

        def start(execute, login=False):
            from shared_tests import HomeworkIdentity, SharedTestError
            try:
                HomeworkIdentity.from_url(url.get())
            except SharedTestError as error:
                messagebox.showerror(APP_TITLE, str(error), parent=win)
                return
            args = [url.get().strip()]
            if batch.get():
                args.append("--batch")
            if login:
                args.append("--login")
            if execute:
                args.append("--execute")
            if self.run_main(args, script=SHARED_TESTS_PY):
                self.log("[共享课测试] 将逐题保存和读回；测试评分以平台结果为准。")
                win.destroy()

        ttk.Button(buttons, text="读取检查", command=lambda: start(False)).pack(side="left")
        ttk.Button(buttons, text="重新扫码登录测试", command=lambda: start(False, True)).pack(side="left", padx=(8, 0))
        def open_page():
            from shared_tests import HomeworkIdentity, SharedTestError
            try:
                HomeworkIdentity.from_url(url.get())
            except SharedTestError as error:
                messagebox.showerror(APP_TITLE, str(error), parent=win)
                return
            import webbrowser
            webbrowser.open(url.get().strip())
        ttk.Button(buttons, text="打开测试网页", command=open_page).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="查看结果", command=self.show_shared_test_results).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="AI 完成并交卷", command=lambda: start(True)).pack(side="right")

    def show_shared_test_results(self):
        from pathlib import Path
        rows = []
        for path in (Path(BASE_DIR) / "shared_test_state").glob("*.json"):
            if path.name.startswith("batch_") or path.name.endswith(".inspection.json"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                status = {"submitted": "已提交", "already_submitted": "平台已提交",
                          "drafting": "答案草稿", "saving": "保存中", "save_unconfirmed": "保存待核验",
                          "submitting": "提交待核验"}.get(data.get("status"), "未完成")
                score = data.get("submission", {}).get("score")
                total = data.get("totalScore")
                score_text = "尚无成绩" if score is None else (f"{score} / {total}" if total is not None else str(score))
                rows.append(f"{data.get('name') or path.stem}：{status}；服务器成绩 {score_text}")
            except (ValueError, OSError, AttributeError):
                continue
        popup = tk.Toplevel(self.root)
        popup.title("共享课平时测试结果")
        popup.transient(self.root)
        text = scrolledtext.ScrolledText(popup, width=85, height=18, font=("Microsoft YaHei UI", 10))
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("end", "\n".join(sorted(rows)) if rows else "还没有本地测试结果，请先读取检查或运行测试。")
        text.configure(state="disabled")

    def run_selected_courses(self):
        ids = self.var_course_ids.get().strip()
        normal, ai_items = [], []

        if ids:
            normal, dropped = parse_course_ids(ids)
            if dropped:
                self.log(f"[已忽略非 ID 内容] {' '.join(dropped[:6])}"
                         + (" …" if len(dropped) > 6 else ""))
            if not normal:
                messagebox.showwarning(
                    APP_TITLE,
                    "在「指定课程 ID」里没找到合法的课程 ID。\n\n"
                    "看起来你粘进来的是课程清单的整行文字。\n"
                    "直接用上面的勾选就行 —— 或者只填 ID 本身，例如：\n"
                    "  0123456789abcdef0123456789abcdef\n\n"
                    "注意：AI 课不支持手填 ID（要课程号+班级号两个参数），\n"
                    "请在列表里直接勾选。")
                return
            self.log(f"[指定课程] {' '.join(normal)}")
        else:
            sel = self.course_list.curselection()
            if not sel:
                messagebox.showinfo(APP_TITLE, "请先在左侧勾选课程，或直接填写课程 ID。")
                return
            for i in sel:
                if i >= len(self.course_items):
                    continue
                it = self.course_items[i]
                if it["kind"] == "ai":
                    ai_items.append(it)
                else:
                    normal.append(it["id"])

        extra = []
        sp = self.var_speed.get().strip()
        if sp:
            extra += ["-s", sp]
        lm = self.var_limit.get().strip()
        if lm and lm != "0":
            extra += ["-l", lm]

        cmds = []
        if normal:
            cmds.append((["-c"] + normal + extra, f"刷 {len(normal)} 门共享/翻转课"))
        ai_extra = ["--noexam"] if self.var_ai_resources_only.get() else []
        if ai_items and not ai_extra:
            ai_cfg = load_config().get("ai", {})
            if not ai_cfg.get("enabled") or ai_cfg.get("use_zhidao_ai", False):
                messagebox.showwarning(APP_TITLE, "请先在「AI 答题设置」填写并保存 OpenAI 兼容接口，或勾选「AI课仅学习资源」。")
                return
        for it in ai_items:
            cmds.append((["-ai", it["courseId"], it["classId"]] + extra + ai_extra,
                         f"刷 AI 课：{it['name']}（{it.get('className') or it['classId']}）"))
        if not cmds:
            messagebox.showinfo(APP_TITLE, "没有可执行的任务。")
            return
        self._run_sequence(cmds)

    def _run_sequence(self, cmds):
        """依次执行多条命令（AI 课必须单独跑，不能和 -c 混在一起）。"""
        self._cmd_queue = list(cmds)
        self._seq_stats = []
        self._seq_start = time.time()
        self.log(f"=== 排入 {len(self._cmd_queue)} 个任务 ===")
        for i, (_a, label) in enumerate(cmds, 1):
            self.log(f"    {i}. {label}")
        self._next_in_sequence()

    def _next_in_sequence(self):
        if not self._cmd_queue:
            self._log_summary()
            return
        args, label = self._cmd_queue.pop(0)
        self._cur_task = {"label": label, "t0": time.time(), "code": None, "dur": 0.0}
        self.log(f"--- {label} ---")
        if self.run_main(args, on_done=self._on_task_done) is False:
            # 没能启动（上一个进程还活着）—— 清空队列，别让界面一直吊着
            self._cur_task = None
            self._cmd_queue = []
            self.log("[中止] 有任务已在运行，剩余队列已取消。")

    def _on_task_done(self, code):
        """单个任务收尾：记录耗时/退出码，读日志给出结论，再排下一个。"""
        if self._cur_task is not None:
            self._cur_task["code"] = code
            self._cur_task["dur"] = time.time() - self._cur_task["t0"]
            self._seq_stats.append(self._cur_task)
            self._cur_task = None
        self._report_task_result(code)
        self._next_in_sequence()

    def _log_summary(self):
        """队列跑完时的汇总。只写「全部任务已结束」等于什么都没说。"""
        self.log("=== 全部任务已结束 ===")
        if not self._seq_stats:
            return
        ok = [s for s in self._seq_stats if s["code"] == 0]
        bad = [s for s in self._seq_stats if s["code"] != 0]
        total = time.time() - self._seq_start
        self.log(f"    合计 {len(self._seq_stats)} 个任务：成功 {len(ok)}，失败 {len(bad)}，"
                 f"总耗时 {total / 60:.1f} 分钟")
        for s in self._seq_stats:
            mark = "成功" if s["code"] == 0 else "失败"
            self.log(f"      [{mark}] {s['label']} —— {s['dur']:.0f} 秒"
                     + ("" if s["code"] == 0 else f"，退出码 {s['code']}"))
        if bad:
            self.log("    失败原因看上面的 [结果] 行。")

    # --------------------------------------------------------------
    # 结果判定：脚本把详情写进 logs\*.log，标准输出里看不到
    # --------------------------------------------------------------
    def _read_log_tail(self, name, limit=8):
        """读 logs 目录下某个日志的尾部若干行。

        注意 logger 用的是 mode='w'，每个 main.py 进程启动时都会清空这些
        文件，所以读到的一定是「刚刚这个任务」的内容。
        """
        path = os.path.join(LOG_DIR, name)
        if not os.path.isfile(path):
            return []
        try:
            raw, _enc = read_text_auto(path)
        except Exception:
            return []
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        return lines[-limit:]

    def _report_task_result(self, code):
        """根据退出码 + logs 给出人话结论。"""
        errs = self._read_log_tail("error.log", 5)
        infos = self._read_log_tail("info.log", 4000)

        captcha = any("captcha" in e.lower() for e in errs)
        already = sum(1 for l in infos if "already watched" in l)

        # 验证码相关事件按时间排序，取最后一条来判断最终是「解开了」还是「没解开」
        cap_events = [l for l in infos
                      if ("Captcha required, waiting up to" in l
                          or "Captcha solved, resuming" in l
                          or "Captcha wait timed out or aborted" in l)]
        last_cap = cap_events[-1] if cap_events else ""

        # 「已看完」是正常跳过，不是失败，先单独说明
        if already:
            self.log(f"[情况] 本门课有 {already} 个视频此前已经看完，直接跳过"
                     f"（看过的不会重复计进度）。")

        if "Captcha solved, resuming" in last_cap:
            # 中途被要求验证码，但解掉之后自己接着刷了 —— 这不是失败
            self.log("[情况] 中途曾被要求验证码，解掉后已自动继续刷课。")
            if code == 0:
                self.log("[结果] 完成。")
                return

        if captcha and "Captcha solved, resuming" not in last_cap:
            # 风控：API 返回 code -12，脚本等待超时/被放弃后会丢掉整门课
            # 注意：这种中断的退出码是 0，上面 _poll_queue 已经把状态写成绿色的
            # 「完成」了，这里必须覆盖掉，否则等于骗人。
            self.set_status("被验证码拦下（未完成）", "#c0392b")
            self.log("[结果] 被风控拦下了 —— 服务端返回「需要验证码」。")
            self.log("       本门课被整体放弃，没有任何新进度（左上角的“完成”不作数）。")
            self.log("       说明：脚本遇到验证码会暂停等你解（最长 15 分钟），"
                     "等待超时或被点了「放弃」才会丢课。")
            self.log("       建议：① 把播放速度调回 1.5~2；② 隔 1~2 小时再试；")
            self.log("       ③ 下次弹提示时在浏览器里把验证码解掉，脚本会自己接着刷。")
            return

        if code == 0:
            self.log("[结果] 完成。")
            return

        self.log(f"[结果] 失败（退出码 {code}）。")
        for e in errs[-3:]:
            self.log(f"       服务端/脚本报错：{e}")

    def refresh_exec(self, quiet=False):
        """重新读两份清单并填列表。

        `quiet=True` 时，只有「门数发生变化」才播报一行 —— 这个方法在每次
        任务结束刷新状态时都会被调用，不设门槛就会反复刷屏，
        让人误以为程序卡在「识别课程」这一步。
        """
        courses, ai = [], []

        if os.path.isfile(EXECUTION_PATH):
            try:
                raw, enc = read_text_auto(EXECUTION_PATH)
                courses = json.loads(raw) if raw.strip() else []
                if enc not in ("utf-8", "utf-8-sig"):
                    self.log(f"[提示] 清单是 {enc} 编码，已兼容读取；点「保存清单修改」会转存为 UTF-8。")
                if not isinstance(courses, list):
                    self.log("[清单格式异常] execution.json 顶层应该是数组，请检查。")
                    courses = []
                else:
                    self.exec_text.delete("1.0", "end")
                    self.exec_text.insert("1.0", raw)
            except Exception as e:
                self.log(f"[清单解析失败] {e}")
                courses = []
        else:
            if not quiet or self._last_sig != ("none",):
                self.log("[提示] 还没有 execution.json —— 点「拉取课程清单」生成。")
            self._last_sig = ("none",)
            self.exec_text.delete("1.0", "end")

        if os.path.isfile(AI_PATH):
            try:
                araw, _aenc = read_text_auto(AI_PATH)
                ai = json.loads(araw) if araw.strip() else []
                if not isinstance(ai, list):
                    ai = []
            except Exception as e:
                self.log(f"[AI 课程清单解析失败] {e}")
                ai = []

        self.course_items = []
        for c in courses:
            if isinstance(c, dict):
                self.course_items.append({
                    "kind": "course",
                    "name": c.get("name", "未命名"),
                    "id": str(c.get("id", "")),
                })
        for c in ai:
            if isinstance(c, dict):
                self.course_items.append({
                    "kind": "ai",
                    "name": c.get("name", "未命名"),
                    "courseId": str(c.get("courseId", "")),
                    "classId": str(c.get("classId", "")),
                    "className": c.get("className", ""),
                })

        self.course_list.delete(0, "end")
        for it in self.course_items:
            if it["kind"] == "ai":
                self.course_list.insert("end", f"[AI课] {it['name']}")
            else:
                self.course_list.insert("end", f"[共享课] {it['name']}   id {it['id']}")

        if not self.course_items:
            if not quiet or self._last_sig != (0, 0):
                self.log("[注意] 没发现任何课程 —— 确认选课是否已导入智慧树。")
            self._last_sig = (0, 0)
        else:
            nc = sum(1 for i in self.course_items if i["kind"] == "course")
            na = len(self.course_items) - nc
            sig = (nc, na)
            if not quiet or sig != self._last_sig:
                self.log(f"[已加载 {len(self.course_items)} 门课程]"
                         f"（共享/翻转 {nc} 门，AI 课 {na} 门）")
            self._last_sig = sig

    def save_exec(self):
        raw = self.exec_text.get("1.0", "end").strip()
        if not raw:
            messagebox.showinfo(APP_TITLE, "内容为空，未保存。")
            return
        try:
            data = json.loads(raw)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"JSON 格式有误，请修正后再保存：\n{e}")
            return
        try:
            with open(EXECUTION_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            self.refresh_exec()
            self.log("[清单已保存]")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"保存失败：{e}")

    def open_exec_external(self):
        if not os.path.isfile(EXECUTION_PATH):
            messagebox.showinfo(APP_TITLE, "还没有 execution.json。")
            return
        os.startfile(EXECUTION_PATH)

    def open_ai_external(self):
        if not os.path.isfile(AI_PATH):
            messagebox.showinfo(APP_TITLE, "还没有 ai_courses.json —— 先点「拉取课程清单」。")
            return
        os.startfile(AI_PATH)

    # --------------------------------------------------------------
    # 状态刷新 / 二维码监听
    # --------------------------------------------------------------
    def _refresh_status(self):
        if os.path.isfile(COOKIES_PATH):
            self.login_state_var.set("登录状态：已保存")
            self.login_state_label.configure(foreground="#1a7f37")
        else:
            self.login_state_var.set("登录状态：未登录")
            self.login_state_label.configure(foreground="#c0392b")

        # 静默刷新：这里每次任务结束都会走到，不该反复播报清单
        self.refresh_exec(quiet=True)

    def _start_watch_qr(self):
        """监听 qr 目录，出现新二维码时自动打开。"""
        self._seen_qr = set()
        if os.path.isdir(QR_DIR):
            self._seen_qr = set(os.listdir(QR_DIR))

        def watch():
            while True:
                try:
                    if os.path.isdir(QR_DIR):
                        now = set(os.listdir(QR_DIR))
                        new = now - self._seen_qr
                        if new:
                            self._seen_qr = now
                            latest = sorted(new)[-1]
                            path = os.path.join(QR_DIR, latest)
                            self.log_queue.put(("log", f"[收到新二维码] {latest} → 正在弹窗显示"))
                            time.sleep(0.3)
                            self.log_queue.put(("qr", path))
                except Exception:
                    pass
                time.sleep(1.2)

        threading.Thread(target=watch, daemon=True).start()

    # --------------------------------------------------------------
    # 二维码弹窗（应用内显示，不依赖系统看图软件）
    # --------------------------------------------------------------
    def show_qr_popup(self, path):
        if not os.path.isfile(path):
            return

        # 关掉上一个二维码窗口，避免过期刷新时窗口堆叠
        old = getattr(self, "_qr_win", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass

        try:
            from PIL import Image, ImageTk
        except Exception:
            try:
                os.startfile(path)
            except Exception as e:
                self.log(f"[无法显示二维码] {e}")
            return

        win = tk.Toplevel(self.root)
        win.title("扫码登录")
        win.configure(bg="#ffffff")
        win.resizable(False, False)
        win.attributes("-topmost", True)

        try:
            img = Image.open(path).convert("RGB")
            side = 320
            img = img.resize((side, side), Image.Resampling.NEAREST)
            photo = ImageTk.PhotoImage(img)
        except Exception as e:
            self.log(f"[二维码加载失败] {e}")
            win.destroy()
            return

        frm = tk.Frame(win, bg="#ffffff", padx=20, pady=16)
        frm.pack()

        tk.Label(frm, text="知到 App 扫码登录", bg="#ffffff",
                 font=("Microsoft YaHei UI", 13, "bold"), fg="#1f2937").pack(pady=(0, 4))
        tk.Label(frm, text="知到 App → 底部「我的」→ 右上角「扫一扫」", bg="#ffffff",
                 font=("Microsoft YaHei UI", 9), fg="#6b7280").pack(pady=(0, 2))
        tk.Label(frm, text="必须用知到 App，微信扫无效", bg="#ffffff",
                 font=("Microsoft YaHei UI", 9), fg="#c0392b").pack(pady=(0, 12))

        lbl = tk.Label(frm, image=photo, bg="#ffffff", bd=0)
        lbl.image = photo
        lbl.pack()

        tk.Label(frm, text="过期会自动刷新，无需操作", bg="#ffffff",
                 font=("Microsoft YaHei UI", 9), fg="#9ca3af").pack(pady=(10, 0))

        ttk.Button(frm, text="关闭", command=win.destroy).pack(pady=(12, 0))

        # 居中显示
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        self._qr_win = win

    # --------------------------------------------------------------
    # 验证码风控：脚本会暂停等人工解，界面负责提示 + 把指令写回脚本
    # --------------------------------------------------------------
    def _send_to_proc(self, text):
        """往脚本的 stdin 写一行指令（go = 继续，skip = 放弃）。"""
        proc = self.proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            self.log("[提示] 脚本已经不在了，指令没送出去。")
            return False
        try:
            proc.stdin.write((text + "\n").encode("utf-8"))
            proc.stdin.flush()
            return True
        except Exception as e:
            self.log(f"[无法通知脚本] {e}")
            return False

    def close_captcha_popup(self):
        win = getattr(self, "_captcha_win", None)
        self._captcha_win = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def captcha_continue(self):
        if self._send_to_proc("go"):
            self.log("[验证码] 已通知脚本立刻重试。")
            self.set_status("运行中…", "#b8860b")
            self.close_captcha_popup()

    def captcha_skip(self):
        if self._send_to_proc("skip"):
            self.log("[验证码] 已通知脚本放弃等待（本门课会被跳过）。")
            self.close_captcha_popup()

    def captcha_open_browser(self):
        url = getattr(self, "_captcha_url", "") or ""
        if not url:
            return
        try:
            import webbrowser
            webbrowser.open(url)
            self.log("[验证码] 已用默认浏览器打开课程页，请登录同一账号并完成验证码。")
        except Exception as e:
            self.log(f"[无法打开浏览器] {e}\n       请手动复制：{url}")

    def show_captcha_popup(self, info):
        """风控暂停提示。非模态窗口 —— 关掉它脚本也不会死，只是继续等。"""
        info = info or {}
        url = info.get("url", "") or ""
        video = info.get("video", "") or ""
        timeout = info.get("timeout", 900)
        try:
            timeout = int(timeout)
        except Exception:
            timeout = 900
        self._captcha_url = url

        self.set_status("等待你在浏览器里解验证码…", "#d9822b")
        self.log("=== 智慧树要求验证码，刷课已暂停 ===")
        if video:
            self.log(f"    卡在视频：{video}")
        self.log(f"    浏览器打开（同一个账号）：{url}")
        self.log(f"    脚本每 20 秒自动探测一次，最多等 {timeout // 60} 分钟。")

        self.close_captcha_popup()

        win = tk.Toplevel(self.root)
        self._captcha_win = win
        win.title("需要你在浏览器里解一次验证码")
        win.configure(bg="#ffffff")
        win.resizable(False, False)
        win.attributes("-topmost", True)

        frm = tk.Frame(win, bg="#ffffff", padx=20, pady=16)
        frm.pack()

        tk.Label(frm, text="智慧树要求验证码，刷课已暂停", bg="#ffffff",
                 font=("Microsoft YaHei UI", 13, "bold"), fg="#b45309").pack(anchor="w")
        if video:
            tk.Label(frm, text=f"卡在视频：{video}", bg="#ffffff",
                     font=("Microsoft YaHei UI", 9), fg="#6b7280").pack(anchor="w", pady=(2, 0))
        tk.Label(frm, text="用浏览器登录同一个账号，打开课程页完成滑块/图形验证码。",
                 bg="#ffffff", font=("Microsoft YaHei UI", 9), fg="#374151",
                 wraplength=420, justify="left").pack(anchor="w", pady=(6, 0))
        tk.Label(frm, text="解完脚本会自动继续；不用回来点按钮也行。", bg="#ffffff",
                 font=("Microsoft YaHei UI", 9), fg="#6b7280").pack(anchor="w", pady=(2, 8))

        row = tk.Frame(frm, bg="#ffffff")
        row.pack(fill="x")
        entry = tk.Entry(row, width=48, font=("Consolas", 8))
        entry.insert(0, url)
        entry.configure(state="readonly")
        entry.pack(side="left", fill="x", expand=True)

        btns = tk.Frame(frm, bg="#ffffff")
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="用浏览器打开课程页",
                   command=self.captcha_open_browser).pack(side="left")
        ttk.Button(btns, text="我已解完，继续刷课",
                   command=self.captcha_continue).pack(side="left", padx=8)
        ttk.Button(btns, text="放弃这门课",
                   command=self.captcha_skip).pack(side="left")

        win.protocol("WM_DELETE_WINDOW", self.close_captcha_popup)

        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        # 自动带上浏览器，省得用户自己复制链接
        if url:
            self.root.after(200, self.captcha_open_browser)


# ------------------------------------------------------------------
def main():
    # 高 DPI 适配
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
