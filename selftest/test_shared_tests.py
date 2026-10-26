import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64
import json

from shared_tests import (HomeworkIdentity, SharedClient, SharedTestError, LoginRequired,
                          IV, check_deadline, prepare_questions, run_homework, validate_choice,
                          load_session, paper_fingerprint)

URL = "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html#/webExamList/dohomework/440533/abc/def/1000096966/1299/0"
IDENTITY = HomeworkIdentity.from_url(URL)
QUESTION = {"eid": "q1", "questionType": {"id": 1}, "name": "<p>测试题干</p>",
            "questionOptions": [{"id": 72, "name": "乙"}, {"id": 31, "name": "甲"}]}
EXAM = {"state": 1, "examBase": {"problemNum": 1, "workExamParts": [{"questionDtos": [QUESTION]}]}}


class FakeClient:
    identity = IDENTITY

    def __init__(self, exam=None):
        self.exam = exam or copy.deepcopy(EXAM)
        self.saved = []
        self.submitted = False

    def open(self):
        return self.exam

    def save(self, record):
        self.saved.append(record)

    def answers(self, ids):
        return {}

    matches = staticmethod(SharedClient.matches)

    def submit(self, records, count):
        self.submitted = True
        return {"submitted": True, "score": 10}


class SharedTests(unittest.TestCase):
    def test_identity_keeps_distinct_opening_parameter(self):
        self.assertEqual(IDENTITY.params()["stuExamId"], "abc")
        self.assertEqual(IDENTITY.open_params()["studentExamId"], "abc")
        self.assertNotIn("stuExamId", IDENTITY.open_params())
        with self.assertRaises(SharedTestError):
            HomeworkIdentity.from_url(URL.replace("dohomework", "doExam"))

    def test_answers_use_ids_even_after_options_reordered(self):
        self.assertEqual(validate_choice(QUESTION, [31]), "31")
        with self.assertRaises(SharedTestError):
            validate_choice(QUESTION, ["A"])
        with self.assertRaises(SharedTestError):
            validate_choice(QUESTION, [72, 31])

    def test_unicode_encryption_uses_utf8_byte_padding(self):
        class Response:
            is_redirect = False
            status_code = 200
            headers = {"Content-Type": "application/json"}
            def raise_for_status(self): pass
            def json(self): return {"status": "200", "rt": {}}
        class Session:
            headers = {}
            def post(self, url, data, **kwargs):
                self.data = data
                return Response()
        session = Session()
        client = SharedClient(session, IDENTITY)
        client.key = b"0123456789abcdef"
        client.post("read", {"name": "中文测试"})
        payload = unpad(AES.new(client.key, AES.MODE_CBC, IV).decrypt(base64.b64decode(session.data["secretStr"])), 16)
        self.assertEqual(json.loads(payload)["name"], "中文测试")

    def test_inspection_never_calls_solver_save_or_submit(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            result = run_homework(client, lambda q: self.fail("AI called"), Path(folder) / "state.json")
        self.assertEqual(result["status"], "inspected")
        self.assertFalse(client.saved or client.submitted)

    def test_already_submitted_never_modified(self):
        client = FakeClient({"state": "3", "examBase": {}})
        with tempfile.TemporaryDirectory() as folder:
            result = run_homework(client, lambda q: self.fail("AI called"), Path(folder) / "state.json", True)
        self.assertEqual(result["status"], "already_submitted")
        self.assertFalse(client.saved or client.submitted)

    def test_invalid_last_answer_leaves_entire_paper_unsaved(self):
        client = FakeClient()
        q2 = copy.deepcopy(QUESTION)
        q2["eid"] = "q2"
        client.exam["examBase"]["workExamParts"][0]["questionDtos"].append(q2)
        client.exam["examBase"]["problemNum"] = 2
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SharedTestError):
                run_homework(client, lambda q: [31] if q["eid"] == "q1" else [999], Path(folder) / "state.json", True)
        self.assertFalse(client.saved or client.submitted)

    def test_unsupported_question_prevents_all_writes(self):
        state = copy.deepcopy(EXAM)
        state["examBase"]["workExamParts"][0]["questionDtos"][0]["questionType"]["id"] = 3
        with self.assertRaises(SharedTestError):
            prepare_questions(state["examBase"])

    def test_timed_out_save_is_read_back_before_resending(self):
        client = SharedClient(requests.Session(), IDENTITY, pause=lambda _: None)
        calls = []
        record = {"eid": "q1", "answer": "31", "questionType": 1}
        def post(path, params):
            calls.append(path)
            if path.endswith("saveStudentAnswer"):
                raise requests.ReadTimeout()
            return {"q1": {"answer": "31"}}
        client.post = post
        client.save(record)
        self.assertEqual(len(calls), 2)

    def test_explicit_save_rejection_never_retried(self):
        client = SharedClient(requests.Session(), IDENTITY, pause=lambda _: None)
        with patch.object(client, "post", return_value={"statu": -10}) as post:
            with self.assertRaises(SharedTestError):
                client.save({"eid": "q1", "answer": "31", "questionType": 1})
        self.assertEqual(post.call_count, 1)

    def test_incomplete_readback_blocks_submission(self):
        client = SharedClient(requests.Session(), IDENTITY)
        with patch.object(client, "answers", return_value={}), patch.object(client, "post") as post:
            with self.assertRaises(SharedTestError):
                client.submit([{"eid": "q1", "answer": "31", "questionType": 1}], 1)
            post.assert_not_called()

    def test_submission_timeout_never_blindly_retried(self):
        client = SharedClient(requests.Session(), IDENTITY)
        record = {"eid": "q1", "answer": "31", "questionType": 1}
        with patch.object(client, "answers", return_value={"q1": {"answer": "31"}}), \
             patch.object(client, "post", side_effect=requests.ReadTimeout()) as post, \
             patch.object(client, "open", return_value=EXAM):
            with self.assertRaises(SharedTestError):
                client.submit([record], 1)
            self.assertEqual(post.call_count, 1)

    def test_expired_deadline_blocks_operations(self):
        deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with patch.dict(os.environ, {"ZHS_STOP_AT": deadline}):
            with self.assertRaises(SharedTestError):
                check_deadline()

    def test_chromium_session_cookie_is_not_discarded_as_expired(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.json"
            path.write_text(json.dumps([{"name": "test", "value": "local-test-only", "domain": "example.invalid",
                                         "expires": -1, "session": True}]), encoding="utf-8")
            session = load_session(path)
            cookie = list(session.cookies)[0]
            self.assertIsNone(cookie.expires)
            self.assertFalse(cookie.is_expired())

    def test_inspection_preserves_resume_drafts(self):
        original = {"drafts": [{"eid": "q1", "answer": "31"}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            run_homework(FakeClient(), lambda q: [], path, False)
            self.assertEqual(json.loads(path.read_text()), original)

    def test_resume_uses_ids_after_option_order_changes(self):
        client = FakeClient()
        q2 = copy.deepcopy(QUESTION)
        q2["eid"] = "q2"
        client.exam["examBase"]["workExamParts"][0]["questionDtos"].append(q2)
        client.exam["examBase"]["problemNum"] = 2
        called = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            with self.assertRaises(SharedTestError):
                run_homework(client, lambda q: [31] if q["eid"] == "q1" else [999], path, True)
            for q in client.exam["examBase"]["workExamParts"][0]["questionDtos"]:
                q["questionOptions"].reverse()
            def solve(q):
                called.append(q["eid"])
                return [31]
            run_homework(client, solve, path, True)
        self.assertEqual(called, ["q2"])
        self.assertTrue(client.submitted)

    def test_existing_saved_answer_is_not_overwritten(self):
        client = FakeClient()
        client.answers = lambda ids: {"q1": {"answer": "31"}}
        with tempfile.TemporaryDirectory() as folder:
            run_homework(client, lambda q: self.fail("AI must not overwrite saved answer"), Path(folder) / "s.json", True)
        self.assertFalse(client.saved)
        self.assertTrue(client.submitted)

    def test_completed_paper_keeps_server_score_and_prior_drafts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text(json.dumps({"drafts": [{"eid": "q1", "answer": "31"}]}), encoding="utf-8")
            client = FakeClient({"state": 3, "score": 8, "examBase": {}})
            result = run_homework(client, lambda q: [], path, True)
        self.assertEqual(result["submission"]["score"], 8)
        self.assertEqual(len(result["drafts"]), 1)
        self.assertFalse(client.submitted)

    def test_pending_list_pages_from_zero_and_excludes_meeting_tests(self):
        client = SharedClient(requests.Session(), IDENTITY)
        item = {"id": "stu1", "examId": "exam1", "courseId": 1000096966, "meetCourseType": 0, "reviewType": 0}
        meeting = dict(item, id="meeting", meetCourseType=1)
        with patch.object(client, "read", side_effect=[{"studentHomeworkList": [item, meeting], "next": 1},
                                                     {"studentHomeworkList": [item], "next": -1}]) as read:
            self.assertEqual(len(client.list_homeworks()), 1)
            self.assertEqual(read.call_args_list[0].args[1]["pageNum"], 0)
            self.assertEqual(read.call_args_list[1].args[1]["pageNum"], 1)
            self.assertEqual(read.call_args_list[0].args[1]["flag"], 1)

    def test_security_challenge_stops_before_generating_or_saving(self):
        client = FakeClient()
        def challenge():
            raise SharedTestError("测试服务要求人工验证")
        client.open = challenge
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(SharedTestError, "人工验证"):
                run_homework(client, lambda q: self.fail("AI called during verification"), Path(folder)/"s.json", True)
        self.assertFalse(client.saved or client.submitted)

    def test_save_failure_preserves_draft_and_blocks_submission(self):
        client = FakeClient()
        client.save = lambda record: (_ for _ in ()).throw(requests.ReadTimeout())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"state.json"
            with self.assertRaises(requests.ReadTimeout):
                run_homework(client, lambda q: [31], path, True)
            persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "save_unconfirmed")
        self.assertEqual(persisted["drafts"][0]["answer"], "31")
        self.assertFalse(client.submitted)

    def test_finished_course_accepts_omitted_empty_pending_list(self):
        client = SharedClient(requests.Session(), IDENTITY)
        with patch.object(client, "read", return_value={"next": -1}):
            self.assertEqual(client.list_homeworks(), [])


if __name__ == "__main__":
    unittest.main()
