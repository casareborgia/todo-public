"""쪽지 업무 점수 판단 (EzQ 쪽지 경로 전용, 초안).

업무 단어 하나만 있어도 업무로 보던 `is_actionable_text()` 대신, 근거마다 점수를 매겨 합으로 판정합니다.
- 가점: 부탁 표현, 명시 기한, 기한과 부탁·업무가 같은 줄, 업무 명사
- 감점: 여러 사람 대상(여러분·전 직원), 참여 독려, 공지·공유 표현, 회의 내용 전달, 완료 보고, 선택형
- 3점 이상 등록 / 1~2점 확인 필요 / 0점 이하 제외

같은 근거는 여러 번 나와도 한 번만 셉니다. 판정 근거(reasons)는 업무 설명에 남겨 사용자가 볼 수 있게 합니다.
점수표는 실사용 표본(저장소 밖)으로 맞추는 중이며 표본이 늘면 다시 조정합니다.
"""

import re
from dataclasses import dataclass, field
from typing import List

from parsers.portal_parser import REFERS_TO_QUOTE_RE
from parsers.rule_parser import PERIOD_DAY_END_TAIL, REPLY_LEFTOVER_RE, REPLY_PHRASE_RE

REGISTER = "등록"
REVIEW = "확인 필요"
EXCLUDE = "제외"

REGISTER_MIN = 3
REVIEW_MIN = 1

_SENT_END = r"(?=\s*(?:[.!,~^ㅎㅠ]|$))"  # 문장 끝 ('부탁해서'처럼 말 중간의 글자는 제외)
REQUEST_RE = re.compile(
    r"부탁\s*(?:드립니다|드려요|드리겠습니다|합니다|해요|드리며|드리오니)"
    r"|요청\s*(?:드립니다|드려요|드리오니|드리며|합니다)"
    # 사내 쪽지에서 흔한 짧은 말투 ('검토 부탁드림', '취합 요청드림', '자료 좀 부탁해')
    + r"|(?:부탁|요청)\s*드림" + _SENT_END + r"|부탁\s*해" + _SENT_END
    # 질문형 요청 ('보내주실 수 있을까요?', '회신 부탁드려도 될까요?'). 기한이 없으면 확인 필요로 감
    + r"|주실\s*수\s*있|(?:부탁|요청)\s*드려도\s*(?:될|되)"
    r"|바랍니다|요망"
    r"|(?:주|줘)(?:세요|십시오|시기)"
    # 문장 끝 명령형만 ('보고할 것.', '제출할것(테스트)', '회신 바람'). '할 것 같아요'·'할 것입니다'·'바람 불어서'는 제외
    r"|(?:할|해야\s*할|해줄|해\s*줄)\s*것(?=\s*(?:[.!,~)(\[\]]|$))"
    r"|바람(?=\s*(?:[.!,~]|$))",
    re.MULTILINE,
)
# 공문의 통보형 맺음말 ('참고하여 주시기 바랍니다', '유의하시기 바랍니다'). 할 일을 부탁하는 말이 아니라서
# 부탁 표현에서 빼고 공지·공유로 감점함 (실제 공문 표본 2026-11-06)
NOTICE_ASK_RE = re.compile(r"(?:참고|유의|숙지)\s*(?:(?:하여|해|하시기)\s*)?(?:주시기\s*)?바랍니다")


def has_request(text: str) -> bool:
    """부탁 표현이 있는지 (통보형 맺음말은 부탁 표현으로 치지 않음)."""
    return bool(REQUEST_RE.search(NOTICE_ASK_RE.sub(" ", text)))


