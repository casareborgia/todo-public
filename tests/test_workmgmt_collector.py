"""업무관리시스템 문서 수집 테스트 (가상 문서 창, 실제 업무관리시스템 없이).

본문은 내부망 점검 4차(2026-11-03)에서 웹한글 기안기 GetTextFile로 받은 본문의 줄 모양을 가상 내용으로 재구성.
"""

from datetime import date, datetime, timedelta
import os
import tempfile
import unittest

from collectors.ezq_collector import EzqMessageCollector
from collectors.workmgmt_collector import WORKMGMT_LIST_SOURCE_REF, WorkMgmtDocCollector, doc_key
from collectors.workmgmt_window import ERR, NOCTRL, NOJS, REQ, TEXT, WAIT, DocWindowResult, ListWindowResult, parse_js_result
from core.database import DatabaseManager, shares_topic, topic_words
from core.models import TaskItem, TaskSource, TaskStatus
from parsers.portal_parser import PortalAndMessengerParser
from parsers.workmgmt_list import ListRow

BODY = """가상 회사 머리말
수신자
수신자 참조
(경유)
제 목
2026년 내부통제 자가진단 결과 제출 시행 알림
문서번호 감사실-123(2026.10.30.) 「내부통제 자가진단 2026년 하반기 계획」관련하여 다음과 같이 시행하오니 결과를 제출하여 주시기 바랍니다
1. 진단대상 : 부서별 내부통제 업무
2. 진단방법 : 자가진단표 작성
3. 회신기한/방법 : '26.11.7(금)/처실별 공문제출
4. 참고사항
가. 결과는 내부감사에 반영 예정('26.12월)
붙임 1. 자가진단표 양식 1부. 끝.
수신처 부서별처실,감사실
결재
시행 감사실-456 (2026.11.03.)
접수 기획실-789 (2026.11.03.)
"""
TITLE = "감사실-456호 2026년 내부통제 자가진단 결과 제출 시행 알림 - 페이지 1 - Microsoft Edge"
NOW = datetime(2026, 11, 3, 17, 50)


class FakeDocWindows:
    """창 번호 → [이번 확인에 돌려줄 결과들] (확인할 때마다 하나씩 꺼냄, 마지막 것은 계속)."""

    def __init__(self):
        self.windows = {}
        self.skipped = []

    def set(self, hwnd, title, *steps):
        self.windows[hwnd] = (title, list(steps))

    def __call__(self, window_class, skip=None):
        out = []
        for hwnd, (title, steps) in self.windows.items():
            if skip and skip(hwnd, title):
                self.skipped.append(hwnd)
                continue
            status, text = steps.pop(0) if len(steps) > 1 else steps[0]
            out.append(DocWindowResult(hwnd, title, status, text))
        return out


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class WorkMgmtTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))
        self.parser = PortalAndMessengerParser()
        self.windows = FakeDocWindows()
        self.clock = Clock(NOW)
        self.collector = WorkMgmtDocCollector(
            self.db, self.parser, reader=self.windows, list_reader=lambda _cls: [], clock=self.clock
        )

    def tearDown(self):
        self.tmp.cleanup()


