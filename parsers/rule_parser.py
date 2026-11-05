"""한국어 날짜·기한·업무지시 규칙 기반(Rule-based) 파서 (강조사항 4-3).

외부 AI API 호출 없이 정규표현식과 한국어 업무 패턴 규칙만으로:
1) 마감 기한(절대 날짜, 상대 날짜, 요일, 시간) 추출 (작성일/발신일과 마감기한 구분)
2) 문서번호/발신자/출처(사내포털 문서 vs 사내메신저) 자동 판별
3) 긴급도 및 색상 라벨 자동 분류
4) 핵심 업무 제목 정제
를 수행합니다.
"""

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.database import compute_content_hash
from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus
from parsers.base_parser import BaseTaskExtractor

WEEKDAY_MAP = {
    "월": 0,
    "화": 1,
    "수": 2,
    "목": 3,
    "금": 4,
    "토": 5,
    "일": 6,
}

JOB_TITLES = r"(?:주무관|사무관|서기관|대리|과장|차장|부장|팀장|실장|국장|계장|선임|책임|연구위원)"

# 메신저 대화 헤더 줄 (예: '[홍길동 과장] 오전 10:15', '[홍길동] 2026-10-28 09:30')
# 메시지를 보낸 시각이므로 마감 시각·날짜 추출 대상에서 제외해야 함
CHAT_HEADER_RE = re.compile(
    r"^[ \t]*\[([^\]\n]{1,20})\][ \t]*(?:(\d{4})[-./](\d{1,2})[-./](\d{1,2})[ \t]*)?"
    r"((?:(?:오전|오후)[ \t]*)?\d{1,2}:\d{2}(?::\d{2})?)[ \t]*$",
    re.MULTILINE,
)

# 문서번호 형태 (예: '감사실-1204', '정보보호팀-3321')와, 공문에서 문서번호가 나오는 위치
DOC_NO_TOKEN = r"[가-힣A-Za-z0-9·]+-\d+"
DOC_NO_CONTEXT_RE = re.compile(
    r"(?:문서번호|접수번호|공문번호|시행|접수)\s*[:：]?\s*(" + DOC_NO_TOKEN + r")"
)

# 다른 문서를 인용한 문서번호: 번호 뒤에 (날짜)가 오고 「제목」·『제목』·'관련'이 바로 이어짐
# (예: '문서번호 감사실-123(2026.10.30.) 「…계획」관련하여', '관련: 시행 감사실-77호 관련')
# 이런 번호를 이 문서의 번호로 쓰면 예전 공문 업무와 잘못 합쳐짐 (기한만 바뀌고 새 공문은 등록 안 됨)
CITED_AFTER_RE = re.compile(r"\s*(?:호\s*)?(?:\([^)\n]*\)\s*)?(?:[「『\"“]|관련)")
CITED_LINE_RE = re.compile(r"^\s*(?:\d+\.\s*)?관련\s*[:：]?")


def is_cited_doc_no(text: str, start: int, end: int) -> bool:
    """text[start:end]의 문서번호가 다른 문서를 인용한 것인지 (같은 줄 기준)."""
    line_start = text.rfind("\n", 0, start) + 1
    if CITED_LINE_RE.match(text, line_start):
        return True
    return bool(CITED_AFTER_RE.match(text, end))


# 마감일 후보에서 제외할 줄: 작성·접수·시행 등 문서 이력 날짜와 '관련' 문서 인용
NON_DEADLINE_LINE_RE = re.compile(
    r"(작성일|발신일|수신일|시행일|등록일|접수일|기안일|결재일|생산일|배부일|발송일)"
    r"|^\s*(?:\d+\.\s*)?(?:관련|시행|접수)(?:\s|[:：])"
)

# 같은 줄에 본문이 이어지는 발신자 표기 (예: '[김철수 팀장] 감사 자료 보고 바랍니다')
INLINE_CHAT_SENDER_RE = re.compile(r"^\s*\[[가-힣]{2,4}\s*" + JOB_TITLES + r"\]")

