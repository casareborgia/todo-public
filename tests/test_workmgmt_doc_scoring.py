"""업무관리시스템 문서 본문 판정 (EzQ 쪽지와 같은 점수표) 테스트."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import timedelta

from collectors.workmgmt_collector import doc_key
from collectors.workmgmt_window import TEXT
from core.database import DatabaseManager
from core.models import TaskItem, TaskSource
from parsers.message_scorer import score_message
from tests.test_workmgmt_collector import BODY, TITLE, DistributedListBase, lrow

NOTICE_BODY = """가상 회사 머리말
수신자 전 직원
제 목
2026년 하반기 인사발령 알림
하반기 인사발령 사항을 다음과 같이 알려드립니다.
붙임 1. 발령 명단 1부. 끝.
시행 인사부-321 (2026.11.05.)
"""
AMBIGUOUS_BODY = """가상 회사 머리말
제 목
사무용품 수요 조사
부서별 필요 수량을 검토해 주시기 바랍니다.
시행 총무부-55 (2026.11.05.)
"""
NOTICE_TITLE = "2026년 하반기 인사발령 알림"


class WorkmgmtJudgmentDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "t.db")
        self.db = DatabaseManager(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_and_list_judgment(self):
        self.db.record_workmgmt_doc("k1", "2026-11-06 09:00:00", "제목", "제외")
        self.db.record_workmgmt_judgment("k1", "제외", -1, ["공지·공유 -3"], "본문")
        rows = self.db.list_workmgmt_judgments(verdict="제외", user_label="")
        self.assertEqual([r["msg_key"] for r in rows], ["k1"])
        self.assertEqual(rows[0]["reasons"], ["공지·공유 -3"])
        self.assertEqual(rows[0]["score"], -1)
        self.assertEqual(rows[0]["title"], "제목")
        self.assertEqual(rows[0]["body"], "본문")

    def test_user_label_kept_when_rejudged(self):
        self.db.record_workmgmt_doc("k1", "2026-11-06 09:00:00", "제목", "제외")
        self.db.record_workmgmt_judgment("k1", "제외", -1, ["a"], "본문")
        self.db.set_workmgmt_user_label("k1", "업무")
        self.db.record_workmgmt_judgment("k1", "업무", 5, ["b"], "본문2")
        got = self.db.get_workmgmt_judgment("k1")
        self.assertEqual(got["user_label"], "업무")
        self.assertEqual(got["score"], 5)

    def test_judgment_inserts_row_when_missing_and_skips_list_keys(self):
        self.db.record_workmgmt_judgment("k2", "업무", 4, [], "본문")
        self.db.record_workmgmt_judgment("LIST:x", "업무", 4, [], "본문")
        self.assertEqual([r["msg_key"] for r in self.db.list_workmgmt_judgments()], ["k2"])
        self.assertEqual(self.db.get_workmgmt_judgment("k2")["title"], "")
        self.assertIsNone(self.db.get_workmgmt_judgment("none"))

    def test_old_workmgmt_rows_have_empty_judgment(self):
        old_path = os.path.join(self.tmp, "old.db")
        conn = sqlite3.connect(old_path)
        conn.execute(
            "CREATE TABLE workmgmt_docs (doc_key TEXT PRIMARY KEY, first_seen TEXT NOT NULL,"
            " title TEXT DEFAULT '', result TEXT DEFAULT '')"
        )
        conn.execute("INSERT INTO workmgmt_docs VALUES ('old', '2026-01-01 00:00:00', 't', 'r')")
        conn.commit()
        conn.close()
        db = DatabaseManager(old_path)
        conn = sqlite3.connect(old_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(workmgmt_docs)")}
        conn.close()
        self.assertTrue({"verdict", "score", "reasons", "body", "user_label"} <= cols)
        self.assertEqual(db.list_workmgmt_judgments(), [])
        self.assertFalse(db.get_workmgmt_judgment("old")["verdict"])


class WorkmgmtDocScoringTest(DistributedListBase):
    """문서 본문을 쪽지 점수표로 판정 (배부 연결 문서는 판정하지 않음)."""

    def open_doc(self, body, hwnd=1, title=TITLE):
        self.windows.set(hwnd, title, (TEXT, body))
        return self.collector.collect_and_register()

    def test_explicit_request_doc_registered(self):
        created = self.open_doc(BODY)
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].needs_review)
        got = self.db.get_workmgmt_judgment(doc_key(BODY))
        self.assertEqual(got["verdict"], "등록")
        self.assertEqual(got["score"], 9)
        self.assertEqual(got["body"], BODY.strip())
        doc = self.db.get_workmgmt_doc(doc_key(BODY))
        self.assertEqual(doc["result"], "등록")
        self.assertEqual(doc["task_id"], created[0].id)

    def test_notice_doc_excluded_and_logged(self):
        self.assertEqual(self.open_doc(NOTICE_BODY, title="인사부-321호 - Microsoft Edge"), [])
        self.assertEqual(self.open_tasks(), [])
        rows = self.db.list_workmgmt_judgments(verdict="제외")
        self.assertEqual([r["msg_key"] for r in rows], [doc_key(NOTICE_BODY)])
        self.assertEqual(rows[0]["score"], -6)
        self.assertEqual(rows[0]["title"], NOTICE_TITLE)   # 업무가 없어도 본문 제목
        self.assertEqual(self.db.get_workmgmt_doc(doc_key(NOTICE_BODY))["result"], "제외")

    def test_ambiguous_doc_needs_review(self):
        created = self.open_doc(AMBIGUOUS_BODY)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].needs_review)
        self.assertTrue(self.db.get_task(created[0].id).needs_review)
        doc = self.db.get_workmgmt_doc(doc_key(AMBIGUOUS_BODY))
        self.assertEqual(doc["result"], "확인 필요")
        self.assertEqual(doc["task_id"], created[0].id)
        self.assertEqual(self.db.get_workmgmt_judgment(doc_key(AMBIGUOUS_BODY))["verdict"], "확인 필요")

    def test_distributed_doc_body_is_not_rescored(self):
        c = self.collector_with_rows([lrow(no="인사부-321", title=NOTICE_TITLE, role="업무담당자")])
        c.collect_and_register()
        self.list_windows.rows = []
        self.open_doc(NOTICE_BODY)
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertFalse(tasks[0].needs_review)
        self.assertFalse(self.db.get_workmgmt_judgment(doc_key(NOTICE_BODY))["verdict"])
        self.assertEqual(self.db.list_workmgmt_judgments(), [])
        self.assertEqual(self.db.get_workmgmt_doc(doc_key(NOTICE_BODY))["result"], "등록")

    def test_dismissed_distribution_still_blocks_body(self):
        c = self.collector_with_rows([lrow(no="감사실-456", role="업무관리자", title="2026년 내부통제 자가진단 결과 제출 시행 알림")])
        c.collect_and_register()
        c.dismiss_task(self.db.find_task_by_keys(["DOC:감사실-456"]))
        self.list_windows.rows = []
        self.assertEqual(self.open_doc(BODY), [])
        self.assertEqual(self.open_tasks(), [])
        self.assertEqual(self.db.get_workmgmt_doc(doc_key(BODY))["result"], "업무 아님(배부 목록)")
        self.assertEqual(self.db.list_workmgmt_judgments(), [])

    def test_excluded_doc_not_rejudged_next_tick(self):
        self.open_doc(NOTICE_BODY)
        self.open_doc(NOTICE_BODY, hwnd=2, title="다른 창")   # 같은 문서를 다른 창에서 다시 열어도
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(self.open_tasks(), [])
        self.assertEqual(len(self.db.list_workmgmt_judgments()), 1)

    def test_parse_document_default_call_unchanged(self):
        # 판정 없이 부르면 지금처럼 단어 판정 (공지 본문도 업무 목록이 나옴)
        from datetime import datetime
        items = self.collector.parse_document(NOTICE_BODY, datetime(2026, 11, 5, 9, 0))
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].needs_review)

    def test_force_ignores_judgment(self):
        from datetime import datetime
        from parsers.message_scorer import score_message
        judged = score_message(AMBIGUOUS_BODY)
        now = datetime(2026, 11, 5, 9, 0)
        self.assertTrue(self.collector.parse_document(AMBIGUOUS_BODY, now, judged=judged)[0].needs_review)
        self.assertFalse(self.collector.parse_document(AMBIGUOUS_BODY, now, force=True, judged=judged)[0].needs_review)


DUE_REVIEW_BODY = """가상 회사 머리말
제 목
사무용품 수요 조사
회의 결과를 공유드립니다.
부서별 수량 검토는 '26.11.7.(금)까지
시행 총무부-57 (2026.11.05.)
"""


class WorkmgmtDocDecisionTest(DistributedListBase):
    """판정된 문서에 대한 사용자 정답 표시 (업무로 등록 / 업무 맞음 / 업무 아님 / 되돌리기)."""

    def open_doc(self, body, hwnd=1, title=TITLE):
        self.windows.set(hwnd, title, (TEXT, body))
        return self.collector.collect_and_register()

    def test_register_excluded_doc(self):
        self.open_doc(NOTICE_BODY)
        key = doc_key(NOTICE_BODY)
        self.assertFalse(self.collector.has_task(key))
        created = self.collector.register_as_task(key)
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].needs_review)
        self.assertEqual(len(self.open_tasks()), 1)
        got = self.db.get_workmgmt_judgment(key)
        self.assertEqual(got["user_label"], DatabaseManager.EZQ_LABEL_TASK)
        self.assertEqual(got["task_id"], created[0].id)
        self.assertTrue(self.collector.has_task(key))
        self.assertTrue(self.collector.is_doc_task(created[0]))

    def test_register_missing_record_returns_empty(self):
        self.assertEqual(self.collector.register_as_task("none"), [])
        self.assertFalse(self.collector.has_task("none"))

    def test_register_excluded_doc_then_reopen_not_duplicated(self):
        self.open_doc(NOTICE_BODY)
        key = doc_key(NOTICE_BODY)
        created = self.collector.register_as_task(key)
        self.db.delete_task(created[0].id)
        self.assertEqual(self.open_doc(NOTICE_BODY, hwnd=2, title="다른 창"), [])
        self.assertEqual(self.open_tasks(), [])
        self.assertFalse(self.collector.has_task(key))
        self.assertEqual(len(self.collector.register_as_task(key)), 1)
        self.assertTrue(self.collector.has_task(key))

    def test_confirm_review_doc_keeps_explicit_due(self):
        created = self.open_doc(DUE_REVIEW_BODY)
        self.assertEqual(len(created), 1)
        task = self.db.get_task(created[0].id)
        self.assertTrue(task.needs_review)
        self.assertEqual((task.due_date, task.due_estimated), ("2026-11-07", False))
        self.clock.now = self.clock.now + timedelta(days=3)
        confirmed = self.collector.confirm_review(task)
        self.assertFalse(confirmed.needs_review)
        got = self.db.get_task(task.id)
        self.assertFalse(got.needs_review)
        self.assertEqual(got.due_date, "2026-11-07")
        self.assertEqual(self.db.get_workmgmt_judgment(doc_key(DUE_REVIEW_BODY))["user_label"], "업무")

    def test_confirm_review_doc_reestimates_due(self):
        created = self.open_doc(AMBIGUOUS_BODY)
        task = self.db.get_task(created[0].id)
        self.assertTrue(task.due_estimated)
        self.clock.now = self.clock.now + timedelta(days=3)
        expected = self.parser.extractor.extract_due("", ref_date=self.clock.now.date())[:2]
        self.assertNotEqual((task.due_date, task.due_time), expected)
        self.collector.confirm_review(task)
        got = self.db.get_task(task.id)
        self.assertFalse(got.needs_review)
        self.assertEqual((got.due_date, got.due_time), expected)
        self.assertEqual(self.db.get_workmgmt_judgment(doc_key(AMBIGUOUS_BODY))["user_label"], "업무")

    def test_review_reason_doc(self):
        created = self.open_doc(AMBIGUOUS_BODY)
        reasons = self.db.get_workmgmt_judgment(doc_key(AMBIGUOUS_BODY))["reasons"]
        self.assertEqual(self.collector.review_reason(created[0]), ", ".join(reasons))
        self.assertFalse(self.collector.review_reason(created[0]).startswith("판정 근거"))

    def test_dismiss_review_doc_and_undo(self):
        created = self.open_doc(AMBIGUOUS_BODY)
        key = doc_key(AMBIGUOUS_BODY)
        task = self.db.get_task(created[0].id)
        self.assertTrue(self.collector.is_doc_task(task))
        self.assertFalse(self.collector.is_list_task(task))
        self.assertEqual(self.collector.dismiss_task(task), key)
        self.assertEqual(self.open_tasks(), [])
        got = self.db.get_workmgmt_judgment(key)
        self.assertEqual(got["user_label"], "업무아님")
        self.assertIsNone(got["task_id"])
        self.assertEqual(got["result"], "업무 아님")
        self.assertFalse(self.collector.has_task(key))
        # 같은 문서 창을 다시 열어도 다시 만들지 않음
        self.assertEqual(self.open_doc(AMBIGUOUS_BODY, hwnd=2, title="다른 창"), [])
        self.assertEqual(self.open_tasks(), [])
        new_id = self.collector.undo_dismiss(task, key)
        self.assertIsNotNone(new_id)
        tasks = self.open_tasks()
        self.assertEqual([t.id for t in tasks], [new_id])
        self.assertTrue(tasks[0].needs_review)
        got = self.db.get_workmgmt_judgment(key)
        self.assertEqual(got["user_label"], "")
        self.assertEqual(got["task_id"], new_id)
        self.assertEqual(got["result"], "확인 필요")
        self.assertTrue(self.collector.has_task(key))

    def test_list_dismiss_unchanged(self):
        c = self.collector_with_rows([lrow(no="감사실-456", role="업무관리자", title="2026년 내부통제 자가진단 결과 제출 시행 알림")])
        c.collect_and_register()
        t = self.db.find_task_by_keys(["DOC:감사실-456"])
        self.assertFalse(c.is_doc_task(t))
        self.assertEqual(c.review_reason(t), "업무관리자로 지정된 배부 문서 (처리구분: 업무관리자)")
        key = c.dismiss_task(t)
        self.assertEqual(key, "LIST:DOC:감사실-456")
        self.assertEqual(self.open_tasks(), [])
        self.assertEqual(self.db.get_workmgmt_doc(key)["result"], "업무 아님")
        self.assertEqual(self.db.get_workmgmt_doc(key)["user_label"], "")
        new_id = c.undo_dismiss(t, key)
        rec = self.db.get_workmgmt_doc(key)
        self.assertEqual((rec["result"], rec["task_id"]), ("확인 필요", new_id))
        self.assertEqual(rec["user_label"], "")
        self.assertEqual([x.id for x in self.open_tasks()], [new_id])

    def test_dismiss_non_doc_task_returns_none(self):
        from core.models import TaskItem, TaskSource
        t = TaskItem(title="x", source=TaskSource.MANUAL)
        t.id = self.db.add_task(t)[0]
        self.assertFalse(self.collector.is_doc_task(t))
        self.assertIsNone(self.collector.dismiss_task(t))
        self.assertEqual(len(self.open_tasks()), 1)


# 제외 점수 공지형 본문인데 배부 목록 행과 문서번호가 같음 (배부 문서: 본문 회신기한이 업무 기한)
DIST_NOTICE_BODY = """가상 회사 머리말
수신자 전 직원
제 목
2026년 하반기 보안점검 결과 알림
하반기 보안점검 결과를 다음과 같이 알려드립니다.
점검 결과 공개 기간은 2026.11.20.까지입니다.
붙임 1. 점검 결과 1부. 끝.
시행 보안부-321 (2026.11.03.)
"""
DIST_NOTICE_TITLE = "2026년 하반기 보안점검 결과 알림"


class WorkmgmtExcludedDistributedDocTest(DistributedListBase):
    """배부 목록보다 먼저(또는 같은 확인에서) 읽혀 제외된 배부 문서도 본문 기한으로 확정 (최종 검토 지적 1)."""

    def setUp(self):
        super().setUp()
        self.assertEqual(score_message(DIST_NOTICE_BODY).verdict, "제외")

    def dist_row(self):
        return lrow(no="보안부-321", title=DIST_NOTICE_TITLE, role="업무담당자")

    def assert_one_task_with_body_due(self):
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        t = tasks[0]
        self.assertEqual(t.due_date, "2026-11-20")
        self.assertFalse(t.due_estimated)
        self.assertFalse(t.needs_review)
        self.assertIn("업무관리시스템 문서", t.source_ref)
        key = doc_key(DIST_NOTICE_BODY)
        doc = self.db.get_workmgmt_doc(key)
        self.assertEqual((doc["result"], doc["task_id"]), ("등록", t.id))
        self.assertTrue(self.collector.has_task(key))
        return t

    def test_doc_tick_then_list_tick(self):
        c = self.collector_with_rows([])
        self.windows.set(1, TITLE, (TEXT, DIST_NOTICE_BODY))
        self.assertEqual(c.collect_and_register(), [])
        self.list_windows.rows = [self.dist_row()]
        c.collect_and_register()
        t = self.assert_one_task_with_body_due()
        # 다른 창으로 다시 열어도 그대로
        self.windows.set(2, "다른 창", (TEXT, DIST_NOTICE_BODY))
        c.collect_and_register()
        self.assertEqual([x.id for x in self.open_tasks()], [t.id])

    def test_doc_and_list_same_tick(self):
        c = self.collector_with_rows([self.dist_row()])
        self.windows.set(1, TITLE, (TEXT, DIST_NOTICE_BODY))
        created = c.collect_and_register()
        t = self.assert_one_task_with_body_due()
        self.assertEqual([x.id for x in created], [t.id])
        self.assertEqual(created[0].due_date, "2026-11-20")

    def test_list_tick_then_doc_tick_unchanged(self):
        c = self.collector_with_rows([self.dist_row()])
        c.collect_and_register()
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, DIST_NOTICE_BODY))
        c.collect_and_register()
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_date, "2026-11-20")
        self.assertFalse(tasks[0].due_estimated)

    def test_unrelated_excluded_doc_not_linked(self):
        c = self.collector_with_rows([])
        self.windows.set(1, TITLE, (TEXT, NOTICE_BODY))
        c.collect_and_register()
        self.list_windows.rows = [self.dist_row()]
        c.collect_and_register()
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0].due_estimated)
        self.assertEqual(self.db.get_workmgmt_doc(doc_key(NOTICE_BODY))["result"], "제외")


class WorkmgmtRegisterFoldTaskIdTest(DistributedListBase):
    """등록 판정 업무가 쪽지 업무와 하나로 합쳐져 지워지면 남은 업무 번호를 기록 (최종 검토 지적 3)."""

    def test_doc_record_points_to_kept_task_after_fold(self):
        desc = "감사실 내부통제 자가진단 공문 왔습니다. 11/7까지 제출 부탁"
        msg = TaskItem(title="내부통제 자가진단 결과 제출 부탁드립니다", description=desc, source=TaskSource.MESSENGER,
                       due_date="2026-11-07", external_key=f"EZQ:{doc_key(desc)}|0", ezq_key=doc_key(desc))
        msg_id = self.db.add_task(msg)[0]
        real = self.db.find_similar_open_task
        calls = []

        def similar(task, source):
            calls.append(task)
            return None if len(calls) == 1 else real(task, source)  # 등록 전 확인만 놓친 것처럼

        self.db.find_similar_open_task = similar
        c = self.collector_with_rows([])
        self.windows.set(1, TITLE, (TEXT, BODY))
        c.collect_and_register()
        self.assertEqual([t.id for t in self.open_tasks()], [msg_id])  # 새 업무는 쪽지 업무에 합쳐 지워짐
        doc = self.db.get_workmgmt_doc(doc_key(BODY))
        self.assertEqual(doc["task_id"], msg_id)
        self.assertTrue(c.has_task(doc_key(BODY)))


if __name__ == "__main__":
    unittest.main()
