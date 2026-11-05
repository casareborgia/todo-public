"""핵심 로직(규칙 파서, 사내포털/메신저 파서, DB 이월·반복·중복방지·Snooze) 자동 검증 테스트."""

from datetime import date, timedelta
import os
import tempfile
import unittest

from collectors.folder_watcher import FolderWatcherCollector
from core.database import DatabaseManager
from core.models import RecurrenceType, TaskSource, TaskStatus
from parsers.portal_parser import PortalAndMessengerParser
from parsers.rule_parser import RuleBasedExtractor


class TestInternalTodoCalendar(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_todo.db")
        self.db = DatabaseManager(self.db_path)
        self.extractor = RuleBasedExtractor()
        self.parser = PortalAndMessengerParser(self.extractor)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rule_parser_dates_and_metadata(self):
        ref = date(2026, 10, 28)  # 수요일
        sample = (
            "[배부] 2026년 정보보안 자체점검 결과 제출 요청\n"
            "문서번호: 정보보호-1234\n"
            "발신부서: 정보보호팀\n"
            "시행일: 2026-10-20\n"
            "처리기한: 2026-11-05 17:00 까지\n"
            "내용: 기한 내 제출 바랍니다."
        )
        items = self.extractor.extract_tasks(sample, ref_date=ref)
        self.assertEqual(len(items), 1)
        t = items[0]
        self.assertIn("정보보안 자체점검", t.title)
        self.assertEqual(t.due_date, "2026-11-05")
        self.assertEqual(t.due_time, "17:00")
        self.assertEqual(t.source, TaskSource.PORTAL)
        self.assertIn("정보보호-1234", t.source_ref)

    def test_relative_dates_and_urgent_messenger(self):
        ref = date(2026, 10, 28)  # 수요일
        msg = "[김철수 팀장] [긴급] 감사 대응 자료 내일 오후 3시까지 메신저로 보고 바랍니다."
        items = self.extractor.extract_tasks(msg, ref_date=ref)
        self.assertEqual(len(items), 1)
        t = items[0]
        self.assertEqual(t.due_date, "2026-10-29")
        self.assertEqual(t.due_time, "15:00")
        self.assertEqual(t.priority, 1)
        self.assertEqual(t.label, "URGENT")

    def test_overdue_rollover_and_recurring_generation(self):
        today = date.today()
        past_date = (today - timedelta(days=10)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        # 1. 과거 기한 미완료 업무 등록 -> 오늘 조회 시 자동 이월(Rollover) 포함 확인
        overdue_items = self.extractor.extract_tasks(
            f"[배부] 과거 누락된 보고서 제출\n처리기한: {past_date} 18:00 까지"
        )
        tid, created = self.db.add_task(overdue_items[0])
        self.assertTrue(created)
        self.assertIsNotNone(tid)

        today_tasks = self.db.query_tasks(target_date=today_str, include_rollover_on_today=True)
        self.assertTrue(any(x.id == tid and x.is_overdue() for x in today_tasks))

        # 2. 매주 반복 일정 완료 시 차주 일정 자동 생성 확인
        recur_items = self.extractor.extract_tasks(
            f"[업무] 매주 주간업무보고 작성 제출\n마감기한: {today_str} 11:00 까지"
        )
        self.assertEqual(recur_items[0].recurrence, RecurrenceType.WEEKLY)
        rt_id, _ = self.db.add_task(recur_items[0])
        next_id = self.db.set_task_status(rt_id, TaskStatus.COMPLETED)
        self.assertIsNotNone(next_id)
        next_task = self.db.get_task(next_id)
        expected_next = (today + timedelta(days=7)).strftime("%Y-%m-%d")
        self.assertEqual(next_task.due_date, expected_next)
        self.assertEqual(next_task.status, TaskStatus.PENDING)

    def test_folder_watcher_deduplication(self):
        inbox_dir = os.path.join(self.temp_dir.name, "inbox")
        os.makedirs(inbox_dir, exist_ok=True)
        sample_file = os.path.join(inbox_dir, "doc.txt")
        with open(sample_file, "w", encoding="utf-8") as f:
            f.write("[배부] 부서 보안점검표 제출\n처리기한: 2026-11-10 18:00까지\n---\n[협조] 연차사용계획서 회신\n기한: 2026-11-12까지")

        watcher = FolderWatcherCollector(self.db, self.parser, watch_folders=[inbox_dir])
        first_run = watcher.collect_and_register()
        self.assertEqual(len(first_run), 2)

        # 동일 파일 재스캔 시 중복 등록되지 않아야 함
        second_run = watcher.collect_and_register()
        self.assertEqual(len(second_run), 0)


if __name__ == "__main__":
    unittest.main()
