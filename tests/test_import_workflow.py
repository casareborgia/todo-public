"""업무관리시스템 CSV 목록 + 공문 PDF + 메신저 저장 파일 수집 흐름 테스트 (가상 데이터).

fixtures/sample_gongmun.pdf 는 fixtures/sample_gongmun.html 을 Chrome '인쇄 → PDF'로 만든 가상 공문입니다.
"""

from datetime import date
import os
import shutil
import sqlite3
import tempfile
import unittest

from collectors.folder_watcher import FolderWatcherCollector
from core.database import DatabaseManager
from core.models import TaskSource, TaskStatus
from parsers.pdf_text import extract_pdf_text
from parsers.portal_parser import PortalAndMessengerParser
from parsers.rule_parser import RuleBasedExtractor

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SAMPLE_PDF = os.path.join(FIXTURES, "sample_gongmun.pdf")

CSV_HEADER = "번호,문서번호,제목,기안부서,접수일,처리상태\n"
AUDIT_ROW = "1,정보보호팀-3321,개인정보 보호 실태 점검 결과 회신 요청,감사실,2026-10-28,{status}\n"
DONE_ROW = "2,총무과-881,비품 수요조사 회신,총무과,2026-10-20,처리완료\n"
PLAN_ROW = "3,기획실-5104,연말 사업실적 자체평가 보고서 제출,기획실,2026-10-29,미처리\n"


class TestPdfText(unittest.TestCase):
    def test_extracts_korean_lines(self):
        text = extract_pdf_text(SAMPLE_PDF)
        self.assertIn("제목 개인정보 보호 실태 점검 결과 회신 요청", text)
        self.assertIn("2026. 11. 6.(금)까지 회신하여 주시기 바랍니다.", text)
        self.assertIn("시행 감사실-1204 (2026. 10. 27.)", text)

    def test_pdf_task_deadline_and_doc_numbers(self):
        items = PortalAndMessengerParser().parse_pdf_file(SAMPLE_PDF, ref_date=date(2026, 10, 29))
        self.assertEqual(len(items), 1)
        t = items[0]
        self.assertEqual(t.title, "개인정보 보호 실태 점검 결과 회신 요청")
        self.assertEqual(t.due_date, "2026-11-06")
        self.assertFalse(t.due_estimated)
        self.assertIn("DOC:감사실-1204", t.match_keys)
        self.assertIn("DOC:정보보호팀-3321", t.match_keys)
        self.assertNotIn("DOC:감사실-1180", t.match_keys)  # '관련' 문서는 다른 문서

    def test_non_pdf_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "broken.pdf")
            with open(path, "wb") as f:
                f.write(b"not a pdf")
            self.assertEqual(PortalAndMessengerParser().parse_pdf_file(path), [])


class TestImportWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = os.path.join(self.tmp.name, "inbox")
        os.makedirs(self.inbox)
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))
        # CSV 목록은 기본 제외이므로, 목록 연동 흐름을 시험하려고 명시적으로 켬
        self.watcher = FolderWatcherCollector(
            self.db, PortalAndMessengerParser(RuleBasedExtractor()), watch_folders=[self.inbox],
            supported_extensions=[".txt", ".pdf", ".csv"],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, enc="cp949"):
        with open(os.path.join(self.inbox, name), "w", encoding=enc) as f:
            f.write(text)

    def by_key(self, key):
        return self.db.find_task_by_keys([key])

    def test_daily_csv_pdf_and_completion(self):
        self.write("목록_1028.csv", CSV_HEADER + AUDIT_ROW.format(status="미처리") + DONE_ROW)
        self.assertEqual(len(self.watcher.collect_and_register()), 1)  # 처리완료 문서는 등록 안 함

        audit = self.by_key("DOC:정보보호팀-3321")
        self.assertTrue(audit.due_estimated)  # 목록에는 처리기한이 없음
        self.assertNotEqual(audit.due_date, "2026-10-28")  # 접수일을 마감일로 쓰지 않음

        # 같은 목록을 다른 파일명으로 다시 받아도 중복 없음, 신규 문서만 추가
        self.write("목록_1029.csv", CSV_HEADER + AUDIT_ROW.format(status="미처리") + DONE_ROW + PLAN_ROW)
        new = self.watcher.collect_and_register()
        self.assertEqual([t.title for t in new], ["연말 사업실적 자체평가 보고서 제출"])

        # 공문 PDF: 접수번호로 목록의 업무를 찾아 처리기한 확정
        shutil.copy(SAMPLE_PDF, os.path.join(self.inbox, "공문.pdf"))
        self.assertEqual(self.watcher.collect_and_register(), [])
        audit = self.by_key("DOC:정보보호팀-3321")
        self.assertEqual((audit.due_date, audit.due_estimated), ("2026-11-06", False))
        self.assertIn("PDF: 공문.pdf", audit.source_ref)

        # 목록에서 처리완료로 바뀌면 업무도 완료
        self.write("목록_1030.csv", CSV_HEADER + AUDIT_ROW.format(status="처리완료") + PLAN_ROW)
        self.watcher.collect_and_register()
        self.assertEqual(self.by_key("DOC:정보보호팀-3321").status, TaskStatus.COMPLETED)
        self.assertEqual(len(self.db.query_tasks()), 2)

    def test_csv_is_ignored_by_default(self):
        watcher = FolderWatcherCollector(
            self.db, PortalAndMessengerParser(RuleBasedExtractor()), watch_folders=[self.inbox]
        )
        self.write("목록.csv", CSV_HEADER + AUDIT_ROW.format(status="미처리"))
        shutil.copy(SAMPLE_PDF, os.path.join(self.inbox, "공문.pdf"))
        self.assertEqual([t.source_ref.split(" | ")[-1] for t in watcher.collect_and_register()], ["PDF: 공문.pdf"])

    def test_pdf_without_list_creates_task(self):
        shutil.copy(SAMPLE_PDF, os.path.join(self.inbox, "공문.pdf"))
        new = self.watcher.collect_and_register()
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0].due_date, "2026-11-06")

    def test_uncompleted_status_is_not_completed(self):
        self.write("목록.csv", CSV_HEADER + AUDIT_ROW.format(status="미완료"))
        self.assertEqual(len(self.watcher.collect_and_register()), 1)
        self.assertEqual(self.by_key("DOC:정보보호팀-3321").status, TaskStatus.PENDING)

    def test_messenger_file_uses_sent_date(self):
        self.write("이영희-2026-10-20-09-20-15.txt", "주무관님, 예산 내역서 내일까지 검토 부탁드립니다.", enc="utf-8")
        new = self.watcher.collect_and_register()
        self.assertEqual(len(new), 1)
        t = new[0]
        self.assertEqual(t.due_date, "2026-10-21")
        self.assertEqual(t.source, TaskSource.MESSENGER)
        self.assertEqual(t.external_key, "MSG:이영희|2026-10-20 09:20:15|0")
        self.assertIn("이영희", t.source_ref)

        # 같은 메시지를 다른 폴더 경로로 다시 저장해도 중복 없음
        os.makedirs(os.path.join(self.inbox, "again"))
        self.write(os.path.join("again", "이영희-2026-10-20-09-20-15.txt"), "주무관님, 예산 내역서 내일까지 검토 부탁드립니다.", enc="utf-8")
        self.assertEqual(self.watcher.collect_and_register(), [])

    def test_dated_chat_header_uses_sent_date(self):
        items = PortalAndMessengerParser().parse_plain_text(
            "[이과장] 2026-10-20 오전 09:20\n예산 내역서 내일까지 검토 부탁드립니다.",
            ref_date=date(2026, 10, 29),
        )
        self.assertEqual(items[0].due_date, "2026-10-21")


SEP = "-" * 53
END = "-" * 79


def _export(top_sender, top_time, title, body, history=()):
    """사내 메신저 저장 파일 형식 (실제 형식을 본뜬 가상 내용)."""
    parts = [f"송신:{top_sender}\n시간:{top_time}:000\n제목:{title}\n내용:{body}\n\n"]
    for sender, to, when, text in history:
        parts.append(f"{SEP}\n발신자 :{sender}\n수신자 :{to}\n날 짜 :{when}\n\n\n{text}\n\n")
    return "".join(parts) + END + "\n"


class TestMessengerExportFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parser = PortalAndMessengerParser()

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="cp949") as f:
            f.write(text)
        return self.parser.parse_file(path)

    def test_only_top_message_becomes_task(self):
        items = self.parse("홍길동-2026-10-29_125415.txt", _export(
            "홍길동", "2026-10-29 09:35:07",
            "[다음주 팀장님 출장 일정 작성 요청 - 오전까지]",
            "안녕하십니까\n기획팀 홍길동입니다.\n\n아래 내용 확인하시어 오늘 오전까지 송부 부탁드립니다!",
            [("홍길동", "가나다,라마바", "2026-10-28 10:31:49", "출장 계획 작성하셔서\n내일 오전까지 쪽지 부탁드립니다."),
             ("임꺽정", "홍길동", "2026-10-28 09:05:00", "주간 출장계획 작성 요청드립니다.\n회신기한 : 10.29(목) 오후 16시까지")],
        ))
        self.assertEqual(len(items), 1)  # 인용된 이전 대화는 따로 등록하지 않음
        t = items[0]
        self.assertEqual(t.title, "다음주 팀장님 출장 일정 작성 요청 - 오전까지")
        self.assertEqual((t.due_date, t.due_time, t.due_estimated), ("2026-10-29", "12:00", False))
        self.assertEqual(t.external_key, "MSG:홍길동|2026-10-29 09:35:07|0")  # 파일 이름이 아닌 메시지 시각
        self.assertIn("회신기한 : 10.29(목)", t.description)  # 이전 대화는 참고용 설명으로 보존

    def test_reply_without_request_is_not_a_task(self):
        items = self.parse("홍길동-2026-10-29_105510.txt", _export(
            "홍길동", "2026-10-29 09:52:19", "알겠습니다. 다시 연락드릴게요", "알겠습니다. 다시 연락드릴게요\n\n감사합니다.",
            [("나", "홍길동", "2026-10-29 09:33:53", "7일 공고는 불가합니다."),
             ("홍길동", "나", "2026-10-29 09:32:06", "11/13 즈음 계약의뢰 드리면 공고기한을 언제까지 잡아야 하나요?")],
        ))
        self.assertEqual(items, [])

    def test_forward_takes_deadline_from_quoted_message(self):
        items = self.parse("홍길동-2026-10-28_110000.txt", _export(
            "홍길동", "2026-10-28 10:59:00", "출장계획 작성 요청", "아래 내용 확인 부탁드립니다.",
            [("임꺽정", "홍길동", "2026-10-28 09:05:00", "주간 출장계획 작성 요청드립니다.\n회신기한 : 10.29(목) 오후 16시까지")],
        ))
        self.assertEqual((items[0].due_date, items[0].due_time, items[0].due_estimated), ("2026-10-29", "16:00", False))

    def test_old_dashed_file_name_still_works(self):
        items = self.parse("홍길동-2026-10-20-09-20-15.txt", "예산 내역서 내일까지 검토 부탁드립니다.")
        self.assertEqual(items[0].due_date, "2026-10-21")


class TestGongmunDates(unittest.TestCase):
    def test_short_year_deadline(self):
        due = RuleBasedExtractor().extract_due(
            "을 아래와 같이 요청드리오니 기한을 엄수하여 제출하시기 바랍니다.\n"
            "※ '26.12.31 예산집행 → '27.1.4 자금집행시, 1월 계획에 반영\n"
            "3. 제출기한/방법 : '26.11.4(수) / 공문 및 메신저 동시 제출\n"
            "전결 10/28\n시행 기획팀-3133 ( 2026.10.28. ) 접수 총무팀-@N ( 2026.10.28 )",
            ref_date=date(2026, 10, 29),
        )
        self.assertEqual(due, ("2026-11-04", "18:00", False))

    def test_han_deadline_beats_event_period(self):
        # 행사 기간(11.4~11.6)보다 '限'으로 표시한 명단 송부 기한(11.2)이 할 일의 기한
        due = RuleBasedExtractor().extract_due(
            "□ 일 시 : '26.11.4(수) ~ 11.6(금), 3일간\n"
            "□ 참석자 명단\n송부\n○ 기획팀 홍길동 대리(메신저 송부, 11.2(월) 限)",
            ref_date=date(2026, 10, 29),
        )
        self.assertEqual(due[:2], ("2026-11-02", "18:00"))

    def test_spaced_title_label(self):
        text = "수신자 수신자참조\n제 목 자금계획 제출요청\n시행 기획팀-3133 ( 2026.10.28. )"
        ex = RuleBasedExtractor()
        self.assertEqual(ex.extract_title(text), "자금계획 제출요청")
        self.assertEqual(ex.extract_metadata(text)["doc_no"], "기획팀-3133")

    def test_gongmun_sender_is_issuing_department(self):
        text = "제 목 자금계획 제출요청\n붙임 : 자금계획 양식 1부. 끝.\n시행 기획팀-3133 ( 2026.10.28. )"
        self.assertEqual(RuleBasedExtractor().extract_metadata(text)["sender"], "기획팀")


