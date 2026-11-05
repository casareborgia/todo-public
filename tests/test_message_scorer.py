"""쪽지 업무 점수 판단 테스트. 본문·이름·부서는 모두 가상으로 지어낸 예시."""

import unittest

from parsers.message_scorer import EXCLUDE, REGISTER, REVIEW, score_message, strip_copied_lines

# --- 업무 (등록되어야 함) ---
CARD_SLIP = "10월 인지세등 법인카드전표내역 11/6(금)까지 처리 부탁드립니다.\n감사합니다."
WEEKLY = (
    "안녕하세요. 박민수입니다!\n\n주간업무 요청드립니다. (~ 11.04 15:00)\n"
    "○대상기간 : (실적) '26.11.2 ~ '26.11.6 / (계획) '26.11.9 ~ '26.11.13\n\n감사합니다^^\n\n"
    "첨부파일:20261109_주간업무_양식.hwp"
)
FORWARD_TOP = "이 대리님..\n아래 내용 참고해서 처리 좀 해주세요~"
FORWARD_QUOTED = (
    "사무용품 수요조사 협조를 요청드리오니\n하기 공문을 참조하시어, 각 팀별로 회신 부탁드립니다.\n"
    "2. 작년도 참고자료 : 공유폴더에 올려놓았으니 참고하시기 바랍니다."
)
TEST_BODY = "내일까지 보고서 제출할것(테스트)"

# --- 단순 공지 (제외되어야 함) ---
MEETING_RECAP = (
    "안녕하세요\n오전 팀장회의 내용 전달드립니다.\n\n"
    "1. 연말 휴가 일정은 팀 안에서 겹치지 않게 조정할 것\n\n"
    "2. 하반기 실적 정리\n - 숫자만 쓰지 말고 근거 자료도 같이 챙길 것\n"
    " - 예상 질문 몇 개 미리 준비할 것\n\n"
    "3. 사무실 정리정돈 신경 써 주세요"
)
CONTEST = (
    "[사내 사진 공모전 참여 안내]\n\n총무팀에서 알려드립니다.\n"
    "아래와 같이 가을 사진 공모전을 시행하고 있으니 직원 분들의 많은 참여 부탁드립니다.\n\n"
    "★ 참여하기 링크 ↓\nhttp://portal.example/contest.asp\n\n"
    "○ 공모기간 :'26. 10.26.(월) ~ 11. 9(월)\n○ 참가대상 : 전 직원\n"
    "○ 경품\n  - 우수작(10점 내외) : 커피 쿠폰\n\n감사합니다."
)
UNION_NEWS = (
    "안녕하십니까, 사내 동호회 총무 김영수입니다. 동호회 소식지 제5호 공유드립니다.\n\n"
    "10월 9일(금) 가을 체육대회가 개최될 예정입니다.\n"
    "회원 여러분의 많은 관심과 적극적인 참여를 부탁드립니다."
)

# --- 답장·잡담 맨 위 글 (제외되어야 함) ---
REPLIES = [
    "좋은하루 보내세요",
    "아 이런 기능이 있군요~ 있는지도 몰랐어요 ㅋㅋ\n알려주셔서 감사해요 대리님~^-^",
    "네 알겠습니다 대리님\n\n견적서나 용역설계서가 계약별로 꼭 필요한 내용이 있진 않나 보네요~?",
]