class TestWorkMgmtCollector(WorkMgmtTestBase):
    def test_registers_document_with_doc_number_and_reply_deadline(self):
        self.windows.set(1, TITLE, (TEXT, BODY))
        created = self.collector.collect_and_register()
        self.assertEqual(len(created), 1)
        t = created[0]
        self.assertEqual(t.external_key, "DOC:감사실-456")
        self.assertEqual(t.due_date, "2026-11-07")
        self.assertFalse(t.due_estimated)
        self.assertEqual(t.source, TaskSource.PORTAL)
        self.assertEqual(t.title, "2026년 내부통제 자가진단 결과 제출 시행 알림")
        self.assertTrue(t.description.startswith("[업무관리시스템 문서 2026-11-03 17:50 확인]"))
        self.assertIn("업무관리시스템 문서", t.source_ref)

    def test_callback_result_arrives_on_next_check(self):
        self.windows.set(1, TITLE, (REQ, ""), (WAIT, ""), (TEXT, BODY))
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(len(self.collector.collect_and_register()), 1)

    def test_text_is_requested_only_once_per_window(self):
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.collector.collect_and_register()
        self.collector.collect_and_register()
        self.assertEqual(self.windows.skipped, [1])  # 본문을 받은 창은 다시 꺼내지 않음

    def test_same_document_in_two_windows_registers_once(self):
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.windows.set(2, TITLE, (TEXT, BODY))
        self.assertEqual(len(self.collector.collect_and_register()), 1)

    def test_deleted_task_is_not_registered_again(self):
        self.windows.set(1, TITLE, (TEXT, BODY))
        task = self.collector.collect_and_register()[0]
        self.db.delete_task(task.id)
        # 프로그램을 다시 켜고 같은 문서 창을 다시 열어도 다시 등록하지 않음
        again = WorkMgmtDocCollector(self.db, self.parser, reader=self.windows, list_reader=lambda _cls: [], clock=self.clock)
        self.windows.set(3, TITLE, (TEXT, BODY))
        self.assertEqual(again.collect_and_register(), [])
        self.assertIsNotNone(self.db.get_workmgmt_doc(doc_key(BODY)))

    def test_same_document_from_pdf_is_same_task(self):
        pdf_item = self.parser.extractor.extract_tasks(BODY, source_hint=TaskSource.PORTAL, source_ref="PDF: 공문.pdf")[0]
        self.db.upsert_scanned_task(pdf_item)
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.assertEqual(self.collector.collect_and_register(), [])  # 같은 시행번호 → 기존 업무

    def test_document_without_enforcement_number_uses_body_key(self):
        body = "제 목\n자료 제출 요청\n1. 자료를 '26.11.10(화)까지 제출하여 주시기 바랍니다. 끝."
        self.windows.set(1, "문서", (TEXT, body))
        t = self.collector.collect_and_register()[0]
        self.assertTrue(t.external_key.startswith("WEBHWP:"))
        self.assertEqual(t.due_date, "2026-11-10")

    def test_short_text_waits_for_editor(self):
        self.windows.set(1, TITLE, (TEXT, " "), (TEXT, BODY))
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(len(self.collector.collect_and_register()), 1)

    def test_list_window_without_editor_is_given_up_after_a_minute(self):
        self.windows.set(9, "문서함 목록", (NOCTRL, ""))
        self.collector.collect_and_register()
        self.clock.now = NOW + timedelta(seconds=61)
        self.collector.collect_and_register()
        self.collector.collect_and_register()
        self.assertEqual(self.windows.skipped, [9])

    def test_disabled_collector_reads_nothing(self):
        self.collector.enabled = False
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(self.windows.skipped, [])

    def test_reader_error_is_ignored(self):
        def broken(*_a, **_k):
            raise OSError("COM 오류")
        self.collector.reader = broken
        self.assertEqual(self.collector.collect_and_register(), [])


class TestMergeWithMessenger(WorkMgmtTestBase):
    """같은 공문을 쪽지와 문서 창으로 모두 받았을 때 업무가 두 건이 되지 않게."""

    def add_messenger_task(self, title, body, due="2026-11-07"):
        t = TaskItem(title=title, description=body, source=TaskSource.MESSENGER, due_date=due, external_key=f"EZQ:{title}|0")
        task_id, _ = self.db.add_task(t)
        return task_id

    def test_document_merges_into_earlier_message_task(self):
        tid = self.add_messenger_task(
            "감사실 내부통제 자가진단 공문 왔습니다",
            "감사실 내부통제 자가진단 공문 왔습니다. 11/7(금)까지 처실별로 제출해야 하니 작성 부탁드립니다.",
        )
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual([t.id for t in self.collector.merged], [tid])
        merged = self.db.find_task_by_keys(["EZQ:감사실 내부통제 자가진단 공문 왔습니다|0"])
        self.assertIn("업무관리시스템 문서 문서번호 감사실-456", merged.source_ref)
        self.assertEqual(self.db.get_workmgmt_doc(doc_key(BODY))["result"], "쪽지 업무에 합침")

    def test_message_on_other_day_is_not_merged(self):
        self.add_messenger_task("감사실 내부통제 자가진단 공문 왔습니다", "11/10까지 제출", due="2026-11-10")
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.assertEqual(len(self.collector.collect_and_register()), 1)

    def test_unrelated_message_same_day_is_not_merged(self):
        self.add_messenger_task("주간업무 실적 제출", "11/7까지 주간업무 실적 제출 부탁드립니다.")
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.assertEqual(len(self.collector.collect_and_register()), 1)

    def test_message_after_document_merges_into_document_task(self):
        self.windows.set(1, TITLE, (TEXT, BODY))
        doc_task = self.collector.collect_and_register()[0]

        class OneWindow:
            def __call__(self, _cls):
                return [(1, "감사실 내부통제 자가진단 공문 왔습니다. 11/7(금)까지 처실별로 제출해야 하니 작성 부탁드립니다.")]

        ezq = EzqMessageCollector(self.db, self.parser, reader=OneWindow(), clock=lambda: NOW)
        self.assertEqual(ezq.collect_and_register(), [])
        self.assertIn("EzQ 쪽지 (같은 일)", self.db.find_task_by_keys([doc_task.external_key]).source_ref)


