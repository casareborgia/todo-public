"""빠른 입력 한 줄을 업무 하나로 바꿈.

예) '내일 오후 3시까지 출장계획 제출' → 제목 '출장계획 제출', 기한 내일 15:00

날짜·시각은 기존 한국어 규칙 파서(RuleBasedExtractor.extract_due)로 계산하고,
제목에서는 그 날짜·시각 표현을 지워 할 일만 남깁니다.
문장에 날짜가 없으면 캘린더에서 선택한 날짜(default_date)를 기한으로 씁니다.
"""

import re
from datetime import date, datetime, timedelta
from typing import Optional

from core.database import compute_content_hash
from core.models import TaskItem, TaskSource, TaskStatus
from parsers.rule_parser import RuleBasedExtractor

_WEEKS = r"(?:이번\s*주|금주|다음\s*주|차주|다다음\s*주)"
# 숫자로 쓴 날짜: 실제로 날짜를 찾았을 때만 제목에서 지움 ('1.5배' 같은 숫자 보호)
_NUMERIC_DATE_PATTERNS = [
    r"(?:\d{4}|'\d{2})\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}\s*\.?",  # 2026-11-05, '26.11.5
    r"\d{1,2}\s*월\s*\d{1,2}\s*일",  # 11월 5일
    r"(?<![\d.])\d{1,2}\s*[/.]\s*\d{1,2}(?![\d])\.?",  # 11/5, 11.5
]
_WORD_DATE_PATTERNS = [
    r"\(\s*[월화수목금토일]\s*\)",  # (수)
    _WEEKS + r"?\s*[월화수목금토일]요일",  # 이번 주 금요일, 금요일
    _WEEKS,
    r"[Dd]\s*-\s*\d+",  # D-3
    r"\d+\s*일\s*(?:뒤|후)",  # 3일 뒤
    r"오늘|금일|내일|명일|모레|글피",
]
_TIME_PATTERNS = [
    r"(?:오전|오후)\s*\d{1,2}\s*(?:시\s*(?:\d{1,2}\s*분|반)?|:\s*\d{2})",
    r"(?<![\d:])\d{1,2}\s*:\s*\d{2}",
    r"\d{1,2}\s*시\s*(?:\d{1,2}\s*분|반)?",
    r"오전|오후|점심\s*전",
]


def _alt(patterns) -> "re.Pattern":
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_STRIP_ALL_RE = _alt(_TIME_PATTERNS + _NUMERIC_DATE_PATTERNS + _WORD_DATE_PATTERNS)
_STRIP_WORDS_RE = _alt(_TIME_PATTERNS + _WORD_DATE_PATTERNS)
# 날짜를 지운 뒤 홀로 남은 '까지', '중' 같은 꼬리말
_LEFTOVER_RE = re.compile(r"(?:^|(?<=\s))(?:전까지|까지|限|중으로|중에|중|내로|안으로|안에|에)(?=\s|$|[,.])")
_WEEKDAY_IDX = {c: i for i, c in enumerate("월화수목금토일")}


def strip_date_words(text: str, numeric_dates: bool = True) -> str:
    """제목용: 날짜·시각 표현과 그 뒤에 남는 '까지' 등을 지움."""
    t = (_STRIP_ALL_RE if numeric_dates else _STRIP_WORDS_RE).sub(" ", text)
    t = _LEFTOVER_RE.sub(" ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ,.-·")


def _fallback_date(line: str, today: date) -> Optional[date]:
    """기존 파서가 기한 문맥('까지' 등) 없이 놓치는 짧은 표현: D-3, 3일 뒤, 금요일."""
    m = re.search(r"[Dd]\s*-\s*(\d{1,3})(?!\d)", line) or re.search(r"(\d{1,3})\s*일\s*(?:뒤|후)", line)
    if m:
        return today + timedelta(days=int(m.group(1)))
    m = re.search(r"([월화수목금토일])요일", line)
    if m:
        return today + timedelta(days=(_WEEKDAY_IDX[m.group(1)] - today.weekday()) % 7)
    return None


def _fallback_time(line: str, default_time: str) -> str:
    """'14:00', '3시', '오후 2시 반'처럼 '까지' 없이 적은 시각."""
    m = re.search(r"(오전|오후)\s*(\d{1,2})\s*(?:시\s*(?:(\d{1,2})\s*분|(반))?|:\s*(\d{2}))", line)
    if m:
        hh = int(m.group(2))
        mm = 30 if m.group(4) else int(m.group(3) or m.group(5) or 0)
        if m.group(1) == "오후" and hh < 12:
            hh += 12
        elif m.group(1) == "오전" and hh == 12:
            hh = 0
    else:
        m = re.search(r"(?<![\d:])(\d{1,2})\s*:\s*(\d{2})(?!\d)", line)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"(?<!\d)(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분|(반))?", line)
            if not m:
                return default_time
            hh = int(m.group(1))
            mm = 30 if m.group(3) else int(m.group(2) or 0)
            if 1 <= hh <= 7:
                hh += 12  # 업무시간 기준 '3시'는 오후 3시
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return default_time


def parse_quick_entry(
    extractor: RuleBasedExtractor,
    text: str,
    default_date: str,
    ref_date: Optional[date] = None,
) -> Optional[TaskItem]:
    """빠른 입력 문장 → 저장 전 TaskItem. 빈 문장이면 None."""
    line = re.sub(r"\s+", " ", text or "").strip()
    if not line:
        return None

    today = ref_date or date.today()
    due_date, due_time, estimated = extractor.extract_due(line, ref_date=today)
    date_found = not estimated
    if estimated:
        found = _fallback_date(line, today)
        date_found = found is not None
        # 문장에 날짜가 없으면 추정 기한(3영업일 뒤) 대신 사용자가 보고 있는 날짜에 넣음
        due_date = found.strftime("%Y-%m-%d") if found else default_date
    if due_time == extractor.default_time:
        due_time = _fallback_time(line, due_time)

    title = strip_date_words(line, numeric_dates=date_found)
    if len(title) < 2:
        title = line
    if len(title) > 75:
        title = title[:72] + "..."

    priority, label = extractor.detect_priority_and_label(line, TaskSource.MANUAL)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return TaskItem(
        title=title,
        description=line,
        source=TaskSource.MANUAL,
        source_ref="빠른 입력",
        content_hash=compute_content_hash(title, f"빠른 입력|{now}", line),
        due_date=due_date,
        due_time=due_time,
        status=TaskStatus.PENDING,
        label=label,
        priority=priority,
        recurrence=extractor.detect_recurrence(line),
        is_new_scan=False,
        due_estimated=False,
    )