_DATE = r"\d{1,2}\s*+[월/.]\s*+\d{1,2}\s*+일?\.?"
# 날짜·요일과 '까지' 사이의 시각: '오전', '오후 3시', '오전 10시 30분', '3시 반', '14:00'
_HOUR = r"\d{1,2}\s*+시(?:\s*+(?:\d{1,2}\s*+분|반))?"
_TOD = r"(?:(?:(?:오전|오후|낮|저녁)\s*+(?:" + _HOUR + r")?|" + _HOUR + r"|\d{1,2}:\d{2})\s*+)?"
# 기간 표현도 명시 기한 ('~ 2026.11.20', '11.10.-11.14.', '11/10~14'). '-'는 앞뒤 모두 날짜일 때만 (문서번호 '감사실-123' 제외)
PERIOD_RE = (
    r"~\s*+(?:20)?\d{2}\s*+\.\s*+\d{1,2}\s*+\.\s*+\d{1,2}"
    + r"|" + _DATE + r"\s*+(?:\([월화수목금토일]\))?\s*+-\s*+" + _DATE
    # 끝이 일만: 끝 일 뒤 경계는 규칙 파서와 같음 ('3~4명', '14~16시', '~3쪽'은 기한 아님)
    + r"|" + _DATE + r"\s*+~\s*+\d{1,2}" + PERIOD_DAY_END_TAIL
)
DEADLINE_RE = re.compile(
    _DATE + r"\s*+(?:\([월화수목금토일]\))?\s*+" + _TOD + r"(?:까지|내로|이내)"
    + r"|(?:[월화수목금토일]요일|\d{1,2}일)\s*+" + _TOD + r"(?:까지|내로)"
    + r"|~\s*+'?(?:\d{2}\.\s*+)?" + _DATE
    + r"|기한|마감|(?:오늘|금일|내일|모레|금주|이번\s*+주|다음\s*+주|차주)\s*+" + _TOD + r"(?:까지|중)"
    + r"|" + PERIOD_RE
)
# 날짜처럼 보이는 말 (기한 표현은 아님). 부탁 표현 없는 짧은 지시('회의자료 제출 11/5')를 살리는 데만 씀
LOOSE_DATE_RE = re.compile(_DATE + r"|오늘|금일|내일|모레|[월화수목금]요일")
TASK_NOUN_RE = re.compile(r"제출|회신|작성|취합|검토|처리|보고(?!\s*싶)|결재|기안|송부|입력|등록|이수|정산")

PENALTIES = [
    ("여러 사람 대상", re.compile(r"여러분|직원\s*분들|전\s*직원|임직원|조합원"), -3),
    ("참여 독려", re.compile(r"(?:참여|참석|관심|동참|응모)\s*(?:를|을)?\s*(?:부탁|바랍니다|해\s*주)"), -2),
    ("공지·공유", re.compile(
        r"\[[^\]\n]*(?:공지|안내|알림|속보|소식)[^\]\n]*\]|알려\s*드립니다|공유\s*(?:드립니다|합니다)"
        r"|전달\s*드립니다|속보|소식지|참고\s*(?:하시기\s*)?바랍니다|참고하세요"
        # 공문의 통보형 표현 ('안내해드립니다', '참고하여 주시기 바랍니다')
        r"|안내\s*(?:해\s*)?드립니다|" + NOTICE_ASK_RE.pattern
    ), -3),
    ("회의 내용 전달", re.compile(r"회의\s*(?:내용|결과)|회의록|지시\s*사항|전달\s*사항"), -2),
    ("완료 보고", re.compile(r"(?:했|하였|올렸|보내\s*드렸|첨부했|드렸|마쳤)습니다|요청하신"), -2),
    ("선택형", re.compile(r"필요하시면|원하시면|희망하시면"), -1),
]

# 알리면서 다음 말로 잇는 공문 표현 ('…을 알려드리오니', '안내드리며'). 뒤에 부탁이 붙어도 대개 통보라 공지·공유로 감점.
# 다만 명시 기한이 있으면(공모 '접수기간 ~11.20') 기한 안에 할 일을 알리는 것이라 감점하지 않음
NOTICE_LEAD_RE = re.compile(r"알려\s*드리(?:오니|니|며)|안내\s*(?:해\s*)?드리(?:오니|니|며)")

# 시스템이 보내는 알림 쪽지. 문서·메일 제목에 업무 단어('결재·공람·제출 요청')가 섞여도 업무가 아님
# (실제 공문은 PDF 경로로 따로 들어옴). 실사용 표본 2026-11-03:
# - 결재알림: '결재 알림메시지 입니다'
# - 메일알림: '…외부메일에서 수신된 메일알림입니다.' + '"기능호출:클릭하시면 메일화면으로 이동합니다."'
# - 통합알림: 첫 줄 '[ 펌뱅킹지급 알림 ]' + '"기능호출: 여기를 클릭하시면 관련화면으로 이동합니다."'
SYSTEM_ALERT_RE = re.compile(
    r"알림\s*메시지\s*입니다|메일\s*알림\s*입니다|기능\s*호출\s*[:：]\s*(?:여기를\s*)?클릭"
)
# 첫 줄 '[ … 알림 ]'은 '보낸사람 :' 줄이 함께 있을 때만 시스템 알림 (사람이 쓴 '[교육 이수 알림]' 제목은 업무일 수 있음)
BRACKET_ALERT_TITLE_RE = re.compile(r"\A\s*\[\s*[^\[\]\n]*알림\s*\]\s*$", re.MULTILINE)
ALERT_SENDER_LINE_RE = re.compile(r"^\s*보낸\s*사람\s*[:：]", re.MULTILINE)


