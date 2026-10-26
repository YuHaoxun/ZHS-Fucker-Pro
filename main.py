import os
import sys
# Keep console output consistent with the launcher's UTF-8 pipe decoder.
for _output_stream in (sys.stdout, sys.stderr):
    if _output_stream is not None and hasattr(_output_stream, "reconfigure"):
        _output_stream.reconfigure(encoding="utf-8", errors="replace")
import re
import json
import argparse
import requests
import platform
from functools import partial
from contextlib import suppress
from fucker import Fucker
from logger import logger
from ObjDict import ObjDict
from utils import showImage, cookie_jar_to_list
from utils import getConfigPath, getRealPath, versionCmp

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "qrlogin": True,
    "save_cookies": True,
    "proxies": {},
    "logLevel": "INFO",
    "tree_view": True,
    "progressbar_view": True,
    "check_update": False,
    "qr_extra": {
        "show_in_terminal": None,
        "ensure_unicode": False
    },
    "image_path":"",
    "captcha": {
        "wait": True,
        "timeout": 900,
        "interval": 20,
        "max_retry": 3
    },
    "pushplus": {
        "enable": False,
        "token": ""
    },
    "bark":{
        "enable": False,
        "token": "https://example.com/xxxxxxxxx"
    },
    "config_version": "1.5.0",
    "ai": {
        "enabled": True,
        "use_zhidao_ai": True,
        "openai": {
            "api_base": "https://api.openai.com",
            "api_key": "sk-",
            "model_name": "claude-3-5-sonnet-20240620"
        },
        "ppt_processing": {
            "provide_to_ai": False,
            "moonShot": {
                "base_url": "https://api.moonshot.cn/v1",
                "api_key": "sk-",
                "delete_after_convert": True
            }
        },
        "use_stream": True
    }
}
# get config or create one if not exist
if os.path.isfile(getConfigPath()):
    with open(getConfigPath(), 'r+', encoding="UTF-8") as f:
        # 不指定编码格式会导致config中可能存在的中文字符乱码
        config = ObjDict(json.load(f), default=None)
        if "config_version" not in config:
            config.config_version = "1.0.0"
        if versionCmp(config.config_version, DEFAULT_CONFIG["config_version"]) < 0:
            new = ObjDict(DEFAULT_CONFIG, default=None)
            if versionCmp(config.config_version, "1.0.1") < 0:
                config.pop("qr_extra", None)
            if versionCmp(config.config_version, "1.3.0") < 0:
                pushplus = config.pop("push", {})
                new.pushplus.update(pushplus)
            config.pop("config_version", None)
            new.update(config)
            config = new
            f.seek(0)
            json.dump(config, f, indent=4)
            f.truncate()
            print("****Config file updated****")
else:
    config = ObjDict(DEFAULT_CONFIG, default=None)
    with open(getConfigPath(), 'w') as f:
        json.dump(config, f, indent=4)

