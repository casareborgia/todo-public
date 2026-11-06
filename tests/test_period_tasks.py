"""기간 업무(시작일) 열 테스트."""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta

from collectors.ezq_collector import EzqMessageCollector
from core.database import DatabaseManager, next_period
from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus
from core.recurrence import next_period as recurrence_next_period, next_task_message
from parsers.message_scorer import REGISTER, score_message
from parsers.portal_parser import PortalAndMessengerParser
from parsers.quick_entry import parse_quick_entry
from parsers.rule_parser import RuleBasedExtractor
from tests.test_rule_bugfixes import WEBHWP_BODY

REF = date(2026, 11, 6)


class PeriodTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_date_roundtrip(self):
        db = DatabaseManager(self.path)
        tid, _ = db.add_task(TaskItem(title="전시", start_date="2026-11-10", due_date="2026-11-14"))
        t = db.get_task(tid)
        self.assertEqual(t.start_date, "2026-11-10")
        self.assertTrue(t.is_period)
        t.start_date = "2026-11-11"
        db.update_task(t)
        self.assertEqual(db.get_task(tid).start_date, "2026-11-11")

    def test_old_db_gets_start_date_column(self):
        conn = sqlite3.connect(self.path)
        conn.execute(
            """CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                description TEXT DEFAULT '', source TEXT DEFAULT 'MANUAL',
                source_ref TEXT DEFAULT '', content_hash TEXT DEFAULT '',
                due_date TEXT DEFAULT '', due_time TEXT DEFAULT '18:00',
                status TEXT DEFAULT 'PENDING', label TEXT DEFAULT 'MANUAL',
                priority INTEGER DEFAULT 2, recurrence TEXT DEFAULT 'NONE',
                is_new_scan INTEGER DEFAULT 0, notified_at TEXT, snooze_until TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT)"""
        )
        conn.execute(
            "INSERT INTO tasks (title, due_date, created_at, updated_at) VALUES ('옛 업무', '2026-11-14', 'x', 'x')"
        )
        conn.commit()
        conn.close()
        db = DatabaseManager(self.path)
        t = db.get_task(1)
        self.assertEqual(t.start_date, "")
        self.assertFalse(t.is_period)

    def test_same_day_period_saved_as_deadline(self):
        db = DatabaseManager(self.path)
        tid, _ = db.add_task(TaskItem(title="하루", start_date="2026-11-14", due_date="2026-11-14"))
        t = db.get_task(tid)
        self.assertEqual(t.start_date, "")
        t.start_date = "2026-11-20"
        db.update_task(t)
        self.assertEqual(db.get_task(tid).start_date, "")

    def test_period_label(self):
        self.assertEqual(TaskItem(start_date="2026-11-10", due_date="2026-11-14").period_label(), "11/10~11/14")
        self.assertEqual(TaskItem(start_date="", due_date="2026-11-14").period_label(), "")
        self.assertEqual(TaskItem(start_date="2026-11-14", due_date="2026-11-14").period_label(), "")


PERIOD_CASES = [
    ("실적 제출 기간: 11/10 ~ 11/14 부탁드립니다", ("2026-11-10", "2026-11-14")),
    ("자료 제출 11.10.~11.14. 바랍니다", ("2026-11-10", "2026-11-14")),
    ("11월 10일 ~ 11월 14일 실적 입력 바랍니다", ("2026-11-10", "2026-11-14")),
    ("설문 응답 11/10~14 부탁드립니다", ("2026-11-10", "2026-11-14")),
    ("교육 이수 기간 2026.11.10 ~ 2026.11.20", ("2026-11-10", "2026-11-20")),
    ("점검 기간 11.10.-11.14. 제출 바랍니다", ("2026-11-10", "2026-11-14")),
    ("신청 기간: 11. 10.(월) ~ 11. 14.(금) 18:00", ("2026-11-10", "2026-11-14")),
    ("연말 정산 12/28~1/3 제출", ("2026-12-28", "2027-01-03")),
    ("11/28~3 접수 바랍니다", ("2026-11-28", "2026-12-03")),
]


def _iso(period):
    return None if period is None else (period[0].isoformat(), period[1].isoformat())