class TestMessageScorer(unittest.TestCase):
    def assertVerdict(self, text, verdict, quoted=""):
        result = score_message(text, quoted)
        self.assertEqual(result.verdict, verdict, f"점수 {result.score}: {result.reasons}")

    def test_requests_are_registered(self):
        for text in (CARD_SLIP, WEEKLY, TEST_BODY):
            self.assertVerdict(text, REGISTER)

    def test_forward_pointing_to_quote_uses_quoted_request(self):
        self.assertVerdict(FORWARD_TOP, REGISTER, quoted=FORWARD_QUOTED)

    def test_forward_without_own_request_uses_quote(self):
        self.assertVerdict("아래 공문 전달드립니다.", REGISTER, quoted="11월 5일까지 명단 회신 바랍니다.")

    def test_forwarded_notice_without_own_request_is_excluded(self):
        # 맨 위 글에 요청이 없으면 인용된 공지의 감점도 그대로 적용
        self.assertVerdict("아래 공지 전달드립니다.", EXCLUDE, quoted=CONTEST)

    def test_broadcast_notices_are_excluded(self):
        for text in (MEETING_RECAP, CONTEST, UNION_NEWS):
            self.assertVerdict(text, EXCLUDE)

    def test_replies_are_excluded(self):
        for text in REPLIES:
            self.assertVerdict(text, EXCLUDE)

    def test_broadcast_with_hard_deadline_is_still_task(self):
        # 전 직원 대상이어도 기한 있는 의무 요청은 업무 (누락 방지)
        self.assertVerdict("전 직원 대상 정보보호 필수교육 11/30까지 이수 바랍니다.", REGISTER)

    def test_notice_forward_with_deadline_request_is_task(self):
        self.assertVerdict("회의 결과 전달드립니다. 각 부서는 11/10까지 개선안 제출 바랍니다.", REGISTER)

    def test_system_alerts_are_excluded(self):
        # 결재·메일·통합 알림 쪽지 (실사용 표본 2026-11-03 형식, 이름·제목·번호는 가상)
        approval = (
            "[결재]\n\n결재 알림메시지 입니다.\n\n"
            "[ [전자문서공유/공람공람수신] 가족 체험행사 참가신청 알림 ]\n\n보낸사람 : 김영수"
        )
        mail = (
            "제 목 : [외부메일][ 협력사 ] 11/5까지 자료 제출 요청드립니다.외부메일에서 수신된 메일알림입니다."
            "인터넷용PC에서 확인하십시요.\n\n\"기능호출:클릭하시면 메일화면으로 이동합니다.\""
        )
        unified = (
            "[ 펌뱅킹지급 알림 ]\n\n\n\n보낸사람 : 회계부\n\n"
            "교육 참석 출장 ( 전표번호 : 1234567890 ) 대금이 정상적으로 지급되었습니다. ｜ 금액 : 10,000 원 ｜\n\n"
            "\"기능호출: 여기를 클릭하시면 관련화면으로 이동합니다.\""
        )
        for text in (approval, mail, unified):
            result = score_message(text)
            self.assertEqual(result.verdict, EXCLUDE, result.reasons)
            self.assertIn("시스템 알림", result.reasons[0])

    def test_chat_lookalikes_are_excluded(self):
        # '할 것 같아요'·'바람'·'보고 싶'·업무 명사 하나뿐인 잡담이 업무로 잡히던 문제
        for text in (
            "그 건은 제가 처리할 것 같아요",
            "다음 주에 보고할 것입니다.",
            "오늘 바람 불어서 춥네요",
            "주말에 영화 보고 싶네요",
            "검토해보니 괜찮네요 ㅎㅎ",
            "대출 금리 1.5 한도는 그대로입니다",
        ):
            self.assertVerdict(text, EXCLUDE)

    def test_imperative_endings_still_count(self):
        for text in ("자료 정리해서 보고할 것.", "명단 회신 바람"):
            result = score_message(text)
            self.assertIn("부탁 표현 +3", result.reasons)
            self.assertNotEqual(result.verdict, EXCLUDE)

    def test_light_request_without_deadline_or_attachment_needs_review(self):
        # 실사용 과탐 표본(2026-11-03): 요청은 맞지만 할 일로 보기 어려운 쪽지 → 버리지 않고 확인 필요
        for text in (
            "네, 사업소 확인 후 알려드리겠습니다.\n\n우선 낙찰자랑 낙찰율 등 관련 내용 좀 공유해주세요~",
            "용역기간이 1년으로 되어 있습니다.\n해당 계약의뢰는 36개월로 요청드렸습니다.\n\n확인 부탁드립니다.",
            "안녕하세요, 11월 윤리인권교육 후기를 작성 요청드립니다.\n감사합니다.",
        ):
            result = score_message(text)
            self.assertEqual(result.verdict, REVIEW, result.reasons)

    def test_request_with_attachment_or_official_doc_is_registered(self):
        self.assertVerdict("첨부한 양식으로 작성 부탁드립니다.\n\n첨부파일:작성양식.hwp", REGISTER)
        self.assertVerdict("아래 내용 참고해서 처리 좀 해주세요~", REGISTER, quoted="하기 공문을 참조하시어 회신 부탁드립니다.")

    def test_weekday_deadline_is_read(self):
        result = score_message("이번 주 금요일까지 명단 보내주세요")
        self.assertTrue(any("명시 기한" in r for r in result.reasons), result.reasons)

    def test_deadline_with_time_of_day_is_read(self):
        # '오전까지', '오후 3시까지'처럼 날짜·요일과 '까지' 사이에 시각이 끼어도 기한으로 읽음 (v1.3.0 회귀)
        for text in (
            "11/9(월) 오전까지 교육 결과 보고서 작성 부탁드립니다.",
            "다음 주 월요일 오전까지 교육 결과 보고서 작성 부탁드립니다.",
            "11월 9일 오후 3시까지 자료 제출 부탁드립니다.",
            "금요일 오후 2시까지 명단 보내주세요",
            "내일 오전 10시까지 회신 부탁드립니다.",
            "11/9 14:00까지 검토 부탁드립니다.",
            "모레 오후까지 보고서 작성 부탁드려요",
        ):
            with self.subTest(text=text):
                self.assertVerdict(text, REGISTER)

    def test_time_of_day_alone_is_not_deadline(self):
        # 시각만 있고 날짜·요일이 없으면 기한이 아님 ('오전까지 회의' 같은 잡담은 그대로)
        result = score_message("오늘은 오전까지 회의가 있어요")
        self.assertFalse(any("명시 기한" in r for r in result.reasons), result.reasons)

    def test_terse_order_with_date_needs_review(self):
        # 부탁 표현 없는 짧은 지시는 버리지 않고 확인 필요로 (누락 방지)
        self.assertVerdict("회의자료 제출 11/5", REVIEW)

    def test_lines_copied_from_quote_are_ignored(self):
        # 내가 보낸 글의 문장을 그대로 옮겨 붙이고 질문하는 답장 (실사용 표본, 내용 축약)
        mine = (
            "- 제안서의 경우 온라인 제출이 가능하지 제외하시기 바랍니다.\n"
            "- 대략 이정도로 정리하시기 바랍니다.\n보완요청공문을 보내드릴 예정이오니 확인후 수정하여 회신주시기 바랍니다."
        )
        top = (
            "- 제안서의 경우 온라인 제출이 가능하지 제외하시기 바랍니다.\n- 대략 이정도로 정리하시기 바랍니다.\n"
            "대리님 혹시 이문구가 이해가잘 되지않는데\n설명조금만 해주실수있으실가요..?"
        )
        stripped = strip_copied_lines(top, [mine])
        self.assertNotIn("제외하시기", stripped)
        self.assertIn("설명조금만", stripped)
        # 옮겨 붙인 '…바랍니다' 줄로 등록되지 않음. 남은 질문형 요청('해주실 수 있으실까요')은 확인 필요
        self.assertEqual(score_message(stripped, mine).verdict, REVIEW)

    def test_short_common_lines_are_not_stripped(self):
        self.assertEqual(strip_copied_lines("감사합니다.\n11/5까지 제출 바랍니다.", ["감사합니다."]),
                         "감사합니다.\n11/5까지 제출 바랍니다.")

    def test_completion_report_is_not_registered(self):
        self.assertVerdict("요청하신 자료 첨부했습니다.", EXCLUDE)

    def test_weak_request_needs_review(self):
        # 부탁 표현만 있고 기한·업무 명사 없음 → 확인 필요 (누락 대신 낮은 순위로 남김)
        self.assertVerdict("회의 참석 부탁드립니다.", REVIEW)

    def test_reasons_are_recorded(self):
        result = score_message(CARD_SLIP)
        self.assertTrue(any("부탁" in r for r in result.reasons))


