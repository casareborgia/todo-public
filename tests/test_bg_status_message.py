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
        t = TaskItem(title="a", source_ref=WORKMGMT_LIST_SOURCE_REF)
        self.assertEqual(
            bg_new_tasks_message([t, TaskItem(title="b")]),
            "신규 문서/메시지 자동 감지: 2건 캘린더 등록 (배부받은 문서 1건: 문서를 열면 기한이 정해집니다)",
        )


if __name__ == "__main__":
    unittest.main()
