"""우클릭·끌어다 놓기·빠른 입력·실적 요약 기능의 화면 밖 로직 시험."""

import os
import tempfile
import unittest
from datetime import date, timedelta

from core.database import DatabaseManager
from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus
from parsers.quick_entry import parse_quick_entry, strip_date_words
from parsers.rule_parser import RuleBasedExtractor
from ui.app_window import postpone_targets, short_date
from ui.stats_dialog import rate_text

REF = date(2026, 11, 2)  # 월요일


class QuickEntryTest(unittest.TestCase):
    def setUp(self):
        self.ex = RuleBasedExtractor({})

    def parse(self, text, default="2026-11-02"):
        return parse_quick_entry(self.ex, text, default, ref_date=REF)

    def test_relative_date_and_time(self):
        t = self.parse("내일 오후 3시까지 출장계획 제출")
        self.assertEqual((t.title, t.due_date, t.due_time), ("출장계획 제출", "2026-11-03", "15:00"))
        self.assertEqual(t.source, TaskSource.MANUAL)
        self.assertFalse(t.is_new_scan)
        self.assertFalse(t.due_estimated)

    def test_date_words_in_middle(self):
        t = self.parse("출장계획 내일까지 제출")
        self.assertEqual((t.title, t.due_date), ("출장계획 제출", "2026-11-03"))

    def test_next_week_weekday(self):
        t = self.parse("다음 주 수요일 예산 내역서 검토")
        self.assertEqual((t.title, t.due_date), ("예산 내역서 검토", "2026-11-11"))

    def test_bare_time_and_numeric_date(self):
        t = self.parse("11/5 14:00 부서 회의 자료 준비")
        self.assertEqual((t.title, t.due_date, t.due_time), ("부서 회의 자료 준비", "2026-11-05", "14:00"))

    def test_bare_weekday_and_weekly(self):
        t = self.parse("매주 금요일 주간업무실적 제출")
        self.assertEqual(t.due_date, "2026-11-06")
        self.assertEqual(t.recurrence, RecurrenceType.WEEKLY)
        self.assertEqual(t.label, "ROUTINE")

    def test_d_minus_and_days_after(self):
        self.assertEqual(self.parse("D-3 예산 요구서").due_date, "2026-11-05")
        t = self.parse("2일 뒤 오후 2시 반 팀 회의")
        self.assertEqual((t.title, t.due_date, t.due_time), ("팀 회의", "2026-11-04", "14:30"))

    def test_urgent_label(self):
        t = self.parse("긴급 감사 자료 오늘 16:00까지 회신")
        self.assertEqual((t.label, t.priority, t.due_time), ("URGENT", 1, "16:00"))

    def test_no_date_uses_selected_day(self):
        t = self.parse("보고서 작성", default="2026-11-20")
        self.assertEqual((t.title, t.due_date, t.due_time), ("보고서 작성", "2026-11-20", "18:00"))

    def test_hour_only_is_afternoon(self):
        t = self.parse("3시까지 회의실 예약")
        self.assertEqual((t.title, t.due_time), ("회의실 예약", "15:00"))

    def test_numbers_kept_when_no_date(self):
        self.assertEqual(self.parse("부서 회의 1.5배 확대").title, "부서 회의 1.5배 확대")
        self.assertEqual(strip_date_words("부서 회의 1.5배 확대", numeric_dates=False), "부서 회의 1.5배 확대")

    def test_empty(self):
        self.assertIsNone(self.parse("   "))

    def test_only_date_keeps_text_as_title(self):
        self.assertEqual(self.parse("내일").title, "내일")


class RescheduleAndStatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = DatabaseManager(os.path.join(self.tmp, "t.db"))

    def add(self, title, due, source=TaskSource.MANUAL, **kw):
        tid, _ = self.db.add_task(TaskItem(title=title, due_date=due, source=source, content_hash=title, **kw))
        return tid

    def test_reschedule_keeps_time_and_resets_alerts(self):
        tid = self.add("자료 제출", "2026-11-03", due_time="15:00", notified_at="x", snooze_until="y", due_estimated=True)
        moved = self.db.reschedule_task(tid, "2026-11-06")
        self.assertEqual((moved.due_date, moved.due_time), ("2026-11-06", "15:00"))
        self.assertIsNone(moved.notified_at)
        self.assertIsNone(moved.snooze_until)
        self.assertFalse(moved.due_estimated)

    def test_reschedule_rejects_bad_date_and_missing_task(self):
        tid = self.add("자료 제출", "2026-11-03")
        with self.assertRaises(ValueError):
            self.db.reschedule_task(tid, "11/6")
        self.assertIsNone(self.db.reschedule_task(9999, "2026-11-06"))

    def _complete(self, tid, when):
        self.db.set_task_status(tid, TaskStatus.COMPLETED)
        t = self.db.get_task(tid)
        t.completed_at = when.strftime("%Y-%m-%d") + " 10:00:00"
        self.db.update_task(t)

    def test_work_stats(self):
        today = date.today()
        iso = lambda d: d.isoformat()  # noqa: E731
        on_time = self.add("기한 내", iso(today + timedelta(days=1)), TaskSource.PORTAL)
        late = self.add("늦음", iso(today - timedelta(days=1)), TaskSource.MESSENGER)
        old = self.add("3주 전 완료", iso(today - timedelta(days=21)))
        self.add("기한경과 미완료", iso(today - timedelta(days=2)))
        self.add("오늘 마감", iso(today))
        self._complete(on_time, today)
        self._complete(late, today)
        self._complete(old, today - timedelta(days=21))

        s = self.db.get_work_stats(today)
        self.assertEqual(s["week"]["completed"], 2)
        self.assertEqual(s["week"]["on_time"], 1)
        self.assertEqual(s["week"]["created"], 5)
        self.assertEqual(s["week"]["auto"], 2)  # 공문·쪽지만, 수동 등록 제외
        self.assertEqual((s["open"], s["overdue"], s["due_today"]), (2, 1, 1))
        weekly = [c for _, c in s["weekly"]]
        self.assertEqual(len(weekly), 4)
        self.assertEqual(weekly[-1], 2)
        self.assertEqual(weekly[0], 1)
        self.assertEqual(s["weekly"][-1][0], today - timedelta(days=today.weekday()))


class MenuHelpersTest(unittest.TestCase):
    def test_short_date(self):
        self.assertEqual(short_date("2026-11-03"), "11.3(화)")
        self.assertEqual(short_date("bad"), "bad")

    def test_postpone_future_task_moves_from_due(self):
        t = TaskItem(due_date="2026-11-05")
        targets = postpone_targets(t, today=REF)
        self.assertEqual(targets[0][1], "2026-11-06")
        self.assertEqual(targets[1][1], "2026-11-09")  # 다음 주 월요일
        self.assertIn("11.6(금)", targets[0][0])

    def test_postpone_overdue_task_moves_from_today(self):
        targets = postpone_targets(TaskItem(due_date="2026-10-28"), today=REF)
        self.assertEqual(targets[0][1], "2026-11-03")

    def test_postpone_no_duplicate_monday(self):
        # 일요일 마감 → 하루 미루기가 곧 다음 주 월요일이면 한 번만
        targets = postpone_targets(TaskItem(due_date="2026-11-08"), today=REF)
        self.assertEqual([d for _, d in targets], ["2026-11-09"])

    def test_rate_text(self):
        self.assertEqual(rate_text(0, 0), "-")
        self.assertEqual(rate_text(2, 3), "67%")


if __name__ == "__main__":
    unittest.main()
