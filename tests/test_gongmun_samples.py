"""실제 공문 표본(저장소 밖)에서 찾은 문제를 가상 문장으로 재현한 테스트.

- 통보형 표현('알려드리오니', '안내해드립니다', '참고하여 주시기 바랍니다', '유의하시기 바랍니다')은 공지 감점, 부탁 표현 아님
- 요청 표현이 전혀 없는 문서(통보·증명)는 제외 (쪽지는 기존 규칙 그대로)
- 기한 오독: 'M.D.(요일)', 인용 문서번호 뒤 날짜, 날짜만 있는 줄(인쇄 표시), 시행일 이전 날짜
"""

import os
import tempfile
import unittest
from datetime import date

from collectors.workmgmt_collector import WorkMgmtDocCollector
from core.database import DatabaseManager
from parsers.message_scorer import EXCLUDE, REGISTER, REVIEW, score_message
from parsers.portal_parser import PortalAndMessengerParser
from parsers.rule_parser import RuleBasedExtractor

REF = date(2026, 11, 6)


def collector():
    return WorkMgmtDocCollector(
        DatabaseManager(":memory:"), PortalAndMessengerParser(), reader=lambda *a, **k: [], list_reader=lambda *a, **k: []
    )


class NoticePhraseTest(unittest.TestCase):
    def test_notice_document_excluded(self):
        body = (
            "제 목 가상 지침 송부\n"
            "가상 지침을 알려드리오니 업무에 참고하여 주시기 바랍니다.\n"
            "시행 가상부-456 ( 2026.10.21. )\n"
        )
        self.assertEqual(collector().judge_document(body).verdict, EXCLUDE)

    def test_note_with_notice_phrase_excluded(self):
        r = score_message("가상 행사 일정을 알려드리니 참고하시기 바랍니다.")
        self.assertEqual(r.verdict, EXCLUDE)

    def test_notice_ask_is_not_request(self):
        for s in ("참고하여 주시기 바랍니다.", "유의하시기 바랍니다.", "숙지하시기 바랍니다.", "참고해 주시기 바랍니다."):
            with self.subTest(s=s):
                r = score_message(f"가상 자료 송부 건입니다. {s}")
                self.assertNotIn("부탁 표현 +3", r.reasons)

    def test_real_request_still_counts_with_notice_phrase(self):
        body = "가상 절차 종료를 알려드리니\n가상 절차에 따라 처리하여 주시기 바랍니다.\n붙임 1부.\n"
        r = collector().judge_document(body)
        self.assertIn("부탁 표현 +3", r.reasons)
        self.assertIn("공지·공유 -3", r.reasons)
        self.assertEqual(r.verdict, REVIEW)

    def test_notice_lead_with_explicit_deadline_not_penalized(self):
        # 공모: '알려드리니 참여해 주시기 바랍니다' + 접수기간 → 기한 안에 할 일이라 감점 없음
        body = (
            "가상 공모 계획을 알려드리니 적극 참여해\n주시기 바랍니다.\n"
            "1. 접수기간 : '26.11.3(화) ~ 11.20(금)(기한 엄수)\n"
            "2. 제출방법 : 공문 제출\n"
        )
        r = collector().judge_document(body)
        self.assertNotIn("공지·공유 -3", r.reasons)
        self.assertEqual(r.verdict, REGISTER)

    def test_guidance_with_conditional_request_needs_review(self):
        body = (
            "가상 서비스 확대를 안내해드립니다.\n"
            "일정을 유의하시기 바랍니다.\n"
            "필요하면 사전 협의서를 제출해주시기 바람\n"
        )
        r = collector().judge_document(body)
        self.assertIn("공지·공유 -3", r.reasons)
        self.assertEqual(r.verdict, REVIEW)


class NoRequestDocumentTest(unittest.TestCase):
    def test_document_without_request_excluded(self):
        body = (
            "가상 용역 이행 실적 증명서\n"
            "계약기간\n2024.11.01\n~\n2027.01.31\n"
            "위와 같이 용역 이행 실적을 증명함. 처리 담당 가상부\n"
        )
        r = collector().judge_document(body)
        self.assertEqual(r.verdict, EXCLUDE)
        self.assertIn("요청 표현 없는 문서(통보·증명) → 제외", r.reasons)

    def test_note_without_request_keeps_rule(self):
        # 쪽지는 요청 표현이 없어도 기존 점수 그대로 (짧은 지시 '회의자료 제출 11/5까지')
        self.assertNotEqual(score_message("회의자료 제출 11/5까지").verdict, EXCLUDE)

    def test_document_with_request_registers(self):
        body = "가상 자료를 작성하여 11.20.(금)까지 회신하여 주시기 바랍니다.\n시행 가상부-1 ( 2026.11.02. )\n"
        self.assertEqual(collector().judge_document(body).verdict, REGISTER)


