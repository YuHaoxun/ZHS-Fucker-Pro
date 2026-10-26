"""Ordinary shared-course homework client for choice and judgement questions.

Separate from AI knowledge exams and video popups. No logger import on reads.
Authentication, saving, readback and submission use the public website protocol.
Unknown question types and security challenges stop the task before submission.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, dataclass
from datetime import datetime
import html
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit, urljoin

import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

ROOT = Path(__file__).resolve().parent
API = "https://studentexam-api.zhihuishu.com/studentExam/gateway/t/v1/"
IV = b"1g3qqdh4jvbskb9x"


class SharedTestError(RuntimeError):
    pass


class LoginRequired(SharedTestError):
    pass


def load_session(cookie_path=None):
    from utils import list_to_cookie_jar
    preferred = ROOT / "shared_test_cookies.json"
    path = Path(cookie_path) if cookie_path else (preferred if preferred.exists() else ROOT / "cookies.json")
    cookies = json.loads(path.read_text(encoding="utf-8"))
    for cookie in cookies:
        # Chromium represents session cookies with expires=-1; requests would discard those as expired.
        if cookie.get("session") or (cookie.get("expires") is not None and cookie["expires"] < 0):
            cookie["expires"] = None
    session = requests.Session()
    session.cookies = list_to_cookie_jar(cookies)
    return session


def check_deadline():
    value = os.environ.get("ZHS_STOP_AT")
    if value and datetime.now().astimezone() >= datetime.fromisoformat(value):
        raise SharedTestError("已到停工时间，停止读取、保存和交卷")


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def backup_logs():
    import shutil
    backup = ROOT / "backups" / ("before_shared_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    if (ROOT / "logs").exists():
        shutil.copytree(ROOT / "logs", backup)


def qr_login(config):
    backup_logs()
    from fucker import Fucker
    from logger import logger
    from utils import showImage, cookie_jar_to_list
    logger.setLevel("WARNING")
    folder = ROOT / "qr"
    folder.mkdir(exist_ok=True)
    adapter = Fucker(proxies=config.get("proxies") or {}, tree_view=False,
                     progressbar_view=False, image_path=str(folder))
    print("请使用知到 App 扫描弹出的二维码，完成后会检查测试服务登录。", flush=True)
    try:
        adapter.login(use_qr=True, qr_callback=lambda image: showImage(image, show_in_terminal=False))
    except Exception as error:
        raise LoginRequired(f"扫码登录未完成（{type(error).__name__}），原凭证已保留") from None
    atomic_json(ROOT / "shared_test_cookies.json", cookie_jar_to_list(adapter.cookies))
    return load_session()


@dataclass(frozen=True)
class HomeworkIdentity:
    recruitId: str
    stuExamId: str
    examId: str
    courseId: str
    schoolId: str
    meetCourseType: str

    @classmethod
    def from_url(cls, value):
        parsed = urlsplit(value.strip())
        if parsed.scheme != "https" or parsed.netloc != "onlineexamh5new.zhihuishu.com":
            raise SharedTestError("请输入智慧树共享课 dohomework 页面完整 HTTPS 链接")
        parts = parsed.fragment.split("/")
        if len(parts) != 9 or parts[:3] != ["", "webExamList", "dohomework"]:
            raise SharedTestError("链接不是共享课平时测试 dohomework 页面")
        values = [unquote(v) for v in parts[3:]]
        if any(not re.fullmatch(r"[A-Za-z0-9_.+=-]+", v) for v in values):
            raise SharedTestError("测试链接参数格式无效")
        if values[-1] != "0":
            raise SharedTestError("当前仅支持普通共享课，暂不支持见面课测试")
        return cls(*values)

    def params(self):
        return {k: v for k, v in asdict(self).items() if k != "meetCourseType"}

    def open_params(self):
        params = self.params()
        params["studentExamId"] = params.pop("stuExamId")
        return params


def decode_signed_key(encrypted, public_key):
    """Decode the server's PKCS#1 v1.5 public-key protocol envelope."""
    block = base64.b64decode(encrypted, validate=True)
    if len(block) != public_key.size_in_bytes() or int.from_bytes(block, "big") >= public_key.n:
        raise SharedTestError("共享课密钥响应长度无效")
    raw = pow(int.from_bytes(block, "big"), public_key.e, public_key.n).to_bytes(len(block), "big")
    sep = raw.find(b"\0", 2)
    if not raw.startswith(b"\0\1") or sep < 10 or raw[2:sep] != b"\xff" * (sep - 2):
        raise SharedTestError("共享课密钥响应封装无效")
    key = json.loads(raw[sep + 1:].decode("utf-8"))["cKey"].encode("utf-8")
    if len(key) not in (16, 24, 32):
        raise SharedTestError("共享课密钥长度无效")
    return key