class PeriodReadingTests(unittest.TestCase):
    """기간(~) 표현 읽기: 끝 날짜는 기한, 시작일은 TaskItem.start_date (기준일 2026-11-06)."""

    def setUp(self):
        self.ex = RuleBasedExtractor()

    def test_extract_period_cases(self):
        for text, expected in PERIOD_CASES:
            with self.subTest(text=text):
                self.assertEqual(_iso(self.ex.extract_period(text, ref_date=REF)), expected)
                due, _time, estimated = self.ex.extract_due(text, ref_date=REF)
                self.assertEqual(due, expected[1])
                self.assertFalse(estimated)
                task = self.ex.extract_tasks(text, ref_date=REF, already_judged=True)[0]
                self.assertEqual((task.start_date, task.due_date), expected)

    def test_doc_number_hyphen_not_period(self):
        text = "감사실-123(2026.10.30.) 관련\n자료를 11/7까지 제출 바랍니다"
        self.assertIsNone(self.ex.extract_period(text, ref_date=REF))
        self.assertEqual(self.ex.extract_due(text, ref_date=REF)[0], "2026-11-07")
        self.assertEqual(self.ex.extract_tasks(text, ref_date=REF)[0].start_date, "")
        self.assertIsNone(self.ex.extract_period("감사실-123(2026.10.30.) 관련 자료 11/7까지 제출", ref_date=REF))

    def test_enforcement_line_not_period(self):
        self.assertIsNone(self.ex.extract_period(WEBHWP_BODY, ref_date=date(2026, 11, 3)))
        task = self.ex.extract_tasks(WEBHWP_BODY, source_hint=TaskSource.PORTAL, ref_date=date(2026, 11, 3))[0]
        self.assertEqual((task.start_date, task.due_date), ("", "2026-11-07"))
        # 시행 줄에 기간처럼 보이는 날짜가 있어도 후보에서 빠짐
        self.assertIsNone(
            self.ex.extract_period("자료 제출 바랍니다\n시행 감사실-456 (2026.11.03.~2026.11.05.)", ref_date=REF)
        )
        self.assertIsNotNone(
            self.ex.extract_period("자료 제출 바랍니다\n감사실-456 (2026.11.03.~2026.11.05.)", ref_date=REF)
        )

    def test_existing_due_unchanged(self):
        ref = date(2026, 10, 29)  # tests/test_rule_bugfixes.py 기준일
        cases = [
            ("오늘 오후 5시까지 각자 주간업무실적 시스템에 입력 바랍니다.", ("2026-10-29", "17:00")),
            ("[배부] 개인정보 보호 실태 점검 결과 회신 요청\n문서번호: 감사실-1204\n처리기한: 2026-11-06\n시행일: 2026-10-27",
             ("2026-11-06", "18:00")),
            ("연간계획서 다음 달 5일까지 제출 바랍니다.", ("2026-11-05", "18:00")),
            ("이번 주 내로 점검표 회신 부탁드립니다.", ("2026-10-30", "18:00")),
            ("[배부] 교육 이수 안내\n문서번호: 교육-1\n기한: 11/20까지", ("2026-11-20", "18:00")),
            ("자료 제출 (~ 11.04 15:00)", ("2026-11-04", "15:00")),
        ]
        for text, (due, time) in cases:
            with self.subTest(text=text):
                self.assertEqual(self.ex.extract_due(text, ref_date=ref)[:2], (due, time))
                task = self.ex.extract_tasks(text, ref_date=ref)[0]
                self.assertEqual((task.due_date, task.due_time, task.start_date), (due, time, ""))

    def test_scorer_counts_period_as_deadline(self):
        r = score_message("교육 이수 기간 2026.11.10 ~ 2026.11.20 이수 바랍니다")
        self.assertIn("명시 기한 +2", r.reasons)
        self.assertEqual(r.verdict, REGISTER)
        self.assertEqual(score_message("점검 기간 11.10.-11.14. 제출 바랍니다").verdict, REGISTER)
        self.assertEqual(score_message("설문 응답 11/10~14 부탁드립니다").verdict, REGISTER)

    def test_scorer_unchanged_without_period(self):
        # 기간 없는 문서번호·전화번호의 '-'는 기한이 아님 (tests/test_message_scorer.py 전체는 회귀 확인에서 따로 돌림)
        self.assertNotIn("명시 기한 +2", score_message("감사실-123 문서 검토 부탁드립니다").reasons)
        self.assertNotIn("명시 기한 +2", score_message("내선 02-123-4567로 연락 부탁드립니다").reasons)

    def test_ezq_period_message_registers_period(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        collector = EzqMessageCollector(
            db=DatabaseManager(os.path.join(tmp.name, "t.db")), parser=PortalAndMessengerParser(),
            reader=lambda _cls: [],
        )
        items = collector.parse_message("설문 응답 11/10~14 부탁드립니다", datetime(2026, 11, 6, 9, 0))
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].start_date, items[0].due_date), ("2026-11-10", "2026-11-14"))
        self.assertFalse(items[0].needs_review)

    def test_day_only_end_forms(self):
        # 끝 바로 뒤 '까지'와 단위 글자로 시작하는 낱말('회신', '명단')은 기간 그대로
        for text in ("11월 10일~14일 교육 이수", "자료 제출 11.10.~14. 바랍니다", "11/10~14까지 회신 바랍니다",
                     "11/10~14 회신 바랍니다", "11/10~14 명단 제출"):
            with self.subTest(text=text):
                self.assertEqual(_iso(self.ex.extract_period(text, ref_date=REF)), ("2026-11-10", "2026-11-14"))
                task = self.ex.extract_tasks(text, ref_date=REF, already_judged=True)[0]
                self.assertEqual((task.start_date, task.due_date, task.due_estimated), ("2026-11-10", "2026-11-14", False))

    def test_count_and_time_ranges_not_period(self):
        # 수량·시각·배수 범위: 기한·판정 모두 시작 전(2204dbb)과 같음 (기한 없음 → 3영업일 뒤 11-11 추정)
        cases = [
            ("11.10. 3~4명 참석 바랍니다", "확인 필요"),
            ("11.10. 1~2개 제출 바랍니다", "확인 필요"),
            ("11.10. 14~16시 교육 참석", "제외"),
            ("11/10~3쪽 검토", "확인 필요"),
            ("11/10~3번 문항", "제외"),
            ("1.5~2배 증가 보고", "확인 필요"),
            ("11.10. 3~4 명 참석 바랍니다", "확인 필요"),  # 띄어 쓴 단위
            ("11/10~3 쪽 검토", "확인 필요"),
        ]
        for text, verdict in cases:
            with self.subTest(text=text):
                self.assertIsNone(self.ex.extract_period(text, ref_date=REF))
                task = self.ex.extract_tasks(text, ref_date=REF, already_judged=True)[0]
                self.assertEqual((task.due_date, task.start_date, task.due_estimated), ("2026-11-11", "", True))
                self.assertEqual(score_message(text).verdict, verdict)

    def test_explicit_due_beats_day_only_period(self):
        for text in (
            "11/7까지 제출 바랍니다 (교육 11/10~14)",
            "자료 11월 7일까지 제출, 교육은 11/10~14",
            "11/7 마감, 설문 11/10~14 진행",
            "2026.11.7까지 제출 (교육 11/10~14)",
            "11/7(금) 18시까지 제출, 교육 11/10~14",
            "내일까지 제출, 교육 11/10~14",
        ):
            with self.subTest(text=text):
                task = self.ex.extract_tasks(text, ref_date=REF, already_judged=True)[0]
                self.assertEqual((task.due_date, task.start_date), ("2026-11-07", ""))

    def test_quick_entry_period(self):
        task = parse_quick_entry(self.ex, "11/10~11/14 교육 이수", "2026-11-06", ref_date=REF)
        self.assertEqual((task.start_date, task.due_date), ("2026-11-10", "2026-11-14"))
        self.assertEqual(task.title, "교육 이수")
        task = parse_quick_entry(self.ex, "11/10~14 설문 응답", "2026-11-06", ref_date=REF)
        self.assertEqual((task.start_date, task.due_date, task.title), ("2026-11-10", "2026-11-14", "설문 응답"))
        task = parse_quick_entry(self.ex, "내일까지 보고서 제출", "2026-11-06", ref_date=REF)
        self.assertEqual((task.start_date, task.due_date), ("", "2026-11-07"))