# parse arguments
def _str2bool(value):
    """argparse 的 type=bool 会把任意非空字符串都当成 True（bool("False") is True），
    这里显式解析，让 --tree_view False / --tree_view 0 真的能关掉。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


parser = argparse.ArgumentParser(prog="ZHS Fucker Pro")
parser.add_argument("-c", "--course", type=str, nargs="+",
                    help="CourseId or recruitAndCourseId, can be found in URL")
parser.add_argument("-v", "--videos", type=str, nargs="+",
                    help="Video IDs(fileId in URL, or, videoId found in API response")
parser.add_argument("-u", "--username", type=str,
                    help="if not set anywhere, will be prompted")
parser.add_argument("-p", "--password", type=str,
                    help="If not set anywhere, will be prompted. Be careful, it will be stored in history")
parser.add_argument("-s", "--speed", type=float,
                    help="Video Play Speed, default value is maximum speed found on site")
parser.add_argument("-t", "--threshold", type=float,
                    help="Video End Threshold, above this will be considered finished, overloaded when there are questions left unanswered")
parser.add_argument("-l", "--limit", type=int, default=0,
                    help="Time Limit (in minutes, 0 for no limit), default is 0")
parser.add_argument("-q", "--qrlogin", action="store_true",
                    help="Use QR Login")
parser.add_argument("-d", "--debug", action="store_true", help="Debug Mode")
parser.add_argument("-f", "--fetch", action="store_true",
                    help="Fetch new course list")
parser.add_argument("--show_in_terminal",
                    action="store_true", help="Show QR in terminal")
parser.add_argument("--proxy", type=str,
                    help="Proxy Config, e.g: http://127.0.0.1:8080")
parser.add_argument("--tree_view", nargs="?", const=True, type=_str2bool, default=None,
                    help="print the tree progress view of the course (--tree_view / --tree_view false)")
parser.add_argument("--progressbar_view", nargs="?", const=True, type=_str2bool, default=None,
                    help="print the progressbar view of the course (--progressbar_view / --progressbar_view false)")
parser.add_argument("--image_path", type=str,
                    help="Image save path, default is empty (do not save)")
parser.add_argument("-ai", "--aicourse", type=str, nargs=2,
                    metavar=('COURSE_ID', 'CLASS_ID'),
                    help="AI Course ID and CLASS ID to fuck aiCourse")

parser.add_argument("--noexam", action="store_true",
                    help="Disable AI exam")

args = parser.parse_args()

course = args.course
username = args.username or config.username
password = args.password or config.password
qrlogin = args.qrlogin or config.qrlogin or True  # Force enabled for v2.3.*
save_cookies = config.save_cookies or False
qr_extra = config.qr_extra or ObjDict(default=None)
show_in_terminal = args.show_in_terminal or config.qr_extra.show_in_terminal
tree_view = config.tree_view if args.tree_view is None else args.tree_view
progressbar_view = config.progressbar_view if args.progressbar_view is None else args.progressbar_view
image_path = args.image_path or config.image_path
if show_in_terminal is None:
    # Defaults to terminal in Windows
    show_in_terminal = platform.system() == "Windows"
ensure_unicode = qr_extra.ensure_unicode or False
logger.setLevel("DEBUG" if args.debug else (config.logLevel or "WARNING"))
proxies = config.proxies or {}
pushplus_token = config.pushplus.enable and config.pushplus.token or ""
bark_token = config.bark.enable and config.bark.token or ""

if logger.getLevel() == "DEBUG":
    print("*****************************\n" +
          "DEBUG MODE ENABLED\n" +
          "SENSITIVE DATA WILL BE LOGGED\n" +
          "*****************************\n")

if args.proxy:  # parse proxy
    match args.proxy.lower().split("://"):
        case ["http" | "https", proxy]:
            proxies["http"] = args.proxy
            proxies["https"] = args.proxy
        case ["socks5", proxy]:
            proxies["socks5"] = args.proxy
        case ["all", proxy]:
            proxies["http"] = args.proxy
            proxies["https"] = args.proxy
            proxies["socks5"] = args.proxy
        case [schema]:
            print(f"*Unsupported proxy type: {schema}")
            exit(1)

# check update
# 默认关闭：国内访问 raw.githubusercontent.com 极慢（实测单次 15s+），
# 而这段代码每次启动 main.py 都会执行一遍，GUI 里刷 N 门课就白等 N 次。
# 需要时把 config.json 里的 check_update 设为 true 即可恢复。
if config.check_update:
    with open(getRealPath("meta.json"), "r") as f:
        try:  # some exceptions won't be caught by 'with'
            m = ObjDict(json.load(f))
            url = f"https://raw.githubusercontent.com/{m.author}/ZHS-Fucker-Pro/{m.branch}/meta.json"
            r = ObjDict(requests.get(url, proxies=proxies, timeout=5).json())
            current = m.version
            latest = r.version
            if versionCmp(current, latest) < 0:
                print("*********************************\n" +
                      f"New version available: {latest}\n" +
                      f"Current version: {current}\n" +
                      "*********************************\n")
        except Exception:
            print("*Failed to check update\n")

# 验证码风控配置（config.json 的 captcha 段，缺项就用默认值兜底）
captcha_conf = config.get("captcha") or {}


def _captcha_opt(key, default):
    try:
        v = captcha_conf.get(key, default)
    except Exception:
        v = default
    return default if v is None else v


# create an instance, now we are talking... or fucking
fucker = Fucker(proxies=proxies, speed=args.speed, end_thre=args.threshold, limit=args.limit,
                pushplus_token=pushplus_token, bark_token=bark_token, tree_view=tree_view, progressbar_view=progressbar_view, image_path=image_path,
                captcha_wait=_captcha_opt("wait", True),
                captcha_timeout=_captcha_opt("timeout", 900),
                captcha_interval=_captcha_opt("interval", 20),
                captcha_max_retry=_captcha_opt("max_retry", 3))

cookies_path = getRealPath("./cookies.json")
cookies_loaded = False
if save_cookies and os.path.exists(cookies_path):
    try:
        with open(cookies_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        if cookies:
            fucker.cookies = cookies
            cookies_loaded = True
            print("已读取本地登录凭证；有效性由目标课程接口验证。")
    except (ValueError, KeyError, TypeError, OSError):
        print("本地登录凭证无法读取或格式无效，需要重新扫码登录。")


# first you need to login to get cookies
if not cookies_loaded:
    try:
        if qrlogin:
            callback = partial(
                showImage, show_in_terminal=show_in_terminal, ensure_unicode=ensure_unicode)
            fucker.login(use_qr=True, qr_callback=callback)
        else:
            fucker.login(username, password)
        print("Login Successful\n")
        if save_cookies:
            with open(cookies_path, 'w') as f:
                json.dump(cookie_jar_to_list(fucker.cookies), f,
                          indent=2, ensure_ascii=False)
    except Exception as e:
        print(e)
        exit(1)

# you can add cookies manually by setting cookies property of a Fucker instance
# notice that cookies of zhihuishu.com expires if you login again in somewhere else
# fucker.cookies = {}

if args.aicourse:
    course_id, class_id = args.aicourse
    try:
        def validate_config(config):
            ai_config = config.get("ai", {})
            
            if not isinstance(ai_config, dict):
                raise ValueError("AI配置不是字典，请检查配置文件")

            if not args.noexam and not ai_config.get("enabled", False):
                raise ValueError("请启用 AI 答题并配置接口，或选择仅学习资源")
            if not args.noexam and ai_config.get("enabled", False) and not ai_config.get("use_zhidao_ai", False):
                validate_openai_config(ai_config.get("openai", {}))
            
            validate_ppt_config(ai_config.get("ppt_processing", {}))

        def validate_openai_config(openai_config):
            if not isinstance(openai_config, dict):
                raise ValueError(f"OpenAI配置不是字典，而是{type(openai_config)}，请检查配置文件")
            
            required_fields = ["api_key", "api_base", "model_name"]
            missing_fields = [field for field in required_fields if not openai_config.get(field)]
            
            if missing_fields:
                raise ValueError(f"OpenAI配置不完整，缺少以下字段：{', '.join(missing_fields)}。请检查配置文件")

        def validate_ppt_config(ppt_config):
            if not isinstance(ppt_config, dict):
                raise ValueError(f"PPT处理配置不是字典，而是{type(ppt_config)}，请检查配置文件")
            
            if ppt_config.get("provide_to_ai", False):
                moonShot_conf = ppt_config.get("moonShot", {})
                required_fields = ["base_url", "api_key"]
                missing_fields = [field for field in required_fields if not moonShot_conf.get(field)]
                
                if missing_fields:
                    raise ValueError(f"PPT处理配置不完整，缺少以下字段：{', '.join(missing_fields)}。请检查配置文件")

        # 使用示例
        try:
            validate_config(config)
        except ValueError as e:
            print(f"配置错误: {e}")
            exit(1)

        if args.noexam:
            no_exam = True
        else:
            no_exam = False

        fucker.fuckAiCourse(course_id, class_id, aiConfig=config.ai, no_exam = no_exam)
    except Exception as e:
        logger.exception(e)
        print(f"Error when fucking AI course {course_id}:\n{e}")
        exit(1)
    else:
        print("AI course task completed")
        exit(0)


exec_list = getRealPath("execution.json")
ai_list_path = getRealPath("ai_courses.json")


def _pick(obj, *names, default=""):
    """兼容 dict / ObjDict / 普通对象三种取值方式。"""
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return default


# fetch course list
if args.fetch:
    # 显式 UTF-8：Windows 中文环境下默认 cp936，会造成写入/读取编码不一致
    with open(exec_list, "w", encoding="utf-8") as f:
        zhidao_ids = [{"name": c.courseName, "id": c.secret}
                      for c in fucker.getZhidaoList()]
        hike_ids = [{"name": c.courseName, "id": str(
            c.courseId)} for c in fucker.getHikeList()]
        json.dump(zhidao_ids + hike_ids, f, indent=4, ensure_ascii=False)
    print(f"*Fetched {len(zhidao_ids)} zhidao + {len(hike_ids)} hike course(s)")

    # AI 课程不在上面两类里，单独存一份（刷 AI 课需要 courseId + classId）
    try:
        ai = fucker.getZhidaoAiList() or []
        ai_items = [{
            "name": _pick(c, "courseName", "name"),
            "courseId": str(_pick(c, "courseId")),
            "classId": str(_pick(c, "classId")),
            "className": _pick(c, "className"),
        } for c in ai]
        with open(ai_list_path, "w", encoding="utf-8") as f:
            json.dump(ai_items, f, indent=4, ensure_ascii=False)
        print(f"*Fetched {len(ai_items)} AI course(s)")
    except Exception as e:
        print(f"*Failed to fetch AI course list: {e}")
    exit(0)

# get courses from file if not specified
if not course and os.path.isfile(exec_list):
    with open(exec_list, "r", encoding="utf-8") as f:
        try:
            course = [str(c["id"]) for c in json.load(f)]
        except Exception as e:
            print(f"*Failed to load course list from file: {e}")
            exit(1)

# still not found?
if not course:
    fucker.fuckWhatever()
    exit(0)

# auto detect mode
for c in course.copy():
    if args.videos:
        for v in args.videos:
            try:
                fucker.fuckVideo(course_id=c, video_id=v)
                print(f"fucked {v}")
                args.videos.remove(v)
            except Exception:
                pass
    else:
        try:
            fucker.fuckCourse(course_id=c)
            course.remove(c)
        except Exception as e:
            logger.exception(e)
            print(f"Error when fucking course {c}:\n{e}")
if args.videos:
    print(f"*the following videos are not fucked: {args.videos}")

# use fuckCourse method to fuck the entire course
# fucker.fuckCourse(course_id="")

# or if you want to fuck a video, use fuckVideo method
# fucker.fuckVideo(course_id="", file_id="")

# check the source code or README to find more info
