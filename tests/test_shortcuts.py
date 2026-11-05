"""여러 업무 선택·삭제와 단축키 안내의 화면 밖 로직 시험."""

import os
import tempfile
import unittest

from core.database import DatabaseManager
from core.models import TaskItem
from ui.shortcuts import SHORTCUTS, delete_confirm_message, shortcut_help_text, tasks_in_selection


def _task(task_id, title="업무"):
    return TaskItem(id=task_id, title=title, due_date="2026-11-04")


class SelectionTest(unittest.TestCase):
    def test_keeps_order_and_skips_unknown_rows(self):
        m = {"rt_1": _task(1, "가"), "rt_2": _task(2, "나"), "rt_3": _task(3, "다")}
        got = tasks_in_selection(["rt_3", "없음", "rt_1"], m)
        self.assertEqual([t.id for t in got], [3, 1])

    def test_skips_unsaved_and_duplicates(self):
        same = _task(5)
        m = {"a": same, "b": same, "c": _task(None)}
        self.assertEqual([t.id for t in tasks_in_selection(["a", "b", "c"], m)], [5])

    def test_empty(self):
        self.assertEqual(tasks_in_selection([], {}), [])


class ConfirmMessageTest(unittest.TestCase):
    def test_single_keeps_old_wording(self):
        self.assertEqual(delete_confirm_message([_task(1, "보고서")]), "'보고서' 업무를 삭제하시겠습니까?")

    def test_many_lists_titles_and_rest_count(self):
        msg = delete_confirm_message([_task(i, f"업무{i}") for i in range(1, 8)], max_titles=5)
        self.assertIn("7건", msg)
        self.assertIn("• 업무5", msg)
        self.assertNotIn("• 업무6", msg)
        self.assertIn("외 2건", msg)


class MultiDeleteDbTest(unittest.TestCase):
    """고른 업무만 지워지고 나머지는 남는지 (메인 창은 delete_task를 건마다 부름)."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseManager(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_only_selected_deleted(self):
        ids = [self.db.add_task(TaskItem(title=f"업무{i}", due_date="2026-11-04"), skip_if_duplicate=False)[0] for i in range(4)]
        m = {f"rt_{i}": self.db.get_task(i) for i in ids}
        chosen = tasks_in_selection([f"rt_{ids[0]}", f"rt_{ids[2]}"], m)
        for t in chosen:
            self.assertTrue(self.db.delete_task(t.id))
        left = [i for i in ids if self.db.get_task(i)]
        self.assertEqual(left, [ids[1], ids[3]])


class HelpTextTest(unittest.TestCase):
    def test_every_shortcut_listed_under_its_scope(self):
        text = shortcut_help_text()
        for keys, action, where in SHORTCUTS:
            self.assertIn(f"{keys}  —  {action}", text)
            self.assertIn(f"[{where}]", text)

    def test_delete_and_keys_present(self):
        keys = {k for k, _a, _w in SHORTCUTS}
        for k in ("Delete", "Enter", "Space", "Ctrl+A", "Esc", "Ctrl+L", "Ctrl+Enter", "F1"):
            self.assertIn(k, keys)


if __name__ == "__main__":
    unittest.main()