class TestCsvColumnNames(unittest.TestCase):
    def parse(self, text):
        return PortalAndMessengerParser().parse_csv_text(text, ref_date=date(2026, 10, 29))

    def test_system_specific_column_names(self):
        items = self.parse(
            "순번,문서번호(접수),문서제목,진행상태\n"
            "1,정보보호팀-3321,개인정보 보호 실태 점검 결과 회신 요청,종결\n"
        )
        self.assertEqual(items[0].external_key, "DOC:정보보호팀-3321")
        self.assertEqual(items[0].title, "개인정보 보호 실태 점검 결과 회신 요청")
        self.assertEqual(items[0].status, TaskStatus.COMPLETED)

    def test_plain_number_doc_no_is_still_the_key(self):
        items = self.parse("등록번호,제목\n2026001234,연말 사업실적 자체평가 보고서 제출\n")
        self.assertEqual(items[0].external_key, "DOC:2026001234")


class TestImportCheck(unittest.TestCase):
    def test_share_report_has_no_content(self):
        from parsers.import_check import check_files

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "기밀목록.csv")
            with open(csv_path, "w", encoding="cp949") as f:
                f.write("번호,제목,접수일\n1,개인정보 보호 실태 점검 결과 회신 요청,2026-10-28\n")
            shutil.copy(SAMPLE_PDF, os.path.join(tmp, "공문.pdf"))
            with open(os.path.join(tmp, "이영희-2026-10-20-09-20-15.txt"), "w", encoding="utf-8") as f:
                f.write("예산 내역서 내일까지 검토 부탁드립니다.")

            full, share = check_files([tmp], ref_date=date(2026, 10, 29))

        self.assertIn("개인정보 보호 실태 점검 결과 회신 요청", full)
        self.assertIn("2026. 11. 6.(금)까지", full)  # PDF 본문 앞부분 미리보기
        self.assertIn("발신자 '이영희'", full)
        for secret in ("개인정보", "기밀목록", "이영희", "예산", "감사실"):
            self.assertNotIn(secret, share)
        self.assertIn("문서번호 열을 찾지 못했습니다", share)
        self.assertIn("메신저 저장 파일 이름 형식 인식됨", share)
        self.assertIn("ToUnicode", share)


class TestDatabaseMigration(unittest.TestCase):
    def test_old_schema_gets_new_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT DEFAULT '',"
                " source TEXT DEFAULT 'MANUAL', source_ref TEXT DEFAULT '', content_hash TEXT DEFAULT '',"
                " due_date TEXT DEFAULT '', due_time TEXT DEFAULT '18:00', status TEXT DEFAULT 'PENDING',"
                " label TEXT DEFAULT 'MANUAL', priority INTEGER DEFAULT 2, recurrence TEXT DEFAULT 'NONE',"
                " is_new_scan INTEGER DEFAULT 0, notified_at TEXT, snooze_until TEXT,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT)"
            )
            conn.execute(
                "INSERT INTO tasks (title, created_at, updated_at) VALUES ('기존 업무', '2026-10-01 09:00:00', '2026-10-01 09:00:00')"
            )
            conn.commit()
            conn.close()

            db = DatabaseManager(path)
            tasks = db.query_tasks()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].external_key, "")
            self.assertFalse(tasks[0].due_estimated)


if __name__ == "__main__":
    unittest.main()
