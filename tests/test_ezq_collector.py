"""EzQ 받은쪽지 창 자동 수집 테스트 (가상 창 내용, 실제 메신저 없이)."""

from datetime import date, datetime
import os
import tempfile
import unittest

from collectors.ezq_collector import EzqMessageCollector, message_key
from core.database import DatabaseManager
from core.models import TaskSource
from parsers.portal_parser import PortalAndMessengerParser

# 내부망 PC 점검에서 실제로 읽힌 테스트 쪽지 본문 (2026-10-30)
REAL_TEST_BODY = "내일까지 보고서 제출할것(테스트)"

# 실제 쪽지 창 형식: 받은 본문 아래에 '-----' 줄로 구분된 이전 대화 인용 (실사용 표본 2026-11-03, 이름은 가상)
SEP = "-" * 53
END = "-" * 79


def quoted(sender, to, when, text):
    return f"{SEP}\n발신자 :{sender}\n수신자 :{to}\n날 짜 :{when}\n\n\n{text}\n"


THANKS_REPLY = (
    "좋은하루 보내세요\n\n\n"
    + quoted("이영희", "홍길동", "2026-11-03 11:07:08",
             "다음주(11월 2째주) 수의시담\n\n다다음주(11월3째주) 계약체결\n\n이렇게 진행될 예정입니다.\n\n감사합니다!")
    + END
)
CHAT_REPLY = (
    "와 이거 누가 해주는 답변이에요? 기가 막히네요~~!!!\n\n\n"
    + quoted("이영희", "김철수", "2026-11-03 10:24:53", "어! 그건 가능할듯 합니다.\n\n감사합니다!")
    + quoted("김철수", "이영희", "2026-11-03 10:22:00",
             "대리님, 인력양성비로 참석자들 출장비 추산해주는 건 가능하겠지요?")
    + END
)
OK_REPLY = (
    "네 알겠습니다 대리님\n\n견적서나 용역설계서가 계약별로 꼭 필요한 내용이 있진 않나 보네요~?\n\n\n"
    + quoted("이영희", "김철수", "2026-11-03 09:49:27", "뭐 딱히 문제될건 없어 보입니다만.\n\n이렇게 진행하시죠?")
    + quoted("김철수", "이영희", "2026-11-03 08:19:48",
             "용역설계서와 견적서 관련 문의드립니다.\n검토 부탁드립니다.\n"
             "계약기간은 우선 '27.9월까지로 하되, 통합연장시 12월까지로 하는게 가능할까요?")
    + END
)
WEEKLY_REQUEST = (
    "안녕하세요. 박민수입니다!\n\n주간업무 요청드립니다. (~ 11.04 15:00)\n"
    "○대상기간 : (실적) '26.11.2 ~ '26.11.6 / (계획) '26.11.9 ~ '26.11.13\n\n감사합니다^^\n\n"
    "첨부파일:20261109_계약자재부 주간업무_양식.hwp\n\n \n" + END
)
FORWARD_REQUEST = (
    "아래 내용 확인 부탁드립니다.\n\n\n"
    + quoted("박민수", "이영희", "2026-11-02 16:00:00", "11월 5일까지 명단 회신 바랍니다.")
    + END
)


class FakeWindows:
    def __init__(self):
        self.open = {}  # 창 번호 → 본문

    def __call__(self, window_class):
        return list(self.open.items())


