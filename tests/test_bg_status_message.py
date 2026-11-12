"""자동 감지 알림 문구 (배부 문서는 기한이 문서를 열 때 정해진다고 알림)."""

import unittest

from collectors.workmgmt_collector import WORKMGMT_LIST_SOURCE_REF
from core.models import TaskItem
from ui.app_window import bg_new_tasks_message


class TestBgNewTasksMessage(unittest.TestCase):
    def test_plain_tasks(self):
        self.assertEqual(bg_new_tasks_message([TaskItem(title="a"), TaskItem(title="b")]), "신규 문서/메시지 자동 감지: 2건 캘린더 등록")

    def test_review_tasks(self):
        self.assertEqual(
            bg_new_tasks_message([TaskItem(title="a"), TaskItem(title="b", needs_review=True)]),
            "신규 문서/메시지 자동 감지: 1건 캘린더 등록, 1건 확인 필요",
        )

    def test_distributed_documents(self):
        t = TaskItem(title="a", source_ref=WORKMGMT_LIST_SOURCE_REF, needs_review=True)  # 배부 문서는 확인 필요로 들어옴
        self.assertEqual(
            bg_new_tasks_message([t, TaskItem(title="b")]),
            "신규 문서/메시지 자동 감지: 1건 캘린더 등록, 1건 확인 필요"
            " (배부받은 문서 1건: 문서를 열면 업무인지 판정하고 기한을 정합니다)",
        )

    def test_list_doc_judged_message(self):
        from ui.app_window import list_doc_judged_message
        kept = TaskItem(title="a")
        review = TaskItem(title="b", needs_review=True)
        self.assertEqual(
            list_doc_judged_message([kept, review], [TaskItem(title="c")]),
            "배부 문서 본문 판정: 1건 캘린더 등록, 1건 확인 필요 유지,"
            " 1건 제외 (안내·통보 문서, [🧾 제외된 쪽지·문서]에서 되살릴 수 있음)",
        )


if __name__ == "__main__":
    unittest.main()