class SharedClient:
    def __init__(self, session, identity, pause=time.sleep):
        self.session, self.identity, self.pause = session, identity, pause
        self.key = None
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Origin": "https://onlineexamh5new.zhihuishu.com",
                                     "Referer": "https://onlineexamh5new.zhihuishu.com/"})

    def initialize(self):
        check_deadline()
        public = RSA.import_key(base64.b64decode((ROOT / "shared_public_key.pem").read_text()))
        uid = base64.b64encode(PKCS1_v1_5.new(public).encrypt(b'{"module":3}')).decode("ascii")
        response = self.session.get("https://appcomm-user.zhihuishu.com/app-commserv-user/c/has",
                                    params={"uid": uid}, timeout=(10, 20), allow_redirects=False)
        response.raise_for_status()
        value = response.json()
        if str(value.get("status")) != "200":
            raise SharedTestError("共享课加密初始化被拒绝")
        self.key = decode_signed_key(value["rt"]["sl"], public)

    def post(self, path, params):
        check_deadline()
        if self.key is None:
            self.initialize()
        encrypted = AES.new(self.key, AES.MODE_CBC, IV).encrypt(pad(compact(params).encode("utf-8"), 16))
        response = self.session.post(API + path, data={"secretStr": base64.b64encode(encrypted).decode("ascii")},
                                     timeout=(10, 25), allow_redirects=False)
        if response.is_redirect or response.status_code in (401, 403):
            raise LoginRequired("共享课测验未登录：现有程序凭证不能访问测试服务。保留原凭证，需重新登录该服务。")
        if response.status_code == 422:
            raise SharedTestError("测试服务要求人工验证，请在测试网页完成后重新运行")
        response.raise_for_status()
        if "json" not in response.headers.get("Content-Type", "").lower():
            raise LoginRequired("测试服务返回非 JSON 页面，无法确认登录；停止操作")
        value = response.json()
        if not isinstance(value, dict):
            raise SharedTestError("测试服务响应格式无效")
        if str(value.get("code")) == "-12" or str(value.get("status")) == "422":
            raise SharedTestError("测试服务要求人工验证，停止自动操作")
        if str(value.get("code")) in ("-1", "-2", "-3"):
            raise LoginRequired("测试服务拒绝登录或选课权限，请重新扫码登录；不会继续交卷")
        if str(value.get("status")) != "200":
            # Do not log the entire response: it may include account or auth data.
            raise SharedTestError(f"测试服务拒绝请求（status={value.get('status')}），停止操作")
        return value.get("rt")

    def read(self, path, params):
        for attempt in range(3):
            try:
                return self.post(path, params)
            except (requests.Timeout, requests.ConnectionError) as error:
                if attempt == 2:
                    raise SharedTestError(f"测试服务连续三次连接失败（{type(error).__name__}）。请检查网络和代理；Clash 用户请保持规则模式。草稿已保留，恢复后可重新运行。") from error
                self.pause(2 ** (attempt + 1))

    def open(self):
        try:
            result = self.read("student/doHomework", self.identity.open_params())
        except LoginRequired:
            # A valid CAS session may still need a service ticket for the homework API.
            self.establish_sso()
            result = self.read("student/doHomework", self.identity.open_params())
        if not isinstance(result, dict) or not isinstance(result.get("examBase"), dict):
            raise SharedTestError("测试服务未返回试卷数据")
        return result

    def establish_sso(self):
        url = "https://studentexam-api.zhihuishu.com/studentExam/gateway/f/v1/gologin/login?fromurl=https%3A%2F%2Fonlineexamh5new.zhihuishu.com%2FstuExamWeb.html"
        for _ in range(8):
            check_deadline()
            response = self.session.get(url, timeout=(10, 20), allow_redirects=False)
            response.raise_for_status()
            if not response.is_redirect:
                return
            url = urljoin(url, response.headers["Location"])
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not (parsed.hostname == "zhihuishu.com" or (parsed.hostname or "").endswith(".zhihuishu.com")):
                raise LoginRequired("测试登录跳转到非智慧树地址，已停止；请重新扫码登录")
        raise LoginRequired("测试服务登录跳转次数过多，请重新扫码登录")

    def list_homeworks(self):
        result, seen = [], set()
        for page in range(100):
            data = self.read("student/getStudentHomework", {
                "recruitId": self.identity.recruitId, "courseId": self.identity.courseId,
                "flag": 1, "pageSize": 100, "pageNum": page})
            if isinstance(data, dict) and "studentHomeworkList" not in data and str(data.get("next")) == "-1":
                return result  # The website also accepts an omitted list for an empty result.
            if not isinstance(data, dict) or not isinstance(data.get("studentHomeworkList"), list):
                raise SharedTestError("未提交测试清单数据不完整，停止批量任务")
            for item in data["studentHomeworkList"]:
                if str(item.get("meetCourseType")) != "0" or str(item.get("reviewType")) == "1":
                    continue
                if str(item.get("courseId")) != self.identity.courseId:
                    raise SharedTestError("测试清单混入其他课程，停止操作")
                if not item.get("id") or not item.get("examId"):
                    raise SharedTestError("测试清单缺少试卷标识")
                if str(item["id"]) not in seen:
                    seen.add(str(item["id"]))
                    result.append(item)
            if str(data.get("next")) == "-1":
                return result
        raise SharedTestError("测试清单超过分页上限，停止操作")

    def answers(self, ids):
        params = self.identity.params() | {"questionIds": ",".join(map(str, ids))}
        result = self.read("answer/getStuAnswerInfoNew", params)
        if not isinstance(result, dict):
            raise SharedTestError("已保存答案的数据格式无效")
        return result

    @staticmethod
    def matches(record, saved):
        entry = saved.get(str(record["eid"]))
        if not isinstance(entry, dict):
            return False
        actual = entry.get("answer")
        if actual is None:
            return False
        if record["questionType"] == 2:
            return set(str(actual).split(",")) == set(record["answer"].split(","))
        return str(actual) == record["answer"]

    def save(self, record):
        # A successful POST is also read back; a timeout is never treated as a failed save by itself.
        for attempt in range(3):
            try:
                result = self.post("answer/saveStudentAnswer", {
                    "recruitId": self.identity.recruitId, "stuExamAnswer": compact([record])})
                if not isinstance(result, dict) or str(result.get("statu")) != "1":
                    raise SharedTestError("服务器明确拒绝保存答案，停止交卷")
            except (requests.Timeout, requests.ConnectionError):
                pass
            saved = self.answers([record["eid"]])
            if self.matches(record, saved):
                return
            if attempt < 2:
                self.pause(2 ** (attempt + 1))
        raise SharedTestError("三次保存后读回仍不一致，停止交卷")

    def submit(self, records, top_level_count):
        saved = self.answers([r["eid"] for r in records])
        if not records or not all(self.matches(r, saved) for r in records):
            raise SharedTestError("交卷前答案读回不完整，停止交卷")
        params = {k: v for k, v in self.identity.params().items() if k in ("recruitId", "examId", "stuExamId")}
        params["achieveCount"] = top_level_count
        try:
            result = self.post("answer/submit", params)
        except (requests.Timeout, requests.ConnectionError):
            state = self.open()
            if str(state.get("state")) in ("3", "4"):
                return {"submitted": True, "score": None, "verifiedBy": "state_readback"}
            raise SharedTestError("交卷结果未知，已停止；不会重复提交") from None
        if not isinstance(result, dict) or str(result.get("statu")) not in ("0", "1", "-1"):
            raise SharedTestError("交卷响应未确认成功，停止操作")
        return {"submitted": True, "score": result.get("score") if str(result["statu"]) == "0" else None,
                "alreadySubmitted": str(result["statu"]) == "-1", "verifiedBy": "submit_response"}


