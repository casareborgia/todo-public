"""규칙 파서 버그 수정 회귀 테스트 (기준일 2026-10-29 목요일, 가상 메시지)."""

from datetime import date
import os
import unittest

from core.models import TaskSource
from parsers.portal_parser import PortalAndMessengerParser
from parsers.rule_parser import RuleBasedExtractor

REF = date(2026, 10, 29)  # 목요일
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRuleParserBugfixes(unittest.TestCase):
    def setUp(self):
        self.extractor = RuleBasedExtractor()
        self.parser = PortalAndMessengerParser(self.extractor)

    def parse(self, text):
        return self.parser.parse_plain_text(text, ref_date=REF)

    # 버그 1: 대화 헤더의 발신 시각을 마감 시각으로 오인
    def test_chat_header_time_is_not_deadline(self):
        items = self.parse("[최팀장] 오전 8:50\n오늘 오후 5시까지 각자 주간업무실적 시스템에 입력 바랍니다.")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].due_date, "2026-10-29")
        self.assertEqual(items[0].due_time, "17:00")

    def test_chat_header_without_deadline_time_uses_default(self):
        items = self.parse("[박대리] 오후 03:10\n예산 내역서 검토 부탁드립니다.")
        self.assertEqual(items[0].due_time, "18:00")

    def test_chat_header_sender_is_kept(self):
        items = self.parse("[이영희 과장] 오전 09:20\n내일까지 내역서 검토 부탁드립니다.")
        self.assertIn("이영희 과장", items[0].source_ref)

    # 버그 2: '[배부] 제목' 다음 줄의 '문서번호:'에서 공문이 2건으로 쪼개짐
    def test_portal_doc_with_doc_no_is_single_task(self):
        doc = (
            "[배부] 개인정보 보호 실태 점검 결과 회신 요청\n"
            "문서번호: 감사실-1204\n"
            "처리기한: 2026-11-06\n"
            "시행일: 2026-10-27"
        )
        items = self.parse(doc)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].due_date, "2026-11-06")
        self.assertIn("개인정보 보호 실태 점검", items[0].title)

    def test_repeated_doc_numbers_still_split(self):
        docs = (
            "문서번호: 총무-1\n제목: 비품 수요조사 회신\n기한: 2026-11-02\n"
            "문서번호: 총무-2\n제목: 연차계획 제출\n기한: 2026-11-09"
        )
        items = self.parse(docs)
        self.assertEqual([t.due_date for t in items], ["2026-11-02", "2026-11-09"])

    def test_chat_log_not_split_at_inner_tag(self):
        log = (
            "[최민수 팀장] 오전 10:05\n[긴급] 점검표 오늘 16:00까지 확인해서 보고 바랍니다.\n"
            "[박대리] 오후 02:10\n주간보고 양식 금요일 오전 11시까지 공유 부탁드립니다."
        )
        items = self.parse(log)
        self.assertEqual(len(items), 2)
        self.assertEqual((items[0].due_date, items[0].due_time), ("2026-10-29", "16:00"))
        self.assertEqual((items[1].due_date, items[1].due_time), ("2026-10-30", "11:00"))

    # 버그 3: 메신저 메시지를 사내포털로 분류
    def test_messenger_source_detected(self):
        self.assertEqual(self.parse("[박대리] 오후 4:40\n자료 검토 부탁드립니다.")[0].source, TaskSource.MESSENGER)
        self.assertEqual(self.parse("[김철수 팀장] 자료 내일까지 보고 바랍니다.")[0].source, TaskSource.MESSENGER)
        self.assertEqual(self.parse("[배부] 교육 이수 안내\n문서번호: 교육-1\n기한: 11/20까지")[0].source, TaskSource.PORTAL)

    # 버그 4: 기본 기한이 주말에 걸림
    def test_default_deadline_skips_weekend(self):
        items = self.parse("신규 사업 아이디어 정리해서 보고해 주세요.")
        self.assertEqual(items[0].due_date, "2026-11-03")  # 목 + 3영업일 = 화

    def test_default_deadline_skips_configured_holiday(self):
        extractor = RuleBasedExtractor({"holidays": ["2026-11-02"]})
        items = extractor.extract_tasks("신규 사업 아이디어 정리해서 보고해 주세요.", ref_date=REF)
        self.assertEqual(items[0].due_date, "2026-11-04")

    # 버그 5: '다음 달 N일', '이번 주 내로', 'N시 반' 미인식
    def test_next_month_day(self):
        self.assertEqual(self.parse("연간계획서 다음 달 5일까지 제출 바랍니다.")[0].due_date, "2026-11-05")
        self.assertEqual(self.parse("다음 달 31일까지 제출 바랍니다.")[0].due_date, "2026-11-30")

    def test_within_this_and_next_week(self):
        self.assertEqual(self.parse("이번 주 내로 점검표 회신 부탁드립니다.")[0].due_date, "2026-10-30")
        self.assertEqual(self.parse("차주 중 결과 보고 바랍니다.")[0].due_date, "2026-11-06")

    def test_half_hour(self):
        self.assertEqual(self.parse("오늘 오후 2시 반까지 자료 회신 부탁드립니다.")[0].due_time, "14:30")
        self.assertEqual(self.parse("내일 3시 반까지 제출 바랍니다.")[0].due_time, "15:30")

    # 버그 6: 제목에 호칭·인사말이 남음
    def test_title_strips_address_and_lead_in(self):
        self.assertTrue(self.parse("[정과장] 오전 11:20\n주무관님, 금요일 회의자료 10부 출력 부탁드려요.")[0].title.startswith("금요일 회의자료"))
        self.assertTrue(self.parse("[최팀장] 오전 10:00\n두 가지 부탁드려요. 예산안 검토는 금요일까지 해주세요.")[0].title.startswith("예산안 검토"))

    def test_title_keeps_honorific_object(self):
        self.assertTrue(self.parse("과장님께 보고서 내일까지 제출 바랍니다.")[0].title.startswith("과장님께"))

    # 샘플 파일 회귀
    def test_sample_files(self):
        def read(name):
            with open(os.path.join(ROOT, "tests", "fixtures", name), encoding="utf-8") as f:
                return f.read()

        portal = self.parse(read("샘플1_사내포털_배부문서.txt"))
        self.assertEqual([t.due_date for t in portal], ["2026-10-15", "2026-10-29", "2026-11-05"])

        chat = self.parse(read("샘플2_사내메신저_업무요청.txt"))
        self.assertEqual([t.due_time for t in chat], ["15:00", "16:00", "11:00"])
        self.assertTrue(all(t.source == TaskSource.MESSENGER for t in chat))


