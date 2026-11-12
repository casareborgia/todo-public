"""2026-11-10 버그 점검의 나머지(중간·낮음) 재현 테스트."""
import os
import tempfile
import unittest
from datetime import date, timedelta

from core.database import DatabaseManager, next_occurrence
from core.models import RecurrenceType, TaskItem, TaskStatus
from core.recurrence import occurrence_dates


class RecurrenceTest(unittest.TestCase):
    """매월 반복이 28일로 굳지 않고, 늦게 완료해도 요일·날짜 주기를 지킨다."""

    def test_monthly_returns_to_31(self):
        d = date(2027, 1, 31)
        chain = []
        for _ in range(4):
            nxt = next_occurrence(d, RecurrenceType.MONTHLY, date(2027, 1, 1), recur_day=31)
            chain.append(nxt)
            d = nxt
        self.assertEqual(chain, [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)])

    def test_edited_due_date_wins_over_old_anchor(self):
        # 31일 반복이었지만 사용자가 15일로 바꿨으면 15일 주기
        self.assertEqual(
            next_occurrence(date(2027, 1, 15), RecurrenceType.MONTHLY, date(2027, 1, 1), recur_day=31),
            date(2027, 2, 15),
        )

    def test_late_completion_keeps_weekday(self):
        sunday = date(2026, 11, 8)
        self.assertEqual(next_occurrence(sunday, RecurrenceType.WEEKLY, date(2026, 11, 10)), date(2026, 11, 15))
        self.assertEqual(next_occurrence(sunday, RecurrenceType.WEEKLY, date(2026, 11, 8)), date(2026, 11, 15))
        self.assertEqual(next_occurrence(date(2026, 11, 5), RecurrenceType.DAILY, date(2026, 11, 10)), date(2026, 11, 10))
        self.assertEqual(
            next_occurrence(date(2026, 9, 30), RecurrenceType.MONTHLY, date(2026, 11, 10)), date(2026, 11, 30)
        )

    def test_preview_matches_created(self):
        dates = occurrence_dates(date(2027, 2, 28), RecurrenceType.MONTHLY, date(2027, 3, 1), date(2027, 5, 31),
                                 date(2027, 1, 1), recur_day=31)
        self.assertEqual(dates, [date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)])

    def test_db_chain_keeps_31(self):
        db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))
        year = date.today().year + 1
        tid, _ = db.add_task(TaskItem(title="월말 정산", due_date=f"{year}-01-31",
                                      recurrence=RecurrenceType.MONTHLY, content_hash="m"))
        dues = []
        for _ in range(3):
            tid = db.set_task_status(tid, TaskStatus.COMPLETED)
            dues.append(db.get_task(tid).due_date)
        self.assertEqual(dues, [f"{year}-02-28" if year % 4 else f"{year}-02-29", f"{year}-03-31", f"{year}-04-30"])

    def test_toggle_completion_does_not_duplicate(self):
        db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))
        past = (date.today() - timedelta(days=3)).isoformat()
        tid, _ = db.add_task(TaskItem(title="주간 보고", due_date=past,
                                      recurrence=RecurrenceType.WEEKLY, content_hash="w"))
        first = db.set_task_status(tid, TaskStatus.COMPLETED)
        db.set_task_status(tid, TaskStatus.PENDING)
        second = db.set_task_status(tid, TaskStatus.COMPLETED)
        self.assertIsNotNone(first)
        self.assertIsNone(second)  # 같은 다음 회차가 이미 있어 새로 만들지 않음
        self.assertEqual(len([t for t in db.query_tasks() if t.title == "주간 보고"]), 2)


