"""반복 업무 미리보기와 반복을 고를 때 라벨 바꾸기 테스트."""

from datetime import date
import unittest

from core.models import RecurrenceType, TaskItem, TaskStatus
from core.recurrence import label_for_recurrence, occurrence_dates, preview_tasks

TODAY = date(2026, 11, 5)


class TestOccurrenceDates(unittest.TestCase):
    def test_weekly_fills_month_after_due(self):
        got = occurrence_dates(date(2026, 11, 6), RecurrenceType.WEEKLY, date(2026, 11, 1), date(2026, 12, 12), TODAY)
        self.assertEqual(got, [date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27),
                               date(2026, 12, 4), date(2026, 12, 11)])

    def test_daily_starts_after_due_and_not_before_today(self):
        got = occurrence_dates(date(2026, 11, 1), RecurrenceType.DAILY, date(2026, 11, 1), date(2026, 11, 8), TODAY)
        self.assertEqual(got[0], date(2026, 11, 5))   # 지난 날짜는 미리보기 안 함
        self.assertEqual(got[-1], date(2026, 11, 8))

    def test_monthly_keeps_day_of_month(self):
        got = occurrence_dates(date(2026, 1, 31), RecurrenceType.MONTHLY, date(2026, 1, 1), date(2026, 4, 30),
                               date(2026, 1, 1))
        self.assertEqual(got, [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_none_and_far_past_due(self):
        self.assertEqual(occurrence_dates(date(2026, 11, 6), RecurrenceType.NONE, date(2026, 11, 1),
                                          date(2026, 12, 12), TODAY), [])
        got = occurrence_dates(date(2020, 1, 3), RecurrenceType.DAILY, date(2026, 11, 1), date(2026, 11, 7), TODAY)
        self.assertEqual(got, [date(2026, 11, d) for d in (5, 6, 7)])


class TestPreviewTasks(unittest.TestCase):
    def task(self, **kw):
        base = dict(title="주간 실적 제출", due_date="2026-11-06", due_time="17:00", label="ROUTINE",
                    recurrence=RecurrenceType.WEEKLY)
        base.update(kw)
        t = TaskItem(**base)
        t.id = 7
        return t

    def test_previews_are_marked_copies(self):
        got = preview_tasks([self.task()], date(2026, 11, 1), date(2026, 11, 30), TODAY)
        self.assertEqual(sorted(got), ["2026-11-13", "2026-11-20", "2026-11-27"])
        p = got["2026-11-13"][0]
        self.assertTrue(p.is_preview)
        self.assertEqual((p.id, p.title, p.due_time, p.label), (7, "주간 실적 제출", "17:00", "ROUTINE"))

    def test_completed_task_has_no_previews(self):
        t = self.task(status=TaskStatus.COMPLETED)
        self.assertEqual(preview_tasks([t], date(2026, 11, 1), date(2026, 11, 30), TODAY), {})

    def test_skips_date_with_real_task_of_same_title(self):
        real = self.task(due_date="2026-11-13")
        real.id = 8
        got = preview_tasks([self.task(), real], date(2026, 11, 1), date(2026, 11, 30), TODAY)
        self.assertNotIn("2026-11-13", got)
        self.assertIn("2026-11-20", got)
        self.assertEqual(len(got["2026-11-20"]), 1)  # 같은 반복이 두 건이어도 미리보기는 하나

    def test_task_without_due_date_has_no_previews(self):
        self.assertEqual(preview_tasks([self.task(due_date="")], date(2026, 11, 1), date(2026, 11, 30), TODAY), {})


    def test_preview_period_shifts_start(self):
        t = self.task(start_date="2026-11-02", due_date="2026-11-06")
        got = preview_tasks([t], date(2026, 11, 1), date(2026, 11, 30), TODAY)
        p = got["2026-11-13"][0]
        self.assertEqual((p.start_date, p.due_date), ("2026-11-09", "2026-11-13"))
        self.assertTrue(p.is_period)
        plain = preview_tasks([self.task()], date(2026, 11, 1), date(2026, 11, 30), TODAY)
        self.assertEqual(plain["2026-11-13"][0].start_date, "")


class TestLabelForRecurrence(unittest.TestCase):
    def test_default_label_becomes_routine(self):
        self.assertEqual(label_for_recurrence("MANUAL", RecurrenceType.WEEKLY), "ROUTINE")

    def test_chosen_label_kept(self):
        self.assertEqual(label_for_recurrence("URGENT", RecurrenceType.MONTHLY), "URGENT")

    def test_no_recurrence_keeps_label(self):
        self.assertEqual(label_for_recurrence("MANUAL", RecurrenceType.NONE), "MANUAL")


if __name__ == "__main__":
    unittest.main()
