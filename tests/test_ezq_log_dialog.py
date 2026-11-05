"""EzQ 쪽지 판정 기록 창의 화면 없는 규칙 테스트."""

import unittest

from core.models import TaskItem, TaskSource
from ui.ezq_log_dialog import can_register, first_line, review_handler, row_values


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
        self.assertEqual(row_values(rec), ("11-03 15:35", "제외 → 업무", -2, "공지"))



class FakeCollector:
    def __init__(self, owns):
        self.owns = owns

    def is_list_task(self, task):
        return self.owns


class TestReviewHandler(unittest.TestCase):
    """확인 필요 정리 창: 배부 건은 업무관리시스템 수집기가, 쪽지는 쪽지 수집기가 처리."""

    def test_list_task_goes_to_workmgmt_collector(self):
        ezq, wm = object(), FakeCollector(True)
        self.assertIs(review_handler(TaskItem(title="x", source=TaskSource.PORTAL), ezq, wm), wm)

    def test_message_task_goes_to_ezq_collector(self):
        ezq, wm = object(), FakeCollector(False)
        self.assertIs(review_handler(TaskItem(title="x"), ezq, wm), ezq)

    def test_without_workmgmt_collector(self):
        ezq = object()
        self.assertIs(review_handler(TaskItem(title="x"), ezq, None), ezq)


if __name__ == "__main__":
    unittest.main()