class TestReplyAndTitleV111(unittest.TestCase):
    """1.1.1: 내부망 실사용에서 확인한 쪽지 오등록·제목 오인 (EzQ 쪽지 본문 형태, 가상 내용)."""

    def setUp(self):
        self.extractor = RuleBasedExtractor()

    def tasks(self, body):
        return self.extractor.extract_tasks(body, source_hint=TaskSource.MESSENGER, ref_date=REF)

    # 버그 1: '확인'이 들어간 단순 답장이 업무로 등록됨
    def test_acknowledgement_reply_is_not_task(self):
        for body in (
            "확인했습니다",
            "넵 확인했습니다 감사합니다",
            "네, 확인했습니다!",
            "확인 완료했습니다. 수고하세요~",
            "네 잘 받았습니다. 감사합니다 ^^",
            "확인했습니다.\n\n발신자 : 홍길동",
        ):
            with self.subTest(body=body):
                self.assertEqual(self.tasks(body), [])

    def test_request_with_acknowledgement_is_still_task(self):
        for body in (
            "확인 부탁드립니다.",
            "확인했습니다. 수정본은 내일까지 다시 보내주세요.",
            "아래 자료 확인하시고 회신 바랍니다.",
        ):
            with self.subTest(body=body):
                self.assertEqual(len(self.tasks(body)), 1)

    # 버그 2: 인사 뒤 자기소개 줄이 제목으로 잡힘
    def test_title_skips_greeting_and_self_introduction(self):
        cases = {
            "안녕하십니까\n기획팀 홍길동입니다.\n\n아래 자료 금요일까지 제출 부탁드립니다.": "아래 자료 금요일까지 제출",
            "안녕하세요.\n총무팀 김철수 과장입니다.\n회의실 예약 현황 확인 부탁드립니다.": "회의실 예약 현황",
            "안녕하세요. 예산팀 이영희입니다. 예산 내역서 내일까지 검토 부탁드립니다.": "예산 내역서",
            "홍길동입니다.\n출장 계획서 작성 요청드립니다.": "출장 계획서",
        }
        for body, start in cases.items():
            with self.subTest(body=body):
                self.assertTrue(self.tasks(body)[0].title.startswith(start), self.tasks(body)[0].title)

    def test_title_keeps_sentence_ending_with_ipnida(self):
        self.assertTrue(self.tasks("긴급 요청입니다.\n보고서 내일까지 제출 바랍니다.")[0].title.startswith("긴급 요청입니다"))