def text_content(value):
    if not isinstance(value, str) or not value.strip():
        raise SharedTestError("题干或选项文字为空，停止答题")
    if re.search(r"<(?:img|audio|video|canvas|svg)\b", value, re.I):
        raise SharedTestError("题目含图片或音视频，当前文字答题接口不能可靠识别")
    return html.unescape(re.sub(r"<[^>]*>", " ", value)).strip()


def question_type(question):
    value = question.get("questionType")
    value = value.get("id") if isinstance(value, dict) else value
    return int(value) if isinstance(value, (int, str)) and str(value).isdigit() else None


def validate_choice(question, selected):
    kind = question_type(question)
    options = question.get("questionOptions") or []
    ids = [str(o.get("id")) for o in options]
    selected = [str(v) for v in selected]
    if not selected or len(set(ids)) != len(ids) or len(set(selected)) != len(selected):
        raise SharedTestError("选项 ID 重复或答案为空")
    if not set(selected).issubset(ids) or (kind in (1, 14) and len(selected) != 1):
        raise SharedTestError("AI 选项 ID 或答案数量无效")
    return ",".join(selected)


def prepare_questions(exam):
    questions = [q for part in exam.get("workExamParts", []) for q in part.get("questionDtos", [])]
    if not questions or str(exam.get("problemNum")) != str(len(questions)):
        raise SharedTestError("试卷题目数量不完整，停止答题")
    ids = []
    for q in questions:
        kind = question_type(q)
        if kind not in (1, 2, 14) or q.get("questionChildrens"):
            raise SharedTestError(f"共享课题型 {kind} 尚未验证；停止整份试卷，不提交部分答案")
        if q.get("questionDatas"):
            raise SharedTestError("试题含附件，当前不能可靠识别，停止答题")
        text_content(q.get("name"))
        options = q.get("questionOptions") or []
        if len(options) < 2:
            raise SharedTestError("题目选项不完整")
        for option in options:
            if option.get("id") in (None, "") or "," in str(option["id"]):
                raise SharedTestError("题目选项 ID 无效")
            text_content(option.get("content") or option.get("name"))
        if q.get("eid") in (None, ""):
            raise SharedTestError("题目缺少 eid")
        ids.append(str(q["eid"]))
    if len(ids) != len(set(ids)):
        raise SharedTestError("试卷题目 ID 重复")
    return questions