# 제목 앞의 호칭 (예: '주무관님, ', '팀장님 ', '김대리, ')
ADDRESS_PREFIX_RE = re.compile(
    r"^(?:[가-힣]{1,4}\s*)?" + JOB_TITLES + r"(?:님(?:\s*[,，]|\s+)|\s*[,，])\s*"
)

# '이름: 내용' 형태의 발신자 추정에서 제외할 항목 이름 (메시지·공문 서식의 머리말)
NOT_SENDER_LABELS = {
    "제목", "건명", "기한", "마감", "내용", "일시", "붙임", "첨부", "첨부파일", "수신", "수신자", "참조",
    "경유", "담당", "담당자", "비고", "주소", "전화", "송신", "시간", "작성요령", "주의사항", "회신기한",
}

# 업무 내용이 없는 인사·도입 문장 (예: '두 가지 부탁드려요.', '안녕하세요.')
LEAD_IN_RE = re.compile(
    r"^(?:안녕하(?:세요|십니까)|수고\s*(?:많으십니다|많습니다|하십니다)|다름이?\s*아니라"
    r"|(?:한|두|세|네|몇)\s*가지\s*(?:부탁|요청)\s*(?:드려요|드립니다|합니다|해요))[.!~,]*\s*"
)

# 보내는 사람 자기소개 (예: '기획팀 홍길동입니다.', '총무팀 김철수 과장입니다.')
# '긴급 요청입니다'처럼 '입니다'로 끝나는 업무 문장과 구분하려고, 직급이 없으면 세 글자 이름만 인정
ORG_UNIT = r"[가-힣A-Za-z0-9·]*(?:팀|과|실|부|처|국|센터|본부|사업소|지사|사업단|위원회|파트|그룹)"
SELF_INTRO_RE = re.compile(
    r"^(?:" + ORG_UNIT + r"\s+){0,2}(?:([가-힣]{2,4})\s*" + JOB_TITLES + r"|([가-힣]{3}))\s*입니다[.!~,]*\s*"
)

# 업무 없이 받았다는 뜻만 담은 답장을 이루는 말 (예: '넵 확인했습니다 감사합니다')
REPLY_PHRASE_RE = re.compile(
    r"(?:잘\s*)?(?:확인|숙지|접수|수신)\s*(?:완료\s*)?(?:했|하였|됐|되었|완료)(?:습니다|어요|습니당|음)?"
    r"|(?:잘\s*)?(?:전달\s*)?받았(?:습니다|어요)"
    r"|알겠(?:습니다|어요|습니당)|알았(?:습니다|어요)"
    r"|감사(?:합니다|드립니다|해요|합니당)|고맙(?:습니다|어요)"
    r"|수고\s*(?:하세요|하십시오|하셨습니다|많으셨습니다|많으십니다)|좋은\s*하루\s*(?:되세요|보내세요)"
    r"|안녕하(?:세요|십니까)|네네|넵|넹|옙|네|예|ㅇㅋ|오케이|ok"
    r"|^\s*(?:발신자|보낸\s*사람|송신)\s*[:：].*$",
    re.IGNORECASE | re.MULTILINE,
)
REPLY_LEFTOVER_RE = re.compile(r"[\s.,!?~^ㅎㅋㅠㅜ:;)(…·*-]*")