class RecurringPeriodTests(unittest.TestCase):
    """반복 기간 업무: 완료하거나 끝날이 지나면 같은 길이의 다음 기간을 만듦."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))
        self.today = date.today()

    def tearDown(self):
        self.tmp.cleanup()

    def _period(self, start, end, rec=RecurrenceType.WEEKLY, **kw):
        tid, _ = self.db.add_task(TaskItem(
            title="주간 실적 입력", start_date=start.isoformat(), due_date=end.isoformat(),
            recurrence=rec, label="ROUTINE", **kw,
        ))
        return tid

    def test_next_period_weekly(self):
        self.assertEqual(
            next_period(date(2026, 11, 10), date(2026, 11, 14), RecurrenceType.WEEKLY, date(2026, 11, 12)),
            (date(2026, 11, 17), date(2026, 11, 21)),
        )
        # recurrence 모듈에서도 같은 함수
        self.assertIs(recurrence_next_period, next_period)

    def test_next_period_skips_past(self):
        self.assertEqual(
            next_period(date(2026, 11, 10), date(2026, 11, 14), RecurrenceType.WEEKLY, date(2026, 11, 30)),
            (date(2026, 12, 1), date(2026, 12, 5)),
        )

    def test_next_period_monthly_end_of_month(self):
        self.assertEqual(
            next_period(date(2099, 1, 29), date(2099, 1, 31), RecurrenceType.MONTHLY, date(2099, 1, 31)),
            (date(2099, 2, 28), date(2099, 2, 28)),
        )
        tid = self._period(date(2099, 1, 29), date(2099, 1, 31), RecurrenceType.MONTHLY)
        feb_id = self.db.set_task_status(tid, TaskStatus.COMPLETED)
        feb = self.db.get_task(feb_id)
        self.assertEqual((feb.start_date, feb.due_date), ("", "2099-02-28"))
        mar = self.db.get_task(self.db.set_task_status(feb_id, TaskStatus.COMPLETED))
        self.assertEqual((mar.start_date, mar.due_date), ("", "2099-03-28"))

    def test_complete_recurring_period_creates_next(self):
        tid = self._period(date(2099, 11, 10), date(2099, 11, 14))
        nxt = self.db.get_task(self.db.set_task_status(tid, TaskStatus.COMPLETED))
        self.assertEqual((nxt.start_date, nxt.due_date), ("2099-11-17", "2099-11-21"))
        self.assertEqual((nxt.label, nxt.recurrence, nxt.status), ("ROUTINE", RecurrenceType.WEEKLY, TaskStatus.PENDING))

    def test_complete_plain_recurring_unchanged(self):
        tid, _ = self.db.add_task(TaskItem(
            title="주간업무보고", due_date=self.today.isoformat(), recurrence=RecurrenceType.WEEKLY,
        ))
        nxt = self.db.get_task(self.db.set_task_status(tid, TaskStatus.COMPLETED))
        self.assertEqual(nxt.due_date, (self.today + timedelta(days=7)).isoformat())
        self.assertEqual(nxt.start_date, "")

    def test_complete_nonrecurring_period_no_next(self):
        tid = self._period(date(2099, 11, 10), date(2099, 11, 14), RecurrenceType.NONE)
        self.assertIsNone(self.db.set_task_status(tid, TaskStatus.COMPLETED))
        self.assertEqual(len(self.db.query_tasks(needs_review=None)), 1)

    def test_activate_expired_periods(self):
        end = self.today - timedelta(days=1)
        tid = self._period(end - timedelta(days=4), end)
        ids = self.db.activate_expired_periods(self.today)
        self.assertEqual(len(ids), 1)
        nxt = self.db.get_task(ids[0])
        self.assertEqual(
            (nxt.start_date, nxt.due_date),
            ((end + timedelta(days=3)).isoformat(), (end + timedelta(days=7)).isoformat()),
        )
        self.assertNotEqual(self.db.get_task(tid).status, TaskStatus.COMPLETED)
        self.assertIn(tid, [t.id for t in self.db.query_tasks(status_filter="OVERDUE")])

    def test_complete_reports_whether_next_was_created(self):
        tid = self._period(date(2099, 11, 10), date(2099, 11, 14))
        self.db.set_task_status(tid, TaskStatus.COMPLETED)
        self.assertTrue(self.db.next_task_was_created())
        # 만료 때 이미 만든 다음 기간 → 완료해도 새로 만든 것이 아님
        end = self.today - timedelta(days=1)
        old = self._period(end - timedelta(days=4), end, description="만료")
        activated = self.db.activate_expired_periods(self.today)
        nxt_id = self.db.set_task_status(old, TaskStatus.COMPLETED)
        self.assertIn(nxt_id, activated)
        self.assertFalse(self.db.next_task_was_created())

    def test_next_task_message_wording(self):
        nxt = TaskItem(title="t", start_date="2099-11-17", due_date="2099-11-21")
        self.assertEqual(
            next_task_message(nxt, False),
            "다음 기간 업무가 이미 등록되어 있습니다.\n• 다음 기간: 2099-11-17 ~ 2099-11-21",
        )
        self.assertEqual(
            next_task_message(nxt, True),
            "정기/반복 업무가 완료되어 다음 주기 일정이 자동 등록되었습니다.\n• 다음 기간: 2099-11-17 ~ 2099-11-21",
        )
        self.assertEqual(
            next_task_message(TaskItem(title="t", due_date="2099-11-21"), True),
            "정기/반복 업무가 완료되어 다음 주기 일정이 자동 등록되었습니다.\n• 다음 마감일: 2099-11-21",
        )

    def test_activate_not_before_end(self):
        self._period(self.today - timedelta(days=4), self.today)
        self.assertEqual(self.db.activate_expired_periods(self.today), [])

    def test_complete_after_expiry_no_duplicate(self):
        end = self.today - timedelta(days=1)
        tid = self._period(end - timedelta(days=4), end)
        ids = self.db.activate_expired_periods(self.today)
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.db.set_task_status(tid, TaskStatus.COMPLETED), ids[0])
        nexts = [t for t in self.db.query_tasks(needs_review=None) if t.id != tid]
        self.assertEqual(len(nexts), 1)

    def test_deleted_next_period_not_recreated(self):
        end = self.today - timedelta(days=1)
        tid = self._period(end - timedelta(days=4), end)
        ids = self.db.activate_expired_periods(self.today)
        self.db.delete_task(ids[0])
        self.assertEqual(self.db.activate_expired_periods(self.today), [])
        # 늦게 완료해도 지운 다음 기간을 다시 만들지 않음
        self.assertIsNone(self.db.set_task_status(tid, TaskStatus.COMPLETED))

    def test_complete_returns_existing_next_period(self):
        # 만료 때 만든 다음 기간이 있으면 늦은 완료는 그 업무 id를 돌려줌 (UI가 '다음 없음'으로 보지 않게)
        end = self.today - timedelta(days=1)
        tid = self._period(end - timedelta(days=4), end)
        (nid,) = self.db.activate_expired_periods(self.today)
        self.assertEqual(self.db.set_task_status(tid, TaskStatus.COMPLETED), nid)
        self.assertEqual(self.db.get_task(nid).status, TaskStatus.PENDING)

    def test_deleted_next_period_not_recreated_via_chain(self):
        # T0 만료 → T1 생성, T1 완료 → T2, T2 삭제 → 나중에 T0(여전히 미완료)로 활성화해도 T2 기간이 다시 안 생김
        d = self.today
        t0 = self._period(d - timedelta(days=8), d - timedelta(days=4))
        (t1,) = self.db.activate_expired_periods(d - timedelta(days=3))
        self.assertEqual(self.db.get_task(t1).due_date, (d + timedelta(days=3)).isoformat())
        t2 = self.db.set_task_status(t1, TaskStatus.COMPLETED)
        self.assertEqual(
            (self.db.get_task(t2).start_date, self.db.get_task(t2).due_date),
            ((d + timedelta(days=6)).isoformat(), (d + timedelta(days=10)).isoformat()),
        )
        self.db.delete_task(t2)
        self.assertEqual(self.db.activate_expired_periods(d + timedelta(days=7)), [])
        self.assertNotEqual(self.db.get_task(t0).status, TaskStatus.COMPLETED)

    def test_review_task_not_activated(self):
        end = self.today - timedelta(days=1)
        self._period(end - timedelta(days=4), end, needs_review=True)
        self.assertEqual(self.db.activate_expired_periods(self.today), [])


class CopyStartDateTests(unittest.TestCase):
    """공문 기한으로 바꾸는 기존 경로가 시작일도 함께 옮겨 시작 > 끝이 안 생기게."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_upsert_copies_start_date(self):
        est = TaskItem(title="가상 점검 제출", due_date="2026-11-30", due_estimated=True,
                       external_key="DOC:감사실-1", source=TaskSource.PORTAL)
        self.db.add_task(est)
        doc = TaskItem(title="가상 점검 제출", start_date="2026-11-10", due_date="2026-11-14",
                       external_key="DOC:감사실-1", source=TaskSource.PORTAL)
        tid, status = self.db.upsert_scanned_task(doc)
        self.assertEqual(status, "updated")
        t = self.db.get_task(tid)
        self.assertEqual((t.start_date, t.due_date), ("2026-11-10", "2026-11-14"))

    def test_rescan_without_period_keeps_user_start(self):
        base = dict(title="가상 점검 제출", due_date="2026-11-30", external_key="DOC:감사실-3", source=TaskSource.PORTAL)
        tid, _ = self.db.upsert_scanned_task(TaskItem(**base))
        t = self.db.get_task(tid)
        t.start_date = "2026-11-20"
        t.notified_at = "2026-11-01 09:00:00"
        t.is_new_scan = False
        self.db.update_task(t)
        before = self.db.get_task(tid)
        _, status = self.db.upsert_scanned_task(TaskItem(**base))
        self.assertEqual(status, "unchanged")
        after = self.db.get_task(tid)
        self.assertEqual(after.start_date, "2026-11-20")
        self.assertEqual(after.notified_at, before.notified_at)
        self.assertEqual(after.is_new_scan, before.is_new_scan)

    def test_rescan_with_new_period_same_due_updates_start(self):
        base = dict(title="가상 점검 제출", due_date="2026-11-30", external_key="DOC:감사실-4", source=TaskSource.PORTAL)
        tid, _ = self.db.upsert_scanned_task(TaskItem(**base))
        _, status = self.db.upsert_scanned_task(TaskItem(start_date="2026-11-25", **base))
        self.assertEqual(status, "updated")
        self.assertEqual(self.db.get_task(tid).start_date, "2026-11-25")

    def test_fold_copies_start_date(self):
        keep = TaskItem(title="가상 점검 제출", due_date="2026-11-30", due_estimated=True,
                        external_key="LIST:가상", source=TaskSource.MESSENGER)
        keep.id = self.db.add_task(keep)[0]
        drop = TaskItem(title="가상 점검 제출 부탁", due_date="2026-11-14", external_key="EZQ:x|0",
                        source=TaskSource.MESSENGER)
        drop.id = self.db.add_task(drop)[0]
        official = TaskItem(title="가상 점검 제출", start_date="2026-11-10", due_date="2026-11-14",
                            external_key="DOC:감사실-2", source=TaskSource.PORTAL)
        official.id = drop.id
        self.assertTrue(self.db.fold_task_into(keep, drop, official))
        t = self.db.get_task(keep.id)
        self.assertEqual((t.start_date, t.due_date), ("2026-11-10", "2026-11-14"))

    def test_estimated_reconfirm_clears_start(self):
        tid, _ = self.db.add_task(TaskItem(
            title="가상 점검 제출", start_date="2026-11-10", due_date="2026-11-14",
            due_estimated=True, needs_review=True, external_key="LIST:DOC:가상-1"))
        t = self.db.confirm_review_task(tid, new_due=("2026-11-20", "18:00"))
        self.assertEqual((t.start_date, t.due_date), ("", "2026-11-20"))
        self.assertEqual(self.db.get_task(tid).start_date, "")