class DeadlineMisreadTest(unittest.TestCase):
    def setUp(self):
        self.ex = RuleBasedExtractor()

    def due(self, text):
        d, _t, est = self.ex.extract_due(text, ref_date=REF)
        return d, est

    def test_month_day_with_dot_before_weekday(self):
        text = (
            "가. 교육일정 : 2026.12.2.(수) ~ 12.3.(목) (1박2일)\n"
            "○ 신청기한 : 11.17.(화)\n"
        )
        self.assertEqual(self.due(text), ("2026-11-17", False))
        task = self.ex.extract_tasks("가상 교육 신청 바랍니다.\n" + text, ref_date=REF, already_judged=True)[0]
        self.assertEqual((task.due_date, task.start_date), ("2026-11-17", ""))

    def test_month_day_with_dot_before_until(self):
        self.assertEqual(self.due("자료 11.7.까지 제출"), ("2026-11-07", False))

    def test_month_day_with_dot_at_end_of_deadline_line(self):
        self.assertEqual(self.due("신청기한 : 11.17."), ("2026-11-17", False))

    def test_decimal_at_end_of_plain_line_not_date(self):
        # 마감 키워드 없는 줄 끝의 소수는 날짜가 아님 (base와 같이 추정)
        for text, wrong in (("전년 대비 증가율 3.5.", "2027-03-05"), ("버전 2.1.", "2027-02-01")):
            with self.subTest(text=text):
                d, est = self.due(text)
                self.assertTrue(est)
                self.assertNotEqual(d, wrong)

    def test_cited_doc_date_not_deadline(self):
        text = (
            "1. 관련: 가상부-123(2026.08.27.)\n"
            "2. 가상부-789(2026.10.8.) 「가상 안내」에 따라 협조 바랍니다.\n"
            "시행 가상부-456 ( 2026.10.21. )\n"
        )
        d, est = self.due(text)
        self.assertTrue(est)
        self.assertNotIn(d, ("2026-08-27", "2026-10-08", "2026-10-21"))

    def test_lone_date_line_ignored(self):
        # 공문(시행 줄 있음)의 날짜만 있는 줄은 인쇄 표시
        d, est = self.due("가상 시스템 반영을 수정하여 주시기 바랍니다.\n10/21\n시행 가상부-456 ( 2026.10.20. )\n")
        self.assertTrue(est)
        self.assertNotEqual(d, "2026-10-21")

    def test_lone_date_after_deadline_word_kept(self):
        self.assertEqual(self.due("회신기한\n11/20\n"), ("2026-11-20", False))
        self.assertEqual(self.due("회신기한\n11/20\n시행 가상부-456 ( 2026.11.02. )\n"), ("2026-11-20", False))

    def test_lone_date_in_note_kept(self):
        self.assertEqual(self.due("보고서 제출\n11/10"), ("2026-11-10", False))

    def test_same_day_as_enforcement_kept(self):
        self.assertEqual(self.due("11/6까지 회신\n시행 가상부-456 ( 2026.11.06. )\n"), ("2026-11-06", False))

    def test_approval_line_date_not_deadline(self):
        d, est = self.due("가상 자료입니다.\n전결 11/10\n시행 가상부-456 ( 2026.11.02. )\n")
        self.assertTrue(est)

    def test_date_before_enforcement_not_deadline(self):
        text = (
            "가상 지시('25.11.3)로 중단된 절차가 종료되었습니다.\n"
            "전결 10/21\n"
            "시행 가상부-456 ( 2026.10.21. ) 접수 가상부-9 ( 2026.10.21 )\n"
        )
        d, est = self.due(text)
        self.assertTrue(est)
        self.assertNotIn(d, ("2025-11-03", "2026-10-21"))

    def test_period_start_may_equal_enforcement(self):
        text = (
            "1. 접수기간 : '26.11.3(화) ~ 11.20(금)(기한 엄수)\n"
            "제출하여 주시기 바랍니다.\n"
            "시행 가상부-456 ( 2026.11.03. )\n"
        )
        task = self.ex.extract_tasks(text, ref_date=REF, already_judged=True)[0]
        self.assertEqual((task.start_date, task.due_date), ("2026-11-03", "2026-11-20"))

    def test_deadline_after_enforcement_kept(self):
        text = "회신기한 : 2026.11. 9(월)까지\n시행 가상부-456 ( 2026.11.04. )\n"
        self.assertEqual(self.due(text), ("2026-11-09", False))


class GongmunSampleEvalScriptTest(unittest.TestCase):
    def test_prints_only_index_and_verdict(self):
        from scripts.gongmun_sample_eval import evaluate, report

        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (
                ("a.txt", "가상 비밀 제목\n가상 자료를 11.20.(금)까지 회신하여 주시기 바랍니다.\n"),
                ("b.txt", "가상 비밀 제목\n가상 지침을 알려드리오니 참고하여 주시기 바랍니다.\n"),
            ):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write(body)
            lines = report(evaluate(tmp, REF))
        out = "\n".join(lines)
        self.assertNotIn("비밀", out)
        self.assertEqual(lines[1], "1: 등록, 2026-11-20(명시), 기간 없음")
        self.assertTrue(lines[2].startswith("2: 제외"))


if __name__ == "__main__":
    unittest.main()