class RuleBasedExtractor(BaseTaskExtractor):
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        kw_cfg = self.config.get("keywords", {})
        self.task_triggers: List[str] = kw_cfg.get(
            "task_triggers",
            [
                "배부", "협조", "결재", "회신", "제출", "보고", "검토", "요청",
                "작성", "취합", "확인", "기안", "공람", "시행", "처리", "안내",
                "점검", "마감", "필수", "부탁", "바랍니다", "주시기",
            ],
        )
        self.urgent_triggers: List[str] = kw_cfg.get(
            "urgent_triggers",
            ["긴급", "즉시", "조속히", "금일중", "오늘까지", "최우선", "필독", "독촉", "지연"],
        )
        self.portal_indicators: List[str] = kw_cfg.get(
            "portal_indicators",
            ["문서번호", "접수번호", "기안자", "발신부서", "수신자", "공문", "업무관리", "온나라", "그룹웨어", "[배부]", "[공문]"],
        )
        self.messenger_indicators: List[str] = kw_cfg.get(
            "messenger_indicators",
            ["메신저", "쪽지", "대화", "님:", "보낸사람", "[쪽지]", "[대화]"],
        )
        self.default_days = int(self.config.get("default_deadline_days_if_missing", 3))
        self.default_time = str(self.config.get("default_deadline_time", "18:00"))
        # 기본 기한 계산 시 건너뛸 공휴일 목록 (YYYY-MM-DD)
        self.holidays = set(self.config.get("holidays", []))

    @staticmethod
    def split_chat_header(text: str) -> Tuple[Optional["re.Match"], str]:
        """메신저 대화 헤더 줄을 떼어내고 (첫 헤더 매치, 본문)을 반환."""
        header = CHAT_HEADER_RE.search(text)
        if not header:
            return None, text
        return header, CHAT_HEADER_RE.sub("", text).strip()

    @staticmethod
    def normalize_doc_no(doc_no: str) -> str:
        return re.sub(r"\s+", "", doc_no or "")

    def find_doc_numbers(self, text: str) -> List[str]:
        """문서번호·시행·접수 위치에 나오는 모든 문서번호 (공문 PDF와 목록 CSV 매칭용)."""
        found: List[str] = []
        for m in DOC_NO_CONTEXT_RE.finditer(text):
            if is_cited_doc_no(text, m.start(1), m.end(1)):
                continue  # 다른 문서를 인용한 번호
            no = self.normalize_doc_no(m.group(1))
            if no not in found:
                found.append(no)
        return found

    def detect_source(self, text: str, source_hint: str = "AUTO") -> str:
        if source_hint in (TaskSource.PORTAL, TaskSource.MESSENGER, TaskSource.CLIPBOARD, TaskSource.MANUAL):
            return source_hint

        if CHAT_HEADER_RE.search(text) or INLINE_CHAT_SENDER_RE.search(text):
            return TaskSource.MESSENGER

        portal_score = sum(1 for k in self.portal_indicators if k in text)
        msg_score = sum(1 for k in self.messenger_indicators if k in text)
        if portal_score >= msg_score and portal_score > 0:
            return TaskSource.PORTAL
        if msg_score > 0:
            return TaskSource.MESSENGER
        return TaskSource.PORTAL

    def extract_metadata(self, text: str) -> Dict[str, str]:
        """문서번호, 발신부서/보낸사람 등 메타데이터 추출."""
        meta: Dict[str, str] = {}
        doc_match = None
        for m in re.finditer(r"(?:문서번호|접수번호|공문번호|과제번호)\s*[:：]\s*([^\r\n,;|]+)", text):
            num = re.match(DOC_NO_TOKEN, m.group(1))
            if num and is_cited_doc_no(text, m.start(1), m.start(1) + num.end()):
                continue  # 다른 문서를 인용한 번호 (예: '문서번호: 감사실-123(…) 「…」 관련')
            doc_match = m
            break
        if doc_match:
            meta["doc_no"] = doc_match.group(1).strip()
        else:
            # 공문 하단 '시행 감사실-1204 (2026. 10. 27.)' 형식
            enforce = re.search(r"^\s*시행\s*(" + DOC_NO_TOKEN + r")", text, re.MULTILINE)
            if enforce:
                meta["doc_no"] = enforce.group(1)

        sender_match = re.search(
            r"(?:발신부서|담당부서|기안자|보낸사람|발신자|작성자)\s*[:：]\s*([^\r\n,;|]+)", text
        )
        if sender_match:
            meta["sender"] = sender_match.group(1).strip()
        else:
            chat_sender = re.search(r"^\s*\[?([가-힣a-zA-Z0-9_]+\s*(?:대리|과장|차장|부장|팀장|주무관|사무관|선임|책임|연구위원|님)?)\]?\s*[:：]", text, re.MULTILINE)
            if chat_sender and chat_sender.group(1).strip() not in NOT_SENDER_LABELS:
                meta["sender"] = chat_sender.group(1).strip()
            elif meta.get("doc_no") and re.search(r"^\s*시행\s*" + re.escape(meta["doc_no"]), text, re.MULTILINE):
                # 공문: 시행번호 '재무개선실-3133'의 부서가 발신 부서
                meta["sender"] = meta["doc_no"].rsplit("-", 1)[0]

        return meta

    def extract_due_date_and_time(
        self, text: str, ref_date: Optional[date] = None
    ) -> Tuple[str, str]:
        due_date, due_time, _ = self.extract_due(text, ref_date)
        return due_date, due_time

    def extract_due(
        self, text: str, ref_date: Optional[date] = None
    ) -> Tuple[str, str, bool]:
        """텍스트에서 (마감일 YYYY-MM-DD, 마감시각 HH:MM, 기한 추정 여부)를 추출.

        '마감/기한/까지/회신/제출' 주변의 날짜를 최우선으로 채택하고,
        작성·접수·시행일 같은 문서 이력 날짜와 '관련' 문서 인용 날짜는 마감일로 쓰지 않습니다.
        기한 표현이 없으면 기본 N영업일 후로 추정하고 추정 여부를 True로 반환합니다.
        """
        today = ref_date or date.today()

        deadline_lines = []
        other_lines = []
        for line in text.splitlines():
            line_s = line.strip()
            if not line_s or NON_DEADLINE_LINE_RE.search(line_s):
                continue
            if any(
                k in line_s
                # '限'은 공문에서 '까지'의 뜻 (예: '명단 송부(11.2(월) 限)')
                for k in ("기한", "마감", "까지", "限", "회신", "제출", "완료일", "처리기한", "기일", "목표일")
            ):
                deadline_lines.append(line_s)
            else:
                other_lines.append(line_s)

        for target_chunk in ["\n".join(deadline_lines), "\n".join(other_lines)]:
            if not target_chunk:
                continue
            parsed_date = self._parse_date_from_chunk(target_chunk, today)
            if parsed_date:
                return parsed_date.strftime("%Y-%m-%d"), self._extract_time(target_chunk), False

        # 기한이 명시되지 않은 업무 지시문인 경우 기본 N영업일 후로 설정 (주말·공휴일 제외)
        fallback_date = self._add_business_days(today, self.default_days)
        return fallback_date.strftime("%Y-%m-%d"), self._extract_time("\n".join(deadline_lines + other_lines)), True

    def _add_business_days(self, start: date, days: int) -> date:
        current = start
        added = 0
        while added < days:
            current += timedelta(days=1)
            if current.weekday() < 5 and current.isoformat() not in self.holidays:
                added += 1
        return current

    def _parse_date_from_chunk(self, chunk: str, today: date) -> Optional[date]:
        # A. 범위 표현 (~ 2026.11.15 또는 ~ 11/15 까지)에서 뒷날짜(마감일) 우선 추출
        range_ymd = re.search(
            r"[~\-∼]\s*(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})",
            chunk,
        )
        if range_ymd:
            d = self._safe_date(int(range_ymd.group(1)), int(range_ymd.group(2)), int(range_ymd.group(3)))
            if d:
                return d

        range_md = re.search(
            r"[~\-∼]\s*(\d{1,2})\s*[./월]\s*(\d{1,2})\s*(?:일)?",
            chunk,
        )
        if range_md:
            d = self._resolve_month_day(today, int(range_md.group(1)), int(range_md.group(2)))
            if d:
                return d

        # B. 'YYYY-MM-DD / YYYY.MM.DD / YYYY년 MM월 DD일', 공문식 줄임 연도 ''26.11.4' + '까지/마감/기한'
        full_date_matches = list(
            re.finditer(
                r"(20\d{2}|['‘’`]\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*(?:일)?",
                chunk,
            )
        )
        if full_date_matches:
            # 여러 날짜가 있으면 가장 마지막(마감일일 확률이 높음) 날짜 선택
            m = full_date_matches[-1]
            year = m.group(1)
            year = 2000 + int(year[1:]) if not year[0].isdigit() else int(year)
            d = self._safe_date(year, int(m.group(2)), int(m.group(3)))
            if d:
                return d

        # C. 'MM월 DD일' 또는 'MM/DD', 'MM.DD' (단, 버전 번호나 문서 호수 오인 방지)
        md_matches = list(
            re.finditer(
                r"(?<![0-9.])(\d{1,2})\s*(?:월|[/.])\s*(\d{1,2})\s*(?:일)?(?:\s*\([월화수목금토일]\))?(?=\s*(?:까지|마감|한|내|오전|오후|\d{1,2}시|\d{1,2}:\d{2}|\([월화수목금토일]\)|$|[,\s]))",
                chunk,
            )
        )
        if md_matches:
            m = md_matches[-1]
            d = self._resolve_month_day(today, int(m.group(1)), int(m.group(2)))
            if d:
                return d

        # D. 상대 날짜 표현 (오늘, 내일, 모레, 금주 금요일, 차주 월요일, N일 내 등)
        if re.search(r"(오늘|금일|당일)\s*(?:중|까지|마감|내|안으로)?", chunk):
            return today
        if re.search(r"(내일|명일|익일)\s*(?:중|까지|마감|오전|오후)?", chunk):
            return today + timedelta(days=1)
        if re.search(r"(모레|내일모레)", chunk):
            return today + timedelta(days=2)

        in_days = re.search(r"(\d{1,2})\s*일\s*(?:이내|내로|내|안으로|뒤|후)", chunk)
        if in_days:
            return today + timedelta(days=int(in_days.group(1)))

        # 다음 달 / 이번 달 N일
        month_day = re.search(r"(다음\s*달|내달|익월|차월|이번\s*달|금월|당월)\s*(\d{1,2})\s*일", chunk)
        if month_day:
            is_next = not re.match(r"(이번|금월|당월)", month_day.group(1))
            year, month = today.year, today.month
            if is_next:
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            day = min(int(month_day.group(2)), calendar.monthrange(year, month)[1])
            return date(year, month, day)

        # 이번 주 내로 / 다음 주 중 → 해당 주 금요일
        week_within = re.search(r"(이번\s*주|금주|다음\s*주|차주|내주)\s*(?:내|중|안)", chunk)
        if week_within:
            this_friday = today + timedelta(days=4 - today.weekday())
            if re.match(r"(이번|금주)", week_within.group(1)):
                return max(today, this_friday)
            return this_friday + timedelta(days=7)

        # 이번주 / 금주 X요일
        this_week = re.search(r"(?:이번\s*주|금주)\s*([월화수목금토일])요일", chunk)
        if this_week:
            target_wd = WEEKDAY_MAP[this_week.group(1)]
            diff = target_wd - today.weekday()
            if diff < 0:
                diff += 7
            return today + timedelta(days=diff)

        # 다음주 / 차주 / 내주 X요일
        next_week = re.search(r"(?:다음\s*주|차주|내주)\s*([월화수목금토일])요일", chunk)
        if next_week:
            target_wd = WEEKDAY_MAP[next_week.group(1)]
            days_until_next_monday = 7 - today.weekday()
            return today + timedelta(days=days_until_next_monday + target_wd)

        # 단독 'X요일까지' (사이에 시각이 끼는 'X요일 오전 11시까지'도 포함)
        weekday_until = re.search(
            r"([월화수목금토일])요일\s*(?:(?:오전|오후)\s*)?"
            r"(?:\d{1,2}\s*(?:시(?:\s*반|\s*\d{1,2}\s*분)?|:\d{2})\s*)?까지",
            chunk,
        )
        if weekday_until:
            target_wd = WEEKDAY_MAP[weekday_until.group(1)]
            diff = (target_wd - today.weekday()) % 7
            return today + timedelta(days=diff)

        # 이번달 말 / 금월 말일
        if re.search(r"(?:이번\s*달|금월|당월)\s*(?:말일|말까지|말)", chunk):
            last_day = calendar.monthrange(today.year, today.month)[1]
            return date(today.year, today.month, last_day)

        return None

    def _extract_time(self, text: str) -> str:
        # 1) 오전/오후 HH시 (MM분|반) or HH:MM
        ampm_match = re.search(
            r"(오전|오후)\s*(\d{1,2})\s*(?:시\s*(?:(\d{1,2})\s*분|(반))?|:(\d{2}))", text
        )
        if ampm_match:
            ampm, hh_s = ampm_match.group(1), ampm_match.group(2)
            mm_s = ampm_match.group(3) or ampm_match.group(5)
            hh = int(hh_s)
            mm = 30 if ampm_match.group(4) else (int(mm_s) if mm_s else 0)
            if ampm == "오후" and hh < 12:
                hh += 12
            elif ampm == "오전" and hh == 12:
                hh = 0
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"

        # 2) 범위 끝 날짜 뒤의 시각 (예: '(~ 11.04 15:00)', '~11/4(화) 15:00')
        range_end_time = re.search(
            r"[~∼]\s*(?:(?:20\d{2}|['‘’`]\d{2})\s*[.\-/년]\s*)?\d{1,2}\s*[./월]\s*\d{1,2}\s*(?:일)?"
            r"\s*(?:\([월화수목금토일]\)\s*)?(\d{1,2}):(\d{2})",
            text,
        )
        if range_end_time:
            hh, mm = int(range_end_time.group(1)), int(range_end_time.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"

        # 2-1) 'HH:MM까지' 처럼 시각 뒤에 기한 표현이 오는 경우
        hm_until = re.search(r"(?<![\d:])(\d{1,2}):(\d{2})\s*(?:까지|마감|전까지)", text)
        if hm_until:
            hh, mm = int(hm_until.group(1)), int(hm_until.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"

        # 3) 기한/마감 줄의 HH:MM 또는 HH시
        hm_match = re.search(
            r"(?:기한|마감|까지|일시).*?(\d{1,2}):(\d{2})", text
        )
        if hm_match:
            hh, mm = int(hm_match.group(1)), int(hm_match.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"

        hour_only = re.search(r"(\d{1,2})\s*시\s*(반)?\s*(?:까지|마감|전까지)", text)
        if hour_only:
            hh = int(hour_only.group(1))
            mm = 30 if hour_only.group(2) else 0
            if 1 <= hh <= 7:
                hh += 12  # 업무시간 기준 1시~7시까지라고 하면 오후(13~19시)로 보정
            if 0 <= hh <= 23:
                return f"{hh:02d}:{mm:02d}"

        # 4) 시각 없이 '오전까지 / 오전 중 / 점심 전' → 정오
        if re.search(r"오전\s*(?:중|까지|내|안)|점심\s*(?:전|까지)", text):
            return "12:00"

        return self.default_time

    def _safe_date(self, y: int, m: int, d: int) -> Optional[date]:
        try:
            return date(y, m, d)
        except ValueError:
            return None

    def _resolve_month_day(self, today: date, m: int, d: int) -> Optional[date]:
        if not (1 <= m <= 12 and 1 <= d <= 31):
            return None
        candidate = self._safe_date(today.year, m, d)
        if not candidate:
            return None
        # 만약 오늘보다 60일 이상 과거 날짜라면 내년 날짜로 보정
        if (today - candidate).days > 60:
            next_yr = self._safe_date(today.year + 1, m, d)
            if next_yr:
                return next_yr
        return candidate

    def extract_title(self, text: str) -> str:
        """문서/메시지 본문에서 가장 대표적인 할 일 제목을 정제하여 추출."""
        # 1) 명시적 '제목:', '건명:', '업무명:', '과제명:' 라인 우선 탐색
        title_line = re.search(
            r"(?:제\s*목|건\s*명|업무명|과제명|문서명|안\s*건)\s*[:：]\s*(.+)",
            text,
        ) or re.search(r"^\s*제\s*목\s+(\S.*)", text, re.MULTILINE)  # 공문 PDF: '제목  OOO'
        if title_line:
            t = title_line.group(1).strip()
            if t:
                # 작성자가 직접 쓴 제목은 인사·호칭 제거를 하지 않음 ('다음주 부장님 휴가 일정'의 '부장님' 보존)
                return self._clean_title(t, strip_greeting=False)

        # 2) 대괄호 말머리가 포함된 첫 의미 있는 라인 탐색 (예: [배부] ..., [협조전] ...)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in lines:
            if any(
                ln.startswith(prefix)
                for prefix in ("문서번호", "접수번호", "발신", "수신", "기안", "일시", "날짜", "기한", "마감")
            ):
                continue
            # 대화형 메시지에서 '[홍길동 대리] 오전 10:15' 같은 헤더 줄은 건너뜀
            if re.match(r"^\[[^\]]+\]\s*(?:오전|오후)?\s*\d{1,2}:\d{2}$", ln):
                continue
            # 쪽지 첨부파일 줄 ('첨부파일:양식.hwp')은 제목이 아님. 첨부만 있는 쪽지는 맨 아래 대체 제목으로
            if re.match(r"^첨부\s*파일\s*[:：]", ln):
                continue
            # 인사·자기소개만 있는 줄 (예: '안녕하십니까', '기획팀 홍길동입니다.')은 건너뜀
            if len(self._strip_lead_in(ln)) < 3:
                continue
            cleaned = self._clean_title(ln)
            if len(cleaned) >= 3:
                return cleaned

        return self._clean_title(lines[0]) if lines else "스캔된 사내 업무"

    def _clean_title(self, raw_title: str, strip_greeting: bool = True) -> str:
        t = re.sub(r"\s+", " ", raw_title).strip()
        # 앞에 붙은 불필요한 글머리 기호 제거
        t = re.sub(r"^[-*•▶▷■□◆◇]\s*", "", t)
        # 제목 전체를 감싼 대괄호 (예: '[다음주 휴가 일정 작성 요청]') 제거. '[배부] 제목' 같은 말머리는 유지
        t = re.sub(r"^\[([^\[\]]+)\]$", r"\1", t).strip()
        # 업무 내용이 없는 인사·도입·자기소개 문장과 앞쪽 호칭 제거 (남는 내용이 있을 때만)
        if strip_greeting:
            stripped = self._strip_lead_in(t)
            if len(stripped) >= 3:
                t = stripped
            stripped = ADDRESS_PREFIX_RE.sub("", t, count=1)
            if len(stripped) >= 3:
                t = stripped
        # 대화 발신자 프리픽스 (예: '김철수 과장: ') 제거하되 핵심 내용은 유지
        t = re.sub(
            r"^[가-힣a-zA-Z0-9_]+\s*(?:대리|과장|차장|부장|팀장|주무관|사무관|선임|책임|님)\s*[:：]\s*",
            "",
            t,
        )
        if len(t) > 75:
            t = t[:72] + "..."
        return t

    def _strip_lead_in(self, text: str) -> str:
        """앞쪽의 인사·도입 문장과 자기소개를 모두 제거 (예: '안녕하세요. 기획팀 홍길동입니다. ...')."""
        t = text.strip()
        while True:
            stripped = LEAD_IN_RE.sub("", t, count=1)
            m = SELF_INTRO_RE.match(stripped)
            # 이름 자리에 업무 단어가 있으면 자기소개가 아님 (예: '보고서 요청입니다')
            if m and not any(k in (m.group(1) or m.group(2)) for k in self.task_triggers):
                stripped = stripped[m.end():]
            if stripped == t:
                return t
            t = stripped.strip()

    def is_reply_only(self, text: str) -> bool:
        """'확인했습니다', '넵 감사합니다'처럼 받았다는 뜻만 있는 답장인지 판별."""
        return bool(REPLY_LEFTOVER_RE.fullmatch(REPLY_PHRASE_RE.sub("", text)))

    def detect_priority_and_label(self, text: str, source: str) -> Tuple[int, str]:
        is_urgent = any(k in text for k in self.urgent_triggers)
        if is_urgent:
            return 1, "URGENT"
        if re.search(r"(매주|매월|매일|정기\s*보고|주간\s*보고|월간\s*보고)", text):
            return 2, "ROUTINE"
        if source == TaskSource.MESSENGER:
            return 2, "MESSENGER"
        if source in (TaskSource.PORTAL, TaskSource.CLIPBOARD):
            return 2, "PORTAL"
        return 2, "MANUAL"

    def detect_recurrence(self, text: str) -> str:
        if re.search(r"(매일|일일\s*점검|일일\s*보고)", text):
            return RecurrenceType.DAILY
        if re.search(r"(매주|주간\s*보고|주간\s*회의|주간\s*실적)", text):
            return RecurrenceType.WEEKLY
        if re.search(r"(매월|월간\s*보고|월례|월말\s*정산)", text):
            return RecurrenceType.MONTHLY
        return RecurrenceType.NONE

    def is_actionable_text(self, text: str) -> bool:
        """단순 인사말이나 무의미한 줄이 아닌 실제 배부 문서/업무 메시지인지 판별."""
        stripped = text.strip()
        if len(stripped) < 5:
            return False
        if self.is_reply_only(stripped):
            return False
        if any(k in stripped for k in self.task_triggers):
            return True
        if any(k in stripped for k in self.urgent_triggers):
            return True
        if any(k in stripped for k in self.portal_indicators):
            return True
        # 날짜/기한 패턴이 있으면 업무로 간주
        if re.search(r"(\d{1,2}\s*[월/.]\s*\d{1,2}|오늘|내일|모레|금주|차주|기한|마감|까지)", stripped):
            return True
        return False

    def extract_tasks(
        self,
        raw_text: str,
        source_hint: str = "AUTO",
        source_ref: str = "",
        ref_date: Optional[date] = None,
        already_judged: bool = False,
    ) -> List[TaskItem]:
        """already_judged=True: 호출한 쪽이 업무 여부를 이미 판정함 (EzQ 쪽지 점수 판단). 단어 판정 생략."""
        if not raw_text or not raw_text.strip():
            return []

        # 메신저 대화 헤더(발신자·발신 시각)는 본문과 분리해 마감 시각으로 오인하지 않게 함
        header, body = self.split_chat_header(raw_text)
        if not body or (not already_judged and not self.is_actionable_text(body)):
            return []

        external_key = ""
        if header:
            if header.group(2):
                # 헤더에 발신일이 있으면 '내일까지' 등 상대 날짜를 발신일 기준으로 계산
                sent = self._safe_date(int(header.group(2)), int(header.group(3)), int(header.group(4)))
                if sent:
                    ref_date = sent
                    external_key = f"MSG:{header.group(1).strip()}|{sent.isoformat()} {header.group(5)}"

        source = self.detect_source(raw_text, source_hint)
        meta = self.extract_metadata(body)
        if header and not meta.get("sender"):
            meta["sender"] = header.group(1).strip()
        title = self.extract_title(body)
        due_date, due_time, due_estimated = self.extract_due(body, ref_date=ref_date)
        priority, label = self.detect_priority_and_label(body, source)
        recurrence = self.detect_recurrence(body)

        doc_numbers = self.find_doc_numbers(body)
        if meta.get("doc_no"):
            doc_no = self.normalize_doc_no(meta["doc_no"])
            external_key = f"DOC:{doc_no}"
            if doc_no not in doc_numbers:
                doc_numbers.insert(0, doc_no)

        ref_parts = []
        if meta.get("doc_no"):
            ref_parts.append(f"문서번호: {meta['doc_no']}")
        if meta.get("sender"):
            ref_parts.append(f"발신: {meta['sender']}")
        if source_ref:
            ref_parts.append(source_ref)
        combined_ref = " | ".join(ref_parts)

        # 중복 판단에는 파일명(source_ref)을 넣지 않음: 같은 목록·대화를 다른 이름으로 다시 저장해도 같은 업무
        identity_ref = " | ".join(p for p in ref_parts if p != source_ref)
        content_hash = compute_content_hash(title, identity_ref, raw_text.strip())

        task = TaskItem(
            title=title,
            description=raw_text.strip(),
            source=source,
            source_ref=combined_ref,
            content_hash=content_hash,
            due_date=due_date,
            due_time=due_time,
            status=TaskStatus.PENDING,
            label=label,
            priority=priority,
            recurrence=recurrence,
            is_new_scan=True,
            external_key=external_key,
            due_estimated=due_estimated,
            match_keys=[f"DOC:{n}" for n in doc_numbers],
        )
        return [task]