def lrow(kind="수신", role="업무담당자", no="계약부-77", title="가상 안전점검 계획 협조", received="2026-11-04 09:12"):
    return ListRow(title, no, kind, role, "가상안전처", received)


class FakeListWindows:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0

    def __call__(self, window_class):
        self.calls += 1
        return [ListWindowResult(9, "결재시행 - Microsoft Edge", list(self.rows))]


class DistributedListBase(WorkMgmtTestBase):
    def collector_with_rows(self, rows):
        self.list_windows = FakeListWindows(rows)
        self.collector = WorkMgmtDocCollector(
            self.db, self.parser, reader=self.windows, list_reader=self.list_windows, clock=self.clock
        )
        return self.collector

    def open_tasks(self):
        return self.db.query_tasks(status_filter="INCOMPLETE", needs_review=None)


class TestDistributedList(DistributedListBase):
    """결재시행 목록의 배부받은 문서 → 업무 (기한은 문서를 열 때 정함)."""

    def test_distributed_row_creates_estimated_task(self):
        created = self.collector_with_rows([lrow()]).collect_and_register()
        self.assertEqual(len(created), 1)
        t = self.db.find_task_by_keys(["DOC:계약부-77"])
        self.assertEqual(t.title, "가상 안전점검 계획 협조")
        self.assertTrue(t.due_estimated)
        self.assertFalse(t.needs_review)
        self.assertEqual(t.source, TaskSource.PORTAL)
        self.assertIn(WORKMGMT_LIST_SOURCE_REF, t.source_ref)
        self.assertTrue(t.description.startswith("[업무관리시스템 배부 2026-11-03 17:50 확인]"))
        self.assertIn("처리구분: 업무담당자", t.description)
        self.assertIn("보낸 부서: 가상안전처", t.description)

    def test_estimated_due_counts_from_received_date(self):
        self.collector_with_rows([lrow()]).collect_and_register()
        expected, _t, _e = self.parser.extractor.extract_due("", ref_date=date(2026, 11, 4))
        self.assertEqual(self.db.find_task_by_keys(["DOC:계약부-77"]).due_date, expected)

    def test_manager_row_needs_review(self):
        self.collector_with_rows([lrow(role="업무관리자")]).collect_and_register()
        self.assertTrue(self.db.find_task_by_keys(["DOC:계약부-77"]).needs_review)

    def test_non_distributed_rows_ignored(self):
        self.assertEqual(self.collector_with_rows([lrow(kind="생산", role="기안")]).collect_and_register(), [])
        self.assertEqual(self.open_tasks(), [])

    def test_same_row_next_tick_no_duplicate(self):
        c = self.collector_with_rows([lrow()])
        c.collect_and_register()
        self.assertEqual(c.collect_and_register(), [])
        self.assertEqual(len(self.open_tasks()), 1)

    def test_deleted_task_not_recreated_on_reappear(self):
        c = self.collector_with_rows([lrow()])
        c.collect_and_register()
        self.db.delete_task(self.db.find_task_by_keys(["DOC:계약부-77"]).id)
        c2 = self.collector_with_rows([lrow()])
        self.assertEqual(c2.collect_and_register(), [])
        self.assertEqual(self.db.get_workmgmt_doc("LIST:DOC:계약부-77")["result"], "등록")

    def test_row_without_doc_no_uses_title_key(self):
        self.collector_with_rows([lrow(no="")]).collect_and_register()
        self.assertEqual(len(self.open_tasks()), 1)
        self.assertTrue(self.open_tasks()[0].external_key.startswith("LISTTITLE:"))

    def test_disabled_reads_nothing(self):
        c = self.collector_with_rows([lrow()])
        c.enabled = False
        self.assertEqual(c.collect_and_register(), [])
        self.assertEqual(self.list_windows.calls, 0)

    def test_list_reader_error_is_ignored(self):
        def broken(*_a, **_k):
            raise OSError("화면 읽기 실패")

        c = WorkMgmtDocCollector(self.db, self.parser, reader=self.windows, list_reader=broken, clock=self.clock)
        self.assertEqual(c.collect_and_register(), [])