class TestEzqCollector(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "t.db")
        self.db = DatabaseManager(self.db_path)
        self.windows = FakeWindows()
        self.now = datetime(2026, 10, 30, 9, 30)
        self.collector = self.make_collector(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def make_collector(self, db):
        return EzqMessageCollector(
            db=db, parser=PortalAndMessengerParser(), reader=self.windows, clock=lambda: self.now
        )

    def test_registers_task_with_due_from_first_seen_time(self):
        self.windows.open[101] = REAL_TEST_BODY
        created = self.collector.collect_and_register()
        self.assertEqual(len(created), 1)
        task = created[0]
        self.assertEqual(task.source, TaskSource.MESSENGER)
        self.assertEqual(task.due_date, date(2026, 10, 31).isoformat())
        self.assertFalse(task.due_estimated)
        self.assertIn("EzQ 쪽지", task.source_ref)
        self.assertIn("2026-10-30 09:30", task.description)
        self.assertEqual(task.external_key, f"EZQ:{message_key(REAL_TEST_BODY)}|0")

    def test_same_window_checked_repeatedly_registers_once(self):
        self.windows.open[101] = REAL_TEST_BODY
        self.collector.collect_and_register()
        self.windows.open[102] = "내일까지  보고서 제출할것(테스트)\n"  # 띄어쓰기만 다른 같은 쪽지
        for _ in range(3):
            self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(len(self.db.query_tasks()), 1)

    def test_restart_next_day_does_not_shift_due(self):
        self.windows.open[101] = REAL_TEST_BODY
        self.collector.collect_and_register()
        self.now = datetime(2026, 10, 31, 8, 50)  # 다음 날 프로그램을 다시 켬, 쪽지 창은 열린 그대로
        restarted = self.make_collector(DatabaseManager(self.db_path))
        self.assertEqual(restarted.collect_and_register(), [])
        tasks = self.db.query_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_date, "2026-10-31")

    def test_reply_is_not_registered(self):
        self.windows.open[101] = "네 알겠습니다."
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(self.db.query_tasks(), [])

    def test_quoted_history_does_not_make_reply_a_task(self):
        # 인용된 이전 대화의 '수신자 :'·'날 짜 :' 줄 때문에 잡담·답장이 오늘 마감 업무로 등록되던 문제
        for hwnd, body in enumerate((THANKS_REPLY, CHAT_REPLY, OK_REPLY), start=201):
            self.windows.open[hwnd] = body
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(self.db.query_tasks(), [])

    def test_request_with_quote_end_line_still_registered(self):
        self.windows.open[101] = WEEKLY_REQUEST
        created = self.collector.collect_and_register()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].due_date, "2026-11-04")
        self.assertEqual(created[0].due_time, "15:00")
        self.assertIn("주간업무", created[0].title)

    def test_forward_takes_due_from_quoted_message(self):
        self.windows.open[101] = FORWARD_REQUEST
        created = self.collector.collect_and_register()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].due_date, "2026-11-05")
        self.assertFalse(created[0].due_estimated)
        self.assertIn("발신자 :박민수", created[0].description)  # 설명에는 인용까지 그대로 보존

    def restart(self):
        self.collector = self.make_collector(DatabaseManager(self.db_path))
        return self.collector

    def test_deleted_task_is_not_registered_again_after_restart(self):
        # 지운 업무가 쪽지 창이 열린 채로 프로그램을 다시 켜면 또 등록되던 문제
        self.windows.open[101] = REAL_TEST_BODY
        task = self.collector.collect_and_register()[0]
        self.db.delete_task(task.id)
        self.assertEqual(self.restart().collect_and_register(), [])
        self.assertEqual(self.db.query_tasks(), [])

    def test_every_judged_message_is_logged(self):
        self.windows.open[101] = REAL_TEST_BODY
        self.windows.open[102] = "[ 펌뱅킹지급 알림 ]\n\n보낸사람 : 회계부\n\n대금이 지급되었습니다."
        self.collector.collect_and_register()
        excluded = self.db.list_ezq_messages(verdict="제외")
        self.assertEqual(len(excluded), 1)
        self.assertIn("시스템 알림", excluded[0]["reasons"][0])
        self.assertEqual(self.db.get_ezq_message(message_key(REAL_TEST_BODY))["verdict"], "등록")

    def test_verdict_is_not_mixed_into_description(self):
        self.windows.open[101] = REAL_TEST_BODY
        task = self.collector.collect_and_register()[0]
        self.assertNotIn("[판정:", task.description)
        self.assertTrue(task.description.endswith(REAL_TEST_BODY))

    def test_register_excluded_message_as_task(self):
        body = "다음 주 회의 참석자 명단이 어떻게 되나요?"  # 부탁 표현 없는 질문: 판정은 제외
        self.windows.open[101] = body
        self.assertEqual(self.collector.collect_and_register(), [])
        self.now = datetime(2026, 10, 31, 9, 0)  # 다음 날 사용자가 '제외된 쪽지'에서 고름
        created = self.collector.register_as_task(message_key(body))
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].title.startswith("[확인 필요]"))
        self.assertEqual(created[0].due_date, "2026-11-04")  # 기본 기한은 처음 받은 날(10-30) 기준 3영업일
        self.assertEqual(self.db.get_ezq_message(message_key(body))["user_label"], "업무")

    def test_dismiss_and_undo(self):
        from collectors.ezq_collector import ezq_key_of

        self.windows.open[101] = REAL_TEST_BODY
        task = self.collector.collect_and_register()[0]
        key = self.collector.dismiss_task(task)
        self.assertEqual(key, ezq_key_of(task))
        self.assertEqual(self.db.query_tasks(), [])
        self.assertEqual(self.db.get_ezq_message(key)["user_label"], "업무아님")
        self.assertEqual(self.restart().collect_and_register(), [])  # 다시 켜도 등록 안 됨

        new_id = self.collector.undo_dismiss(task, key)
        restored = self.db.get_task(new_id)
        self.assertEqual(restored.title, task.title)
        self.assertEqual(restored.due_date, task.due_date)
        self.assertEqual(self.db.get_ezq_message(key)["user_label"], "")

    def test_dismiss_task_registered_before_log_existed(self):
        # 판정 기록 도입 전에 등록된 업무(설명에 예전 '[판정: …]' 줄)도 '업무 아님' 가능
        from collectors.ezq_collector import ezq_key_of
        from core.models import TaskItem

        old = TaskItem(
            title="보고서 제출", source=TaskSource.MESSENGER, external_key="DOC:감사실-1204",
            description=f"[EzQ 쪽지 2026-10-30 09:30 확인]\n[판정: 등록 9점 — …]\n{REAL_TEST_BODY}",
        )
        old.id, _ = self.db.add_task(old)
        self.assertEqual(ezq_key_of(old), message_key(REAL_TEST_BODY))
        key = self.collector.dismiss_task(old)
        self.assertEqual(self.db.get_ezq_message(key)["body"], REAL_TEST_BODY)
        self.windows.open[101] = REAL_TEST_BODY
        self.assertEqual(self.restart().collect_and_register(), [])

    def test_non_ezq_task_cannot_be_dismissed(self):
        from core.models import TaskItem

        manual = TaskItem(title="개인 일정", description="메모")
        manual.id, _ = self.db.add_task(manual)
        self.assertIsNone(self.collector.dismiss_task(manual))
        self.assertIsNotNone(self.db.get_task(manual.id))

    LIGHT_REQUEST = "우선 낙찰자랑 낙찰율 등 관련 내용 좀 공유해주세요~"  # 기한·첨부 없는 가벼운 요청 → 확인 필요

    def test_review_task_is_kept_out_of_calendar_briefing_and_reminders(self):
        self.windows.open[101] = self.LIGHT_REQUEST
        created = self.collector.collect_and_register()
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].needs_review)
        self.assertFalse(created[0].title.startswith("[확인 필요]"))
        self.assertEqual(self.db.query_tasks(), [])
        self.assertEqual(len(self.db.query_tasks(needs_review=True)), 1)
        self.assertEqual(self.db.count_review_tasks(), 1)
        summary = self.db.get_briefing_summary(ref_date=date(2026, 11, 30))
        self.assertFalse(any(summary.values()))
        self.assertEqual(self.db.get_due_tasks_for_reminder(datetime(2026, 11, 30, 17, 0)), [])

    def test_confirm_review_moves_task_to_calendar(self):
        self.windows.open[101] = self.LIGHT_REQUEST
        task = self.collector.collect_and_register()[0]
        confirmed = self.collector.confirm_review(task)
        self.assertFalse(confirmed.needs_review)
        self.assertTrue(confirmed.is_new_scan)
        self.assertEqual([t.id for t in self.db.query_tasks()], [task.id])
        self.assertEqual(self.db.count_review_tasks(), 0)
        self.assertEqual(self.db.get_ezq_message(message_key(self.LIGHT_REQUEST))["user_label"], "업무")

    def test_dismiss_review_task(self):
        self.windows.open[101] = self.LIGHT_REQUEST
        task = self.collector.collect_and_register()[0]
        self.collector.dismiss_task(task)
        self.assertEqual(self.db.count_review_tasks(), 0)
        self.assertEqual(self.db.get_ezq_message(message_key(self.LIGHT_REQUEST))["user_label"], "업무아님")

    def test_review_task_confirmed_by_official_document(self):
        # 확인 필요 쪽지와 같은 문서번호의 공문 PDF가 들어오면 업무로 확정
        from core.models import TaskItem

        doc = "감사실-1204"
        # 문서번호가 적힌 쪽지는 DOC: 키로 등록됨
        review = TaskItem(title="자료 확인", source=TaskSource.MESSENGER, needs_review=True, external_key=f"DOC:{doc}")
        review.id, _ = self.db.add_task(review)
        pdf = TaskItem(title="자료 확인", source=TaskSource.PORTAL, external_key=f"DOC:{doc}", due_date="2026-11-10")
        _id, action = self.db.upsert_scanned_task(pdf)
        self.assertEqual(action, "updated")
        self.assertFalse(self.db.get_task(review.id).needs_review)

    def test_old_review_title_prefix_is_migrated(self):
        # 예전 버전은 '[확인 필요] ' 제목으로만 표시함 → 새 열로 옮기고 제목 앞 표시는 뗌
        from core.models import TaskItem

        old = TaskItem(title="[확인 필요] 회의 참석 부탁드립니다", source=TaskSource.MESSENGER,
                       external_key="EZQ:old|0", description="[EzQ 쪽지 2026-10-30 09:30 확인]\n회의 참석 부탁드립니다")
        old.id, _ = self.db.add_task(old)
        conn = self.db._get_conn()
        conn.execute("ALTER TABLE tasks DROP COLUMN needs_review")
        conn.commit()
        conn.close()
        migrated = DatabaseManager(self.db_path).get_task(old.id)
        self.assertTrue(migrated.needs_review)
        self.assertEqual(migrated.title, "회의 참석 부탁드립니다")

    # ── 코드 리뷰(2026-11-03) 지적 사항 ──

    def test_plainly_deleted_task_can_be_registered_again(self):
        self.windows.open[101] = REAL_TEST_BODY
        task = self.collector.collect_and_register()[0]
        key = message_key(REAL_TEST_BODY)
        self.assertTrue(self.collector.has_task(key))
        self.db.delete_task(task.id)  # 실수로 일반 삭제
        self.assertFalse(self.collector.has_task(key))
        created = self.collector.register_as_task(key)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].due_date, task.due_date)  # 처음 받은 시각 기준 기한 유지

    def test_failed_register_does_not_mark_as_task(self):
        body = quoted("이영희", "홍길동", "2026-10-30 09:00:00", "11/5까지 제출 바랍니다.") + END  # 맨 위 글 없음
        self.windows.open[101] = body
        self.collector.collect_and_register()
        key = message_key(body)
        self.assertEqual(self.collector.register_as_task(key), [])
        self.assertEqual(self.db.get_ezq_message(key)["user_label"], "")

    def test_confirm_late_review_recomputes_estimated_due(self):
        self.windows.open[101] = self.LIGHT_REQUEST  # 10-30 받음, 추정 기한
        task = self.collector.collect_and_register()[0]
        self.assertTrue(task.due_estimated)
        self.now = datetime(2026, 11, 10, 9, 0)  # 열흘 뒤 '업무 맞음'
        confirmed = self.collector.confirm_review(task)
        expected, _t, _e = self.collector.parser.extractor.extract_due("", ref_date=date(2026, 11, 10))
        self.assertEqual(confirmed.due_date, expected)
        self.assertEqual(self.db.get_due_tasks_for_reminder(datetime(2026, 11, 10, 9, 5)), [])

    def test_confirm_review_keeps_explicit_due(self):
        body = "11/20까지 자료 좀 부탁해"  # 기한이 적힌 쪽지 (등록됨) → 확인 필요로 바꿔 놓고 '업무 맞음'해도 기한 유지
        self.windows.open[101] = body
        task = self.collector.collect_and_register()[0]
        task.needs_review = True
        self.db.update_task(task)
        self.now = datetime(2026, 11, 10, 9, 0)
        self.assertEqual(self.collector.confirm_review(task).due_date, "2026-11-20")

    def test_old_prefix_migration_only_touches_ezq_tasks(self):
        from core.models import TaskItem

        manual = TaskItem(title="[확인 필요] 계약서 검토", description="직접 쓴 메모")
        manual.id, _ = self.db.add_task(manual)
        conn = self.db._get_conn()
        conn.execute("ALTER TABLE tasks DROP COLUMN needs_review")
        conn.commit()
        conn.close()
        kept = DatabaseManager(self.db_path).get_task(manual.id)
        self.assertFalse(kept.needs_review)
        self.assertEqual(kept.title, "[확인 필요] 계약서 검토")

    def test_legacy_dismiss_keeps_original_receipt_time(self):
        from core.models import TaskItem

        old = TaskItem(
            title="보고서 제출", source=TaskSource.MESSENGER, external_key="EZQ:legacy|0",
            description=f"[EzQ 쪽지 2026-10-28 14:05 확인]\n{REAL_TEST_BODY}",
        )
        old.id, _ = self.db.add_task(old)
        key = self.collector.dismiss_task(old)
        self.assertEqual(self.db.get_ezq_message(key)["first_seen"], "2026-10-28 14:05:00")

    def test_ezq_key_survives_description_edit(self):
        from collectors.ezq_collector import ezq_key_of

        body = "문서번호: 감사실-1204\n내일까지 자료 제출 바랍니다."  # 문서번호가 있어 DOC: 키로 등록됨
        self.windows.open[101] = body
        task = self.collector.collect_and_register()[0]
        self.assertTrue(task.external_key.startswith("DOC:"))
        task.description = "사용자가 고친 메모"
        self.db.update_task(task)
        self.assertEqual(ezq_key_of(self.db.get_task(task.id)), message_key(body))

    def test_disabled_does_not_read_windows(self):
        self.collector.enabled = False
        self.collector.reader = lambda _c: self.fail("꺼져 있으면 창을 읽지 않아야 함")
        self.assertEqual(self.collector.collect_and_register(), [])

    def test_reader_error_is_ignored(self):
        def broken(_c):
            raise OSError("창 읽기 실패")

        self.collector.reader = broken
        self.assertEqual(self.collector.collect_and_register(), [])