if __name__ == "__main__":
    unittest.main()


class TestReviewFindings20261103(unittest.TestCase):
    """코드 리뷰(2026-11-03)에서 찾은 누락 위험."""

    def test_human_bracket_alert_title_is_not_system_alert(self):
        # 첫 줄 '[… 알림]'만으로는 시스템 알림이 아님 (통합알림은 '보낸사람 :' 줄이 함께 있음)
        result = score_message("[교육 이수 알림]\n11/5까지 법정교육 이수 바랍니다")
        self.assertEqual(result.verdict, REGISTER, result.reasons)

    def test_informal_and_question_requests(self):
        cases = {
            "보고서 검토 부탁드림": REVIEW,             # 기한 없는 요청 → 확인 필요
            "11/5까지 자료 취합 요청드림": REGISTER,
            "내일까지 자료 좀 부탁해": REGISTER,
            "내일까지 자료 보내주실 수 있을까요?": REGISTER,
            "이번 주 금요일까지 회신 부탁드려도 될까요?": REGISTER,
        }
        for text, verdict in cases.items():
            result = score_message(text)
            self.assertEqual(result.verdict, verdict, f"{text}: {result.reasons}")

    def test_request_words_inside_other_phrases_do_not_count(self):
        for text in ("그때 부탁해서 받은 자료입니다", "알려주셔서 감사해요 대리님"):
            self.assertNotIn("부탁 표현 +3", score_message(text).reasons)