FULL_TITLE = "2026년 내부통제 자가진단 결과 제출 시행 알림"


class TestListThenBody(DistributedListBase):
    """배부 목록으로 먼저 만든 업무를 문서 본문이 같은 업무로 이어 기한을 확정."""

    def list_then_body(self, list_no, title=FULL_TITLE, role="업무담당자"):
        c = self.collector_with_rows([lrow(no=list_no, title=title, role=role, received="2026-11-03 09:00")])
        c.collect_and_register()
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, BODY))
        c.collect_and_register()

    def test_body_confirms_due_same_doc_no(self):
        self.list_then_body(list_no="감사실-456")
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_date, "2026-11-07")
        self.assertFalse(tasks[0].due_estimated)

    def test_body_confirms_due_when_list_shows_receipt_no(self):
        self.list_then_body(list_no="계약부-901")  # 접수번호, 본문 시행번호(감사실-456)와 다름
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].due_date, "2026-11-07")

    def test_truncated_list_title_uses_full_title_attr(self):
        # 목록 읽기(LIST_JS)가 title 속성으로 전체 제목을 받았다는 전제
        self.list_then_body(list_no="계약부-901", title=FULL_TITLE)
        self.assertEqual(len(self.open_tasks()), 1)

    def test_review_task_confirmed_by_body(self):
        self.list_then_body(list_no="감사실-456", role="업무관리자")
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertFalse(tasks[0].needs_review)


class TestListLinksMessenger(DistributedListBase):
    """배부 목록 업무가 먼저 온 같은 일 쪽지 업무와 겹치지 않게 (C1), 쪽지가 확인 필요를 풀지 않게 (C6)."""

    def add_messenger_task(self, title, due="2026-11-07", created=None):
        t = TaskItem(title=title, description=title, source=TaskSource.MESSENGER, due_date=due,
                     external_key=f"EZQ:{doc_key(title)}|0")
        task_id, _ = self.db.add_task(t)
        if created:
            conn = self.db._get_conn()
            conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (created, task_id))
            conn.commit()
            conn.close()
        return task_id

    def messenger_item(self):
        return TaskItem(title="자가진단 결과 확인 부탁", source=TaskSource.MESSENGER, source_ref="EzQ 쪽지",
                        due_date="2026-11-07", external_key="EZQ:x|0")

    def test_list_row_links_to_earlier_messenger_task(self):
        msg_id = self.add_messenger_task("내부통제 자가진단 결과 11/7까지 제출 부탁드립니다")
        created = self.collector_with_rows([lrow(title=FULL_TITLE)]).collect_and_register()
        self.assertEqual(created, [])
        self.assertEqual(len(self.open_tasks()), 1)
        self.assertIn("업무관리시스템 배부 문서번호 계약부-77", self.db.get_task(msg_id).source_ref)
        rec = self.db.get_workmgmt_doc("LIST:DOC:계약부-77")
        self.assertEqual((rec["result"], rec["task_id"]), ("쪽지 업무에 합침", msg_id))

    def test_new_list_task_records_task_id(self):
        created = self.collector_with_rows([lrow()]).collect_and_register()
        self.assertEqual(self.db.get_workmgmt_doc("LIST:DOC:계약부-77")["task_id"], created[0].id)

    def test_single_shared_word_does_not_link(self):
        self.add_messenger_task("자가진단 표 양식 공유 부탁드립니다")
        self.collector_with_rows([lrow(title=FULL_TITLE)]).collect_and_register()
        self.assertEqual(len(self.open_tasks()), 2)

    def test_old_messenger_task_not_linked(self):
        self.add_messenger_task("내부통제 자가진단 결과 제출 부탁드립니다", created="2026-10-20 09:00:00")
        self.collector_with_rows([lrow(title=FULL_TITLE)]).collect_and_register()
        self.assertEqual(len(self.open_tasks()), 2)

    def test_messenger_merge_does_not_clear_manager_review(self):
        self.collector_with_rows([lrow(role="업무관리자")]).collect_and_register()
        t = self.db.find_task_by_keys(["DOC:계약부-77"])
        self.db.merge_into(t, self.messenger_item(), "EzQ 쪽지 (같은 일)", clear_review=False)
        self.assertTrue(self.db.find_task_by_keys(["DOC:계약부-77"]).needs_review)

    def test_ezq_merge_into_review_portal_task_keeps_review(self):
        t = TaskItem(title=FULL_TITLE, source=TaskSource.PORTAL, due_date="2026-11-07", external_key="DOC:감사실-456",
                     needs_review=True)
        self.db.add_task(t)

        class OneWindow:
            def __call__(self, _cls):
                return [(1, "감사실 내부통제 자가진단 공문 왔습니다. 11/7(금)까지 처실별로 제출해야 하니 작성 부탁드립니다.")]

        EzqMessageCollector(self.db, self.parser, reader=OneWindow(), clock=lambda: NOW).collect_and_register()
        self.assertTrue(self.db.find_task_by_keys(["DOC:감사실-456"]).needs_review)