class TestMergedMessageHasTask(unittest.TestCase):
    """공문 업무에 합쳐진 쪽지도 '업무 있음' (판정 기록 창에서 '업무로 등록'으로 같은 일을 두 번 만들지 않게)."""

    BODY = "감사실 내부통제 자가진단 공문 왔습니다. 11/7(금)까지 처실별로 제출해야 하니 작성 부탁드립니다."

    def setUp(self):
        from core.models import TaskItem

        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))
        self.portal_id = self.db.add_task(
            TaskItem(title="2026년 내부통제 자가진단 결과 제출 시행 알림", source=TaskSource.PORTAL,
                     due_date="2026-11-07", external_key="DOC:감사실-456")
        )[0]
        body = self.BODY

        class OneWindow:
            def __call__(self, _cls):
                return [(1, body)]

        self.ezq = EzqMessageCollector(self.db, PortalAndMessengerParser(), reader=OneWindow(),
                                       clock=lambda: datetime(2026, 11, 3, 17, 50))
        self.assertEqual(self.ezq.collect_and_register(), [])  # 공문 업무에 합쳐짐
        self.key = message_key(body)

    def tearDown(self):
        self.tmp.cleanup()

    def test_merged_message_has_task(self):
        self.assertTrue(self.ezq.has_task(self.key))
        self.assertEqual(self.db.find_task_by_ezq_key(self.key).id, self.portal_id)

    def test_merged_task_deleted_then_has_task_false(self):
        self.db.delete_task(self.portal_id)
        self.assertFalse(self.ezq.has_task(self.key))


if __name__ == "__main__":
    unittest.main()