if __name__ == "__main__":
    unittest.main()


class TestTitleSkipsAttachmentLines(unittest.TestCase):
    """쪽지 본문이 '첨부파일:' 줄로 시작하면 첨부파일 이름이 업무 제목이 되던 문제 (실사용 2026-11-03, 가상 내용)."""

    def setUp(self):
        self.extractor = RuleBasedExtractor()

    def test_attachment_line_is_not_title(self):
        cases = {
            "첨부파일:통제평가결과_계약부.txt\n\n계약부의 내부통제 결과 작성하여 보내드립니다.\n11/5까지 확인바랍니다.":
                "계약부의 내부통제 결과 작성하여 보내드립니다.",
            "\n첨부파일:(붙임1) 작성양식.hwp\n첨부 파일 : (붙임2) 매뉴얼.zip\n\n하기 양식으로 11/4까지 작성 요청드립니다.":
                "하기 양식으로 11/4까지 작성 요청드립니다.",
        }
        for body, title in cases.items():
            self.assertEqual(self.extractor.extract_title(body), title)

    def test_attachment_only_message_keeps_file_name(self):
        # 첨부파일 줄뿐이면 그거라도 제목으로 (빈 제목 방지)
        self.assertIn("양식.hwp", self.extractor.extract_title("첨부파일:양식.hwp"))


# 웹한글 기안기 본문 (내부망 점검 4차 결과의 줄 모양을 가상 내용으로 재구성)
WEBHWP_BODY = """가상 회사 머리말
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
시행 감사실-456 (2026.11.03.)
접수 기획실-789 (2026.11.03.)
"""


class TestCitedDocumentNumbers(unittest.TestCase):
    """공문 본문에 인용된 다른 문서의 번호를 이 문서의 번호로 쓰지 않음 (예전 공문 업무와 잘못 합쳐지던 문제)."""

    def setUp(self):
        self.extractor = RuleBasedExtractor()

    def extract(self, text):
        return self.extractor.extract_tasks(text, source_hint=TaskSource.PORTAL, ref_date=date(2026, 11, 3))

    def test_webhwp_body_key_deadline_and_title(self):
        task = self.extract(WEBHWP_BODY)[0]
        self.assertEqual(task.external_key, "DOC:감사실-456")
        self.assertEqual(task.due_date, "2026-11-07")
        self.assertFalse(task.due_estimated)
        self.assertEqual(task.title, "2026년 내부통제 자가진단 결과 제출 시행 알림")

    def test_cited_number_is_not_a_match_key(self):
        keys = self.extract(WEBHWP_BODY)[0].match_keys
        self.assertNotIn("DOC:감사실-123", keys)
        self.assertIn("DOC:감사실-456", keys)
        self.assertIn("DOC:기획실-789", keys)

    def test_cited_number_with_colon_is_not_own_number(self):
        text = "문서번호: 감사실-123(2026.10.30.) 「점검 계획」 관련\n자료를 11.10.(화)까지 제출 바랍니다.\n시행 감사실-456 (2026.11.03.)"
        self.assertEqual(self.extract(text)[0].external_key, "DOC:감사실-456")

    def test_plain_doc_number_line_still_used(self):
        text = "문서번호: 감사실-1204\n자료를 11.10.(화)까지 제출 바랍니다."
        task = self.extract(text)[0]
        self.assertEqual(task.external_key, "DOC:감사실-1204")
        self.assertIn("DOC:감사실-1204", task.match_keys)

    def test_doc_number_followed_by_title_word_relation_in_list_row_kept(self):
        # 목록 한 줄: 번호 뒤에 바로 '관련'이 오지 않으면 이 문서의 번호
        text = "문서번호: 총무팀-55 | 제목: 관련 부서 자료 제출 요청 11.10.(화)까지"
        self.assertEqual(self.extract(text)[0].external_key, "DOC:총무팀-55")

    def test_cited_doc_does_not_merge_into_old_task(self):
        import tempfile
        from core.database import DatabaseManager
        from core.models import TaskItem
        with tempfile.TemporaryDirectory() as d:
            db = DatabaseManager(os.path.join(d, "t.db"))
            old = TaskItem(title="하반기 자가진단 계획", source=TaskSource.PORTAL, external_key="DOC:감사실-123", due_date="2026-10-31")
            db.add_task(old)
            new_id, state = db.upsert_scanned_task(self.extract(WEBHWP_BODY)[0])
            self.assertEqual(state, "created")
            self.assertEqual(db.find_task_by_keys(["DOC:감사실-123"]).due_date, "2026-10-31")