class TestBodyFoldsDuplicates(TestListThenBody):
    """문서 본문이 기한을 정한 뒤 같은 일 쪽지 업무와 하나로 (C2), 배부 목록 기록을 먼저 봄."""

    def add_messenger_task(self, title, body, due="2026-11-07"):
        t = TaskItem(title=title, description=body, source=TaskSource.MESSENGER, due_date=due,
                     external_key=f"EZQ:{doc_key(body)}|0", ezq_key=doc_key(body))
        return self.db.add_task(t)[0]

    def test_body_folds_duplicate_messenger_task_after_due_confirmed(self):
        # 쪽지 제목 주제어가 1개라 목록 단계에서는 연결 안 됨 → 본문으로 기한이 정해진 뒤 같은 날·주제어로 하나가 됨
        msg_id = self.add_messenger_task("자가진단 결과 제출 부탁", "감사실 내부통제 자가진단 공문 왔습니다. 11/7까지 제출 부탁")
        self.list_then_body(list_no="계약부-901")
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        kept = tasks[0]
        self.assertEqual(kept.id, msg_id)  # 먼저 있던 업무를 남김
        self.assertEqual(kept.due_date, "2026-11-07")
        self.assertFalse(kept.due_estimated)
        self.assertTrue(kept.external_key.startswith("DOC:"))
        self.assertEqual(kept.ezq_key, doc_key("감사실 내부통제 자가진단 공문 왔습니다. 11/7까지 제출 부탁"))
        self.assertIn(WORKMGMT_LIST_SOURCE_REF, kept.source_ref)
        self.assertEqual(self.db.get_workmgmt_doc("LIST:DOC:계약부-901")["task_id"], msg_id)

    def test_body_uses_list_link_when_numbers_differ(self):
        self.list_then_body(list_no="계약부-901")  # 접수번호, 본문은 감사실-456
        t = self.open_tasks()[0]
        self.assertEqual(t.external_key, "DOC:계약부-901")
        self.assertEqual(t.due_date, "2026-11-07")
        self.assertIn("업무관리시스템 문서", t.source_ref)

    def test_body_uses_list_link_even_if_title_changed(self):
        # 목록 제목과 본문 제목이 많이 달라도 같은 문서번호 기록이면 이어짐
        self.list_then_body(list_no="감사실-456", title="가상 다른 제목")
        self.assertEqual(len(self.open_tasks()), 1)

    def test_body_after_list_merged_into_messenger_confirms_messenger_task(self):
        msg_id = self.add_messenger_task("내부통제 자가진단 결과 제출 부탁드립니다", "내부통제 자가진단 결과 제출 부탁드립니다",
                                         due="2026-11-10")
        self.list_then_body(list_no="계약부-901")
        tasks = self.open_tasks()
        self.assertEqual([t.id for t in tasks], [msg_id])
        self.assertEqual(tasks[0].due_date, "2026-11-07")  # 공문 회신기한으로