def is_system_alert(text: str) -> bool:
    return bool(SYSTEM_ALERT_RE.search(text)) or bool(
        BRACKET_ALERT_TITLE_RE.search(text) and ALERT_SENDER_LINE_RE.search(text)
    )


# 업무로 확정할 근거 (기한이 없을 때): 첨부 양식·공문. 없으면 요청이 있어도 '확인 필요'
# ('관련 내용 공유해주세요~', '확인 부탁드립니다'처럼 할 일로 보기 어려운 가벼운 요청이 등록되던 문제)
MATERIAL_RE = re.compile(r"첨부\s*파일\s*[:：]|붙임|공문")

# 인용 글에서 그대로 옮겨 붙인 줄로 볼 최소 길이 ('감사합니다.' 같은 흔한 짧은 줄은 그대로 둠)
COPIED_LINE_MIN = 10


def _squash(line: str) -> str:
    return re.sub(r"\s+", "", line)


def strip_copied_lines(top: str, quoted_bodies: List[str]) -> str:
    """맨 위 글에서 인용된 글과 똑같은 줄을 뺌 (내가 보낸 문장을 옮겨 붙이고 질문하는 답장 대비)."""
    quoted = {_squash(ln) for body in quoted_bodies for ln in body.splitlines() if len(_squash(ln)) >= COPIED_LINE_MIN}
    if not quoted:
        return top
    return "\n".join(ln for ln in top.splitlines() if _squash(ln) not in quoted)


@dataclass
class ScoreResult:
    score: int = 0
    verdict: str = EXCLUDE
    reasons: List[str] = field(default_factory=list)


def _is_reply_only(text: str) -> bool:
    return bool(REPLY_LEFTOVER_RE.fullmatch(REPLY_PHRASE_RE.sub("", text)))


def has_deadline_task_line(text: str) -> bool:
    """기한과 부탁·업무 명사가 같은 줄에 있는지 ('검토는 11.7.(금)까지')."""
    return any(DEADLINE_RE.search(line) and (has_request(line) or TASK_NOUN_RE.search(line))
               for line in text.splitlines())


def score_message(top: str, quoted: str = "") -> ScoreResult:
    """맨 위 받은 글(top)을 점수로 판정. 인용된 글을 가리키면 첫 인용 글(quoted)도 함께 봄."""
    top = (top or "").strip()
    result = ScoreResult()
    if len(top) < 5 or _is_reply_only(top):
        result.reasons.append("받았다는 답장·짧은 글")
        return result
    if is_system_alert(top):
        result.reasons.append("시스템 알림 쪽지 (결재·메일·통합 알림)")
        return result

    text = top
    if quoted and REFERS_TO_QUOTE_RE.search(top):
        text = f"{top}\n{quoted.strip()}"
        result.reasons.append("인용된 글을 가리킴 → 인용 글 함께 판단")

    def add(name: str, points: int):
        result.score += points
        result.reasons.append(f"{name} {points:+d}")

    if has_request(text):
        add("부탁 표현", 3)
    if DEADLINE_RE.search(text):
        add("명시 기한", 2)
    if has_deadline_task_line(text):
        add("기한과 부탁·업무가 같은 줄", 3)
    if TASK_NOUN_RE.search(text):
        add("업무 명사", 1)
    # 맨 위 글이 직접 요청하면('아래 내용 처리 좀 해주세요') 인용 글은 가점 근거로만 쓰고 감점은 맨 위 글로만 판단.
    # 요청 없이 전달만 하면('아래 공지 전달드립니다') 인용된 공지의 감점도 적용
    penalty_text = top if has_request(top) else text
    for name, pattern, points in PENALTIES:
        if pattern.search(penalty_text):
            add(name, points)
    if "공지·공유 -3" not in result.reasons and NOTICE_LEAD_RE.search(penalty_text) and not DEADLINE_RE.search(text):
        add("공지·공유", -3)

    if result.score >= REGISTER_MIN:
        if DEADLINE_RE.search(text) or MATERIAL_RE.search(text):
            result.verdict = REGISTER
        else:
            result.verdict = REVIEW
            result.reasons.append("기한·첨부·공문 없는 요청 → 확인 필요")
    elif result.score >= REVIEW_MIN:
        # 업무 명사 하나뿐인 잡담('검토해보니 괜찮네요')은 제외. 부탁·기한·날짜 중 하나는 있어야 확인 필요
        if has_request(text) or DEADLINE_RE.search(text) or LOOSE_DATE_RE.search(text):
            result.verdict = REVIEW
        else:
            result.reasons.append("업무 명사만 있음 → 제외")
    return result
