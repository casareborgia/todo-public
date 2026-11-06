"""EzQ 쪽지 판정 기록 창의 화면 없는 규칙 테스트."""

import unittest

from core.models import TaskItem, TaskSource
from ui.ezq_log_dialog import (
    ORIGIN_DOC,
    ORIGIN_EZQ,
    VIEWS,
    can_register,
    first_line,
    merged_records,
    confirm_handler,
    review_handler,
    row_values,
)


class TestEzqLogDialogRules(unittest.TestCase):
    def test_can_register(self):
        # 지금 그 쪽지의 업무가 없으면 판정·표시와 상관없이 등록 가능 (일반 삭제로 지운 업무도 되살림)
        for rec in (
            {"verdict": "제외", "user_label": ""},
            {"verdict": "등록", "user_label": ""},
            {"verdict": "등록", "user_label": "업무아님"},
            {"verdict": "제외", "user_label": "업무"},
        ):
            self.assertTrue(can_register(rec, task_exists=False), rec)
            self.assertFalse(can_register(rec, task_exists=True), rec)

    def test_first_line_skips_blank_and_truncates(self):
        self.assertEqual(first_line("\n\n  안녕하세요  \n둘째 줄"), "안녕하세요")
        self.assertEqual(len(first_line("가" * 100, width=20)), 20)

    def test_row_values_show_user_label(self):
        rec = {"first_seen": "2026-11-03 15:35:00", "verdict": "제외", "score": -2, "body": "공지", "user_label": "업무"}
        self.assertEqual(row_values(rec), ("11-03 15:35", "쪽지", "제외 → 업무", -2, "공지"))

    def test_merged_records_sorted_and_tagged(self):
        ezq = [{"msg_key": "m1", "first_seen": "2026-11-03 09:00:00", "verdict": "제외", "score": -2, "body": "쪽지"}]
        doc = [{"msg_key": "d1", "first_seen": "2026-11-04 10:00:00", "verdict": "제외", "score": -1, "body": "문서"}]
        recs = merged_records(ezq, doc)
        self.assertEqual([r["msg_key"] for r in recs], ["d1", "m1"])
        self.assertEqual([r["origin"] for r in recs], [ORIGIN_DOC, ORIGIN_EZQ])
        self.assertEqual([r["row_id"] for r in recs], ["문서:d1", "쪽지:m1"])
        self.assertEqual(row_values(recs[0])[1], "문서")

    def test_rows_dispatch_by_origin(self):
        ezq = [{"msg_key": "abc", "first_seen": "2026-11-03 09:00:00"}]
        doc = [{"msg_key": "abc", "first_seen": "2026-11-03 09:00:00"}]
        ids = {r["row_id"] for r in merged_records(ezq, doc)}
        self.assertEqual(ids, {"쪽지:abc", "문서:abc"})

    def test_view_names(self):
        self.assertEqual(
            [v[0] for v in VIEWS], ["제외된 쪽지·문서", "'업무 아님'으로 표시한 쪽지·문서", "전체 판정 기록"]
        )



class FakeCollector:
    def __init__(self, owns, doc=False):
        self.owns = owns
        self.doc = doc

    def is_list_task(self, task):
        return self.owns

    def is_doc_task(self, task):
        return self.doc


class TestReviewHandler(unittest.TestCase):
    """확인 필요 정리 창: 배부 건은 업무관리시스템 수집기가, 쪽지는 쪽지 수집기가 처리."""

    def test_list_task_goes_to_workmgmt_collector(self):
        ezq, wm = object(), FakeCollector(True)
        self.assertIs(review_handler(TaskItem(title="x", source=TaskSource.PORTAL), ezq, wm), wm)

    def test_message_task_goes_to_ezq_collector(self):
        ezq, wm = object(), FakeCollector(False)
        self.assertIs(review_handler(TaskItem(title="x"), ezq, wm), ezq)

    def test_review_handler_doc_task(self):
        ezq, wm = object(), FakeCollector(False, doc=True)
        self.assertIs(review_handler(TaskItem(title="x", source=TaskSource.PORTAL), ezq, wm), wm)

    def test_without_workmgmt_collector(self):
        ezq = object()
        self.assertIs(review_handler(TaskItem(title="x"), ezq, None), ezq)


class TestConfirmHandler(unittest.TestCase):
    def test_list_task_goes_to_ezq_collector(self):
        ezq, wm = object(), FakeCollector(True)
        self.assertIs(confirm_handler(TaskItem(title="x"), ezq, wm), ezq)

    def test_doc_only_task_goes_to_workmgmt_collector(self):
        ezq, wm = object(), FakeCollector(False, doc=True)
        self.assertIs(confirm_handler(TaskItem(title="x"), ezq, wm), wm)

    def test_list_and_doc_task_goes_to_ezq_collector(self):
        ezq, wm = object(), FakeCollector(True, doc=True)
        self.assertIs(confirm_handler(TaskItem(title="x"), ezq, wm), ezq)


if __name__ == "__main__":
    unittest.main()