class TestDismissListTask(DistributedListBase):
    """확인 필요 정리 창의 '업무 아님'이 배부 건에도 먹고 (C3), 지운 배부 건은 문서를 열어도 다시 안 생김 (C4)."""

    def manager_task(self, no="감사실-456"):
        self.collector_with_rows([lrow(no=no, role="업무관리자", title=FULL_TITLE)]).collect_and_register()
        return self.db.find_task_by_keys([f"DOC:{no}"])

    def test_is_list_task(self):
        t = self.manager_task()
        self.assertTrue(self.collector.is_list_task(t))
        self.assertFalse(self.collector.is_list_task(TaskItem(title="x", source=TaskSource.MESSENGER, source_ref="EzQ 쪽지")))

    def test_review_reason(self):
        self.assertEqual(self.collector.review_reason(self.manager_task()), "업무관리자로 지정된 배부 문서 (처리구분: 업무관리자)")

    def test_dismiss_list_task_then_open_body_not_recreated(self):
        t = self.manager_task()
        self.assertEqual(self.collector.dismiss_task(t), "LIST:DOC:감사실-456")
        self.assertEqual(self.open_tasks(), [])
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(self.open_tasks(), [])
        self.assertEqual(self.db.get_workmgmt_doc(doc_key(BODY))["result"], "업무 아님(배부 목록)")

    def test_dismissed_receipt_no_list_task_blocks_body_by_title(self):
        t = self.manager_task(no="계약부-901")   # 목록은 접수번호, 본문은 감사실-456
        self.collector.dismiss_task(t)
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, BODY))
        self.collector.collect_and_register()
        self.assertEqual(self.open_tasks(), [])

    def test_dismiss_list_task_not_recreated_from_list(self):
        t = self.manager_task()
        self.collector.dismiss_task(t)
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual(self.open_tasks(), [])

    def test_undo_dismiss_list_task(self):
        t = self.manager_task()
        key = self.collector.dismiss_task(t)
        self.assertIsNotNone(self.collector.undo_dismiss(t, key))
        self.assertEqual(len(self.open_tasks()), 1)
        rec = self.db.get_workmgmt_doc(key)
        self.assertEqual(rec["result"], "확인 필요")
        self.assertEqual(rec["task_id"], self.open_tasks()[0].id)

    def test_dismiss_non_list_task_returns_none(self):
        t = TaskItem(title="x", source=TaskSource.MANUAL)
        t.id = self.db.add_task(t)[0]
        self.assertIsNone(self.collector.dismiss_task(t))
        self.assertEqual(len(self.open_tasks()), 1)


