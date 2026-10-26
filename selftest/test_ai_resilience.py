"""Offline AI regressions. Never imports the app logger or calls real services."""
import ast
import json
import re
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from ObjDict import ObjDict

SOURCE = Path(__file__).resolve().parents[1] / "fucker.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
ns = dict(requests=requests, time=time, logger=Mock(), EXAM_KEY=b"test", Path=Path,
          json=json, hashlib=hashlib, randint=lambda a, b: a, progressBar=Mock())
for name, methods in {
    "ExamCtx": {"saveAnswer", "submitExam", "startFuck", "_startExam", "getAnswer", "verifiedAnswerText", "getQuestionAnswer",
                "_draftPath", "_draftFingerprint", "_readDrafts", "_writeDrafts", "getDraftAnswer", "rememberDraft", "closeDrafts"},
}.items():
    source_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    cls = ast.ClassDef(name=name, bases=[], keywords=[], body=[n for n in source_class.body if isinstance(n, ast.FunctionDef) and n.name in methods], decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(SOURCE), "exec"), ns)
ExamCtx = ns["ExamCtx"]

class AIResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = patch.dict(ns, {"getRealPath": lambda path: str(Path(self.temp.name) / path)})
        self.paths.start()
        self.addCleanup(self.paths.stop)

    def question(self):
        return {"id": 4, "questionType": 14, "version": 1, "content": "stem",
                "optionVos": [{"id": 10, "content": "yes"}, {"id": 20, "content": "no"}]}

    def context(self, effects):
        ctx = ExamCtx()
        ctx.courseId, ctx.examTestId, ctx.examPaperId = 1, 2, 3
        ctx.knowledgeId = 5
        ctx.fucker = Mock()
        ctx.fucker.zhidaoAiExamQuery.side_effect = effects
        return ctx

    def test_timeout_with_successful_server_save(self):
        saved = {"data": {"questionType": 2, "userAnswerVo": [{"questionId": 4, "answer": "20#@#10"}]}}
        ctx = self.context([requests.ReadTimeout(), saved])
        self.assertTrue(ctx.saveAnswer(4, [10, 20]))
        self.assertEqual(ctx.fucker.zhidaoAiExamQuery.call_count, 2)

    def test_missing_server_save_is_retried(self):
        ctx = self.context([requests.ReadTimeout(), {"data": {"questionType": 2, "userAnswerVo": None}}, {"code": 0}])
        with patch.object(time, "sleep"):
            self.assertTrue(ctx.saveAnswer(4, [10]))
        self.assertEqual(ctx.fucker.zhidaoAiExamQuery.call_count, 3)

    def test_server_rejection_is_not_blindly_retried(self):
        ctx = self.context([ValueError("code: 401")])
        self.assertFalse(ctx.saveAnswer(4, [10]))
        self.assertEqual(ctx.fucker.zhidaoAiExamQuery.call_count, 1)

    def test_retries_are_bounded(self):
        ctx = self.context([requests.ReadTimeout(), requests.ReadTimeout()] * 3)
        with patch.object(time, "sleep"):
            self.assertFalse(ctx.saveAnswer(4, [10]))
        self.assertEqual(ctx.fucker.zhidaoAiExamQuery.call_count, 6)

    def test_fill_cache_contains_text_not_option_id(self):
        q = {"questionType": 3, "optionVos": [{"id": 10, "sort": 1, "content": "correct text", "isCorrect": 1}], "userAnswerVo": [{"isCorrect": 0, "answer": "10"}]}
        ctx = ExamCtx()
        ctx.allAnswerCache = {"1": {"answer": "10", "questionDict": q}}
        self.assertEqual(ctx.getAnswer(1)["answer"], "correct text")
        q["optionVos"][0]["content"] = None
        self.assertIsNone(ctx.getAnswer(1))

    def test_heartbeat_stops_on_any_exception(self):
        ctx = ExamCtx()
        ctx.examStopped = False
        ctx._startExam = Mock(side_effect=RuntimeError("stop"))
        with self.assertRaises(RuntimeError):
            ctx.startFuck([])
        self.assertTrue(ctx.examStopped)

    def test_draft_survives_process_context_restart(self):
        ctx = self.context([])
        ctx.rememberDraft(self.question(), [10])
        restarted = self.context([])
        self.assertEqual(restarted.getDraftAnswer(self.question()), [10])

    def test_changed_question_never_reuses_draft(self):
        ctx = self.context([])
        q = self.question()
        ctx.rememberDraft(q, [10])
        q["content"] = "different stem"
        self.assertIsNone(ctx.getDraftAnswer(q))

    def test_option_shuffle_keeps_id_mapping(self):
        ctx = self.context([])
        q = self.question()
        ctx.rememberDraft(q, [10])
        q["optionVos"].reverse()
        self.assertEqual(ctx.getDraftAnswer(q), [10])

    def test_submitted_draft_is_not_reused_as_verified_answer(self):
        ctx = self.context([])
        ctx.rememberDraft(self.question(), [10])
        ctx.closeDrafts()
        self.assertIsNone(ctx.getDraftAnswer(self.question()))

    def test_fill_draft_preserves_text(self):
        ctx = self.context([])
        q = self.question()
        q["questionType"] = 3
        ctx.rememberDraft(q, ["first text", "second text"])
        self.assertEqual(ctx.getDraftAnswer(q), ["first text", "second text"])

    def test_verified_cache_has_priority_over_ungraded_draft(self):
        ctx = self.context([])
        ctx.rememberDraft(self.question(), [10])
        ctx.getAnswer = Mock(return_value={"answer": "20"})
        with patch.object(time, "sleep"):
            answer, note = ctx.getQuestionAnswer(self.question())
        self.assertEqual(answer, ["20"])
        self.assertEqual(note, "cached")

    def test_save_failure_preserves_generated_answer_and_stops_submit(self):
        ctx = self.context([])
        ctx.progress_view = False
        ctx.readAnswerCache = Mock()
        ctx.openExam = Mock()
        ctx.getSheetContent = Mock(return_value=[{"questionId": 4, "version": 1}])
        ctx.getQuestionContent = Mock(return_value=ObjDict(self.question()))
        ctx.getQuestionAnswer = Mock(return_value=([10], "generated"))
        ctx.saveAnswer = Mock(return_value=False)
        ctx.lastSaveError = "timeout"
        ctx.submitExam = Mock()
        with self.assertRaises(RuntimeError):
            ctx.startFuck([])
        self.assertEqual(self.context([]).getDraftAnswer(self.question()), [10])
        ctx.submitExam.assert_not_called()
        self.assertTrue(ctx.examStopped)

    def test_submit_timeout_is_not_blindly_retried(self):
        ctx = self.context([requests.ReadTimeout()])
        with self.assertRaisesRegex(RuntimeError, "已停止重发"):
            ctx.submitExam()
        self.assertEqual(ctx.fucker.zhidaoAiExamQuery.call_count, 1)
        self.assertTrue(ctx.examStopped)

    def test_submit_rejection_is_not_retried(self):
        ctx = self.context([ValueError("rejected")])
        with self.assertRaises(ValueError):
            ctx.submitExam()
        self.assertEqual(ctx.fucker.zhidaoAiExamQuery.call_count, 1)
        self.assertTrue(ctx.examStopped)

if __name__ == "__main__":
    unittest.main()