class ParserDateTest(unittest.TestCase):
    def setUp(self):
        from parsers.rule_parser import RuleBasedExtractor
        self.p = RuleBasedExtractor({})

    def due(self, text, today):
        return self.p._parse_date_from_chunk(text, today)

    def test_january_message_about_december_is_last_year(self):
        jan2 = date(2027, 1, 2)
        self.assertEqual(self.due("12월 31일까지 제출", jan2), date(2026, 12, 31))
        self.assertEqual(self.due("12/28까지", jan2), date(2026, 12, 28))
        # 반대 방향은 그대로: 12월에 받은 '1/5까지' → 다음 해
        self.assertEqual(self.due("1/5까지 제출", date(2026, 12, 20)), date(2027, 1, 5))
        # 가까운 미래는 그대로
        self.assertEqual(self.due("3/2까지 제출", jan2), date(2027, 3, 2))

    def test_reversed_range_is_not_a_period(self):
        today = date(2026, 11, 10)
        self.assertIsNone(self.p.extract_period("11/20~11/10 교육", today))
        self.assertEqual(self.p.extract_period("12/28~1/3 교육", today), (date(2026, 12, 28), date(2027, 1, 3)))
        self.assertIsNone(self.p.extract_period("2026.11.20~11.10 교육", today))
        self.assertEqual(self.p.extract_period("2026.12.28~1.3 교육", today), (date(2026, 12, 28), date(2027, 1, 3)))

    def test_this_week_past_weekday_stays_this_week(self):
        tue = date(2026, 11, 10)
        self.assertEqual(self.due("이번주 월요일까지 제출", tue), date(2026, 11, 9))
        self.assertEqual(self.due("이번주 금요일까지 제출", tue), date(2026, 11, 13))

    def test_quick_entry_duration_is_not_time(self):
        from parsers.quick_entry import parse_quick_entry
        r = parse_quick_entry(self.p, "보고서 1시간 검토", "2026-11-10", ref_date=date(2026, 11, 10))
        self.assertEqual(r.due_time, "18:00")
        self.assertEqual(r.title, "보고서 1시간 검토")
        r = parse_quick_entry(self.p, "내일 3시 회의자료 제출", "2026-11-10", ref_date=date(2026, 11, 10))
        self.assertEqual((r.due_date, r.due_time), ("2026-11-11", "15:00"))


class HostileFontTest(unittest.TestCase):
    """내장 폰트 cmap 형식 12에 0~0x10FFFF 그룹을 여러 개 넣어도 바로 끝나야 한다."""

    def test_huge_format12_groups(self):
        import struct
        import time
        from parsers.pdf_text import _truetype_gid_to_unicode
        groups = b"".join(struct.pack(">III", 0, 0x10FFFF, 1) for _ in range(20))
        sub = struct.pack(">HHIII", 12, 0, 16 + len(groups), 0, 20) + groups
        cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 10, 12) + sub
        font = struct.pack(">IHHHH", 0x10000, 1, 0, 0, 0) + struct.pack(">4sIII", b"cmap", 0, 28, len(cmap)) + cmap
        t0 = time.perf_counter()
        table = _truetype_gid_to_unicode(font)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertLessEqual(len(table), 0x10000)

    def test_truncated_group_count(self):
        import struct
        from parsers.pdf_text import _truetype_gid_to_unicode
        sub = struct.pack(">HHIII", 12, 0, 16, 0, 1_000_000)  # 그룹 수만 크고 데이터 없음
        cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 10, 12) + sub
        font = struct.pack(">IHHHH", 0x10000, 1, 0, 0, 0) + struct.pack(">4sIII", b"cmap", 0, 28, len(cmap)) + cmap
        self.assertEqual(_truetype_gid_to_unicode(font), {})