class TestReviewFixes(DistributedListBase):
    """최종 리뷰 지적 (2026-11-04)."""

    def body_first(self):
        c = self.collector_with_rows([])
        self.windows.set(1, TITLE, (TEXT, BODY))
        return c.collect_and_register()[0]

    def add_messenger(self, title, desc, needs_review=False):
        t = TaskItem(title=title, description=desc, source=TaskSource.MESSENGER, due_date="2026-11-07",
                     external_key=f"EZQ:{doc_key(desc)}|0", ezq_key=doc_key(desc), needs_review=needs_review)
        return self.db.add_task(t)[0]

    def test_body_first_then_list_receipt_no_is_one_task(self):  # 지적 1
        body_task = self.body_first()
        self.list_windows.rows = [lrow(no="계약부-901", title=FULL_TITLE)]
        self.assertEqual(self.collector.collect_and_register(), [])
        self.assertEqual([t.id for t in self.open_tasks()], [body_task.id])
        self.assertEqual(self.db.get_workmgmt_doc("LIST:DOC:계약부-901")["task_id"], body_task.id)

    def test_pdf_after_list_link_is_same_task(self):  # 지적 2
        c = self.collector_with_rows([lrow(no="계약부-901", title=FULL_TITLE)])
        c.collect_and_register()
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, BODY))
        c.collect_and_register()
        pdf = TaskItem(title=FULL_TITLE, source=TaskSource.PORTAL, due_date="2026-11-07",
                       external_key="DOC:감사실-456", match_keys=["DOC:감사실-456"])
        _tid, action = self.db.upsert_scanned_task(pdf)
        self.assertNotEqual(action, "created")
        self.assertEqual(len(self.open_tasks()), 1)

    def test_second_distribution_not_merged_into_already_linked_message(self):  # 지적 3
        self.add_messenger("내부통제 자가진단 결과 제출 부탁드립니다", "내부통제 자가진단 결과 제출 부탁드립니다")
        c = self.collector_with_rows([lrow(no="계약부-1", title=FULL_TITLE)])
        c.collect_and_register()
        self.list_windows.rows = [lrow(no="계약부-2", title="2026년 내부통제 자가진단 결과 보완 제출 요청")]
        self.assertEqual(len(c.collect_and_register()), 1)
        self.assertEqual(len(self.open_tasks()), 2)

    def test_fold_keeps_user_edits_of_dropped_task(self):  # 지적 4
        self.add_messenger("자가진단 결과 제출 부탁", "감사실 내부통제 자가진단 공문 왔습니다. 11/7까지 제출 부탁")
        c = self.collector_with_rows([lrow(no="계약부-901", title=FULL_TITLE)])
        c.collect_and_register()
        list_task = self.db.find_task_by_keys(["DOC:계약부-901"])
        list_task.status = TaskStatus.IN_PROGRESS
        list_task.priority = 1
        list_task.description += "\n내 메모: 김 대리에게 자료 요청함"
        self.db.update_task(list_task)
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, BODY))
        c.collect_and_register()
        tasks = self.open_tasks()
        self.assertEqual(len(tasks), 1)
        kept = tasks[0]
        self.assertEqual(kept.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(kept.priority, 1)
        self.assertIn("내 메모: 김 대리에게 자료 요청함", kept.description)
        self.assertIn("처리구분: 업무담당자", kept.description)

    def test_fold_needs_two_topic_words(self):  # 지적 4: 지우는 합치기는 주제어 1개로 하지 않음
        self.add_messenger("자가진단 표 확인 부탁", "자가진단 표 확인 부탁 11/7")
        c = self.collector_with_rows([lrow(no="감사실-456", title="자가진단 결과 제출")])
        c.collect_and_register()
        self.list_windows.rows = []
        self.windows.set(1, TITLE, (TEXT, BODY))
        c.collect_and_register()
        self.assertEqual(len(self.open_tasks()), 2)

    def test_assigned_distribution_confirms_review_message_and_is_list_task(self):  # 지적 5
        msg_id = self.add_messenger("내부통제 자가진단 결과 제출 부탁드립니다", "내부통제 자가진단 결과 제출 부탁드립니다",
                                    needs_review=True)
        c = self.collector_with_rows([lrow(no="계약부-901", title=FULL_TITLE)])
        c.collect_and_register()
        merged = self.db.get_task(msg_id)
        self.assertFalse(merged.needs_review)  # 업무담당자 배부는 확실한 원본
        self.assertTrue(c.is_list_task(merged))
        self.assertEqual(c.dismiss_task(merged), "LIST:DOC:계약부-901")
        self.assertEqual(self.db.get_workmgmt_doc("LIST:DOC:계약부-901")["result"], "업무 아님")

    def test_manager_distribution_keeps_review_message_review(self):  # C6 그대로
        msg_id = self.add_messenger("내부통제 자가진단 결과 제출 부탁드립니다", "내부통제 자가진단 결과 제출 부탁드립니다",
                                    needs_review=True)
        self.collector_with_rows([lrow(no="계약부-901", title=FULL_TITLE, role="업무관리자")]).collect_and_register()
        self.assertTrue(self.db.get_task(msg_id).needs_review)


class TestHelpers(unittest.TestCase):
    def test_parse_js_result(self):
        self.assertEqual(parse_js_result("TEXT:본문"), (TEXT, "본문"))
        self.assertEqual(parse_js_result("ERR:권한 없음"), (ERR, "권한 없음"))
        self.assertEqual(parse_js_result("REQ"), (REQ, ""))
        self.assertEqual(parse_js_result("NOCTRL"), (NOCTRL, ""))
        self.assertEqual(parse_js_result(None), (NOJS, ""))
        self.assertEqual(parse_js_result("이상한 값"), (NOJS, ""))

    def test_topic_words_drop_common_words_and_numbers(self):
        self.assertEqual(topic_words("2026년 내부통제 자가진단 결과 제출 시행 알림"), ["내부통제", "자가진단"])

    def test_shares_topic(self):
        self.assertTrue(shares_topic("2026년 내부통제 자가진단 결과 제출", "감사실 내부통제 자가진단 공문 왔습니다"))
        self.assertFalse(shares_topic("2026년 내부통제 자가진단 결과 제출", "내부통제 교육 안내"))
        self.assertFalse(shares_topic("결과 제출 요청", "결과 제출 요청"))  # 흔한 단어뿐이면 같은 일로 보지 않음


if __name__ == "__main__":
    unittest.main()