from collectors.workmgmt_window import TEXT
from tests.test_workmgmt_collector import BODY, TITLE, DistributedListBase, lrow


class DocPeriodOnListTaskTests(DistributedListBase):
    def test_doc_confirms_period_on_list_task(self):
        body = BODY.replace("3. 회신기한/방법 : '26.11.7(금)/처실별 공문제출", "3. 제출기간: 11.10.~11.14.")
        self.assertIn("11.10.~11.14.", body)
        c = self.collector_with_rows([lrow(no="감사실-456", title="2026년 내부통제 자가진단 결과 제출 시행 알림",
                                           received="2026-11-03 09:00")])
        c.collect_and_register()
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, body))
        c.collect_and_register()
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual((tasks[0].start_date, tasks[0].due_date), ("2026-11-10", "2026-11-14"))
        self.assertFalse(tasks[0].due_estimated)


class PeriodUiLogicTests(unittest.TestCase):
    """화면에 쓰는 기간 계산: 달력 칸, 날짜 목록 조회, 끌어 옮기기."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_span_dates_inside(self):
        from core.recurrence import span_dates
        t = TaskItem(title="전시", start_date="2026-11-10", due_date="2026-11-14")
        got = span_dates(t, date(2026, 11, 1), date(2026, 12, 12))
        self.assertEqual(got, [date(2026, 11, d) for d in range(10, 15)])
        plain = TaskItem(title="보고", due_date="2026-11-12")
        self.assertEqual(span_dates(plain, date(2026, 11, 1), date(2026, 12, 12)), [date(2026, 11, 12)])
        self.assertEqual(span_dates(plain, date(2026, 12, 1), date(2026, 12, 12)), [])

    def test_span_dates_cross_month(self):
        from core.recurrence import span_dates
        t = TaskItem(title="점검", start_date="2026-10-28", due_date="2026-11-03")
        got = span_dates(t, date(2026, 10, 26), date(2026, 12, 6))
        self.assertEqual(len(got), 7)
        self.assertEqual((got[0], got[-1]), (date(2026, 10, 28), date(2026, 11, 3)))
        # 보이는 범위 밖은 잘라 냄
        self.assertEqual(span_dates(t, date(2026, 11, 1), date(2026, 12, 12))[0], date(2026, 11, 1))

    def test_query_day_includes_period_span(self):
        tid, _ = self.db.add_task(TaskItem(title="전시", start_date="2026-11-10", due_date="2026-11-14"))
        self.db.add_task(TaskItem(title="다른 날", due_date="2026-11-13"))
        got = self.db.query_tasks(target_date="2026-11-12", include_period_span=True)
        self.assertEqual([t.id for t in got], [tid])
        self.assertEqual(self.db.query_tasks(target_date="2026-11-12"), [])
        self.assertEqual(self.db.query_tasks(target_date="2026-11-12", include_period_span=False), [])

    def test_shift_period_keeps_length(self):
        tid, _ = self.db.add_task(TaskItem(title="전시", start_date="2026-11-10", due_date="2026-11-14"))
        t = self.db.reschedule_task(tid, "2026-11-21")
        self.assertEqual((t.start_date, t.due_date), ("2026-11-17", "2026-11-21"))
        pid, _ = self.db.add_task(TaskItem(title="보고", due_date="2026-11-12"))
        self.assertEqual(self.db.reschedule_task(pid, "2026-11-19").start_date, "")
        # 가운데 칸(11/19)에서 끌어 11/26에 놓으면 끝도 7일 뒤
        from core.recurrence import drag_due_date
        moved = self.db.get_task(tid)
        self.assertEqual(drag_due_date(moved, "2026-11-19", "2026-11-26"), "2026-11-28")
        self.assertEqual(drag_due_date(self.db.get_task(pid), "2026-11-19", "2026-11-26"), "2026-11-26")


if __name__ == "__main__":
    unittest.main()