def paper_fingerprint(questions):
    canonical = [{"eid": str(q["eid"]), "type": question_type(q), "name": q["name"],
                  "choices": sorted([(str(o["id"]), o.get("content") or o.get("name"))
                                     for o in q["questionOptions"]])} for q in questions]
    return hashlib.sha256(compact(canonical).encode("utf-8")).hexdigest()


def run_homework(client, solver, checkpoint, execute=False, report=lambda message: None):
    report("正在读取测试试卷…")
    state = client.open()
    if str(state.get("state")) in ("3", "4"):
        outcome = {}
        if Path(checkpoint).exists():
            try:
                previous = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
                outcome = previous if isinstance(previous, dict) else {}
            except (ValueError, OSError):
                pass
        outcome.update(status="already_submitted", name=state["examBase"].get("name"),
                       totalScore=state["examBase"].get("totalScore"),
                       questionCount=state["examBase"].get("problemNum"),
                       submission={"submitted": True, "score": state.get("score"),
                       "verifiedBy": "state_readback"})
        atomic_json(checkpoint, outcome)
        return outcome
    questions = prepare_questions(state["examBase"])
    report(f"已读取 {state['examBase'].get('name', '测试')}，共 {len(questions)} 题")
    fingerprint = paper_fingerprint(questions)
    progress = {"status": "inspected", "name": state["examBase"].get("name"),
                "totalScore": state["examBase"].get("totalScore"), "fingerprint": fingerprint,
                "questionCount": len(questions), "drafts": [], "saved": []}
    if not execute:
        atomic_json(Path(checkpoint).with_suffix(".inspection.json"), progress)
        return progress
    previous = {}
    if Path(checkpoint).exists():
        try:
            previous = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            report("旧草稿无法读取，将重新生成答案")
    reusable = {str(r["eid"]): r for r in previous.get("drafts", [])} if previous.get("fingerprint") == fingerprint else {}
    server_answers = client.answers([q["eid"] for q in questions])
    # Generate and validate the entire paper before writing any answer to the server.
    for index, q in enumerate(questions, 1):
        check_deadline()
        existing = server_answers.get(str(q["eid"]), {})
        candidate = existing.get("answer") if isinstance(existing, dict) else None
        if candidate in (None, ""):
            candidate = reusable.get(str(q["eid"]), {}).get("answer")
        try:
            if candidate in (None, ""):
                raise SharedTestError("没有可复用草稿")
            answer = validate_choice(q, str(candidate).split(","))
            report(f"第 {index}/{len(questions)} 题复用已验证格式的草稿")
        except SharedTestError:
            report(f"AI 正在生成第 {index}/{len(questions)} 题答案…")
            answer = validate_choice(q, solver(q))
        record = client.identity.params() | {"eid": str(q["eid"]), "questionType": question_type(q),
                    "answer": answer, "dataIds": "", "deviceId": "", "examType": "", "fromType": 3}
        record.pop("courseId", None)
        progress["drafts"].append(record)
        progress["status"] = "drafting"
        atomic_json(checkpoint, progress)
    for record in progress["drafts"]:
        check_deadline()
        report(f"正在保存并读回核验第 {len(progress['saved']) + 1}/{len(questions)} 题…")
        progress["status"] = "saving"
        atomic_json(checkpoint, progress)
        try:
            if not client.matches(record, server_answers):
                client.save(record)
        except Exception as error:
            progress["status"] = "save_unconfirmed"
            progress["errorType"] = type(error).__name__
            atomic_json(checkpoint, progress)
            raise
        progress["saved"].append(record["eid"])
        progress["status"] = "saving"
        atomic_json(checkpoint, progress)
    report("全部答案已读回核验，正在交卷…")
    progress["status"] = "submitting"
    atomic_json(checkpoint, progress)
    progress["submission"] = client.submit(progress["drafts"], len(questions))
    progress["status"] = "submitted"
    atomic_json(checkpoint, progress)
    return progress