class CollectorRetryTest(unittest.TestCase):
    """처리 중 일시 오류(DB 잠김 등)가 나도 쪽지·문서가 영영 빠지지 않고 다음 확인 때 등록된다."""

    def setUp(self):
        self.db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _fail_once(self, name):
        real = getattr(self.db, name)
        state = {"failed": False}

        def flaky(*a, **kw):
            if not state["failed"]:
                state["failed"] = True
                raise RuntimeError("database is locked")
            return real(*a, **kw)
        setattr(self.db, name, flaky)

    def test_ezq_message_retried_after_error(self):
        import contextlib
        import io
        from datetime import datetime
        from collectors.ezq_collector import EzqMessageCollector
        from parsers.portal_parser import PortalAndMessengerParser
        body = "내일까지 보고서 제출 부탁드립니다\n"
        times = iter([datetime(2026, 11, 10, 9, 0), datetime(2026, 11, 10, 9, 5)])
        c = EzqMessageCollector(self.db, PortalAndMessengerParser(), reader=lambda _cls: [(1, body)],
                                clock=lambda: next(times))
        self._fail_once("upsert_scanned_task")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(c.collect_and_register(), [])
        created = c.collect_and_register()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].due_date, "2026-11-11")  # 처음 발견한 9:00 기준
        self.assertEqual(c.collect_and_register(), [])  # 한 번만

    def test_workmgmt_document_retried_after_error(self):
        import contextlib
        import io
        from collectors.workmgmt_collector import WorkMgmtDocCollector
        from parsers.portal_parser import PortalAndMessengerParser
        from tests.test_workmgmt_collector import BODY, NOW, TITLE, TEXT, Clock, FakeDocWindows
        windows = FakeDocWindows()
        windows.set(1, TITLE, (TEXT, BODY))
        c = WorkMgmtDocCollector(self.db, PortalAndMessengerParser(), reader=windows,
                                 list_reader=lambda _cls: [], clock=Clock(NOW))
        self._fail_once("upsert_scanned_task")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(c.collect_and_register(), [])
        self.assertEqual(len(c.collect_and_register()), 1)


class TaskDialogInputTest(unittest.TestCase):
    def test_normalize_dates(self):
        from ui.task_dialog import normalize_date_input
        self.assertEqual(normalize_date_input("2026-11-5"), "2026-11-05")
        self.assertEqual(normalize_date_input("2026.11.5."), "2026-11-05")
        self.assertEqual(normalize_date_input(" 2026/11/20 "), "2026-11-20")
        for bad in ["", "11-20", "2026-02-30", "내일", "2026-11-20 15:00"]:
            self.assertIsNone(normalize_date_input(bad), bad)

    def test_normalize_times(self):
        from ui.task_dialog import normalize_time_input
        self.assertEqual(normalize_time_input(""), "18:00")
        self.assertEqual(normalize_time_input("9:00"), "09:00")
        for bad in ["25:00", "9시", "12:60"]:
            self.assertIsNone(normalize_time_input(bad), bad)

    def test_unpadded_start_keeps_period(self):
        from ui.task_dialog import normalize_date_input
        db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))
        tid, _ = db.add_task(TaskItem(title="교육", start_date=normalize_date_input("2026-11-5"),
                                      due_date=normalize_date_input("2026-11-10"), content_hash="p"))
        self.assertTrue(db.get_task(tid).is_period)

    def test_edit_resets_alerts_and_keeps_fresh_fields(self):
        from ui.task_dialog import apply_edit
        db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))
        tid, _ = db.add_task(TaskItem(title="보고", due_date="2026-11-20", due_time="18:00", content_hash="e",
                                      snooze_until="2026-11-21 09:00:00", notified_at="2026-11-20 10:00:00"))
        before = db.get_task(tid)  # 편집 창을 열 때의 값
        db.mark_notified(tid)       # 창이 열린 동안 알림이 떴음
        values = dict(title="보고", start_date="", due_date="2026-11-20", due_time="15:00", label="MANUAL",
                      priority=2, recurrence=RecurrenceType.NONE, source_ref="", description="")
        saved = apply_edit(db.get_task(tid), before, values)
        self.assertIsNone(saved.snooze_until)
        self.assertIsNone(saved.notified_at)
        # 기한을 안 바꾸면 그 사이 바뀐 알림 기록을 덮어쓰지 않음
        values["due_time"] = "18:00"
        fresh = db.get_task(tid)
        kept = apply_edit(db.get_task(tid), before, values)
        self.assertEqual(kept.notified_at, fresh.notified_at)


class MonthToShowTest(unittest.TestCase):
    def test_month_follows_moved_date(self):
        from ui.app_window import month_to_show
        self.assertEqual(month_to_show(2026, 11, "2026-11-30"), (2026, 11))
        self.assertEqual(month_to_show(2026, 11, "2026-12-05"), (2026, 11))  # 11월 달력의 마지막 줄에 보임
        self.assertEqual(month_to_show(2026, 11, "2026-12-14"), (2026, 12))
        self.assertEqual(month_to_show(2026, 12, "2027-01-20"), (2027, 1))


if __name__ == "__main__":
    unittest.main()