def configured_solver(config):
    ai = config.get("ai") or {}
    settings = ai.get("openai") or {}
    if not ai.get("enabled") or ai.get("use_zhidao_ai"):
        raise SharedTestError("共享课测试需要已启用的外部 AI 接口")
    if any(not settings.get(k) for k in ("api_base", "api_key", "model_name")):
        raise SharedTestError("外部 AI 接口配置不完整")
    # Import the legacy production AI adapter only when actually generating answers.
    # Its logger opens files with mode=w, so preserve all previous log files first.
    backup_logs()
    from fucker import Openai
    engine = Openai(baseUrl=settings["api_base"], apiKey=settings["api_key"],
                    modelName=settings["model_name"], useZhidao=False, stream=False)

    def solve(q):
        choices = [{"id": o["id"], "content": text_content(o.get("content") or o.get("name"))}
                   for o in q["questionOptions"]]
        template = {1: engine.singleChoiceTemplate, 2: engine.multipleChoiceTemplate,
                    14: engine.judgementTemplate}[question_type(q)]
        return engine.generateAnswer(template(text_content(q["name"]), choices))
    return solve


def run_batch(client, solver, state_dir, execute=False, report=lambda message: None):
    # Authenticate through an explicit homework URL before requesting its course's pending list.
    client.open()
    items = client.list_homeworks()
    summary = {"status": "running", "pendingCount": len(items), "results": []}
    summary_path = Path(state_dir) / ("batch_" + client.identity.recruitId + ".json")
    atomic_json(summary_path, summary)
    report(f"发现 {len(items)} 份未提交的普通共享课平时测试")
    for index, item in enumerate(items, 1):
        check_deadline()
        identity = HomeworkIdentity(client.identity.recruitId, str(item["id"]), str(item["examId"]),
                                    client.identity.courseId, client.identity.schoolId, "0")
        target = SharedClient(client.session, identity, client.pause)
        target.key = client.key
        report(f"开始第 {index}/{len(items)} 份：{item.get('examName', '')}")
        try:
            result = run_homework(target, solver, Path(state_dir) / (identity.stuExamId + ".json"), execute, report)
        except Exception as error:
            summary["status"] = "interrupted"
            summary["results"].append({"name": item.get("examName"), "status": "failed",
                                       "errorType": type(error).__name__})
            atomic_json(summary_path, summary)
            raise
        submission = result.get("submission", {})
        summary["results"].append({"name": item.get("examName"), "status": result["status"],
                                   "score": submission.get("score"), "totalScore": result.get("totalScore")})
        atomic_json(summary_path, summary)
        report(f"{item.get('examName', '')}：{result['status']}，服务器成绩 {submission.get('score')}")
    summary["status"] = "completed" if execute else "inspected"
    atomic_json(summary_path, summary)
    return summary


def main():
    for stream in (__import__("sys").stdout, __import__("sys").stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="共享课平时测试；默认只读取检查，支持单选、多选、判断")
    parser.add_argument("url", help="普通共享课 dohomework 页面链接")
    parser.add_argument("--execute", action="store_true", help="生成、保存、读回核验并交卷")
    parser.add_argument("--batch", action="store_true", help="处理同一课程全部未提交的普通平时测试")
    parser.add_argument("--login", action="store_true", help="先重新扫码登录测试服务，保留其他凭证文件")
    args = parser.parse_args()
    lock = None
    try:
        check_deadline()
        if args.execute and os.name == "nt":
            import msvcrt
            lock = (ROOT / ".shared_tests.lock").open("a+b")
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SharedTestError("已有共享课答题任务运行，请等待结束，避免重复交卷") from None
        identity = HomeworkIdentity.from_url(args.url)
        atomic_json(ROOT / "shared_test_settings.json", {"last_url": args.url})
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        session = qr_login(config) if args.login else load_session()
        client = SharedClient(session, identity)
        # Lazily initialize the solver after authentication and the whole paper were checked.
        engine = []
        def solve(q):
            if not engine:
                engine.append(configured_solver(config))
            return engine[0](q)
        checkpoint = ROOT / "shared_test_state" / (identity.stuExamId + ".json")
        report = lambda message: print(message, flush=True)
        if args.batch:
            result = run_batch(client, solve, ROOT / "shared_test_state", args.execute, report)
        else:
            result = run_homework(client, solve, checkpoint, execute=args.execute, report=report)
        if args.batch:
            print(f"共享课批量结果：{result['status']}；处理 {len(result['results'])}/{result['pendingCount']} 份")
        else:
            print(f"共享课测试结果：{result['status']}；题数：{result.get('questionCount', '已提交')}")
        if "submission" in result:
            print(f"服务器成绩：{result['submission'].get('score')}（None 表示尚未公布）")
        return 0
    except (SharedTestError, requests.RequestException, ValueError, KeyError, OSError) as error:
        # Credentials, HTTP bodies and headers are deliberately excluded.
        print(f"共享课测试未完成：{error if isinstance(error, SharedTestError) else type(error).__name__}")
        return 1
    finally:
        if lock is not None:
            lock.close()  # Windows releases the byte-range lock even after an interrupted run.


if __name__ == "__main__":
    raise SystemExit(main())
