"""반복 업무의 다음 날짜 미리보기와 반복을 고를 때의 라벨.

반복 업무는 DB에 1건만 두고, 완료하면 다음 주기 업무를 1건 만듦 (DatabaseManager.set_task_status).
캘린더에는 앞으로의 날짜를 미리보기로 함께 그려 반복 일정이 한눈에 보이게 함. 미리보기는 저장하지 않음.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Dict, List

from core.database import add_months
from core.models import RecurrenceType, TaskItem

ROUTINE_LABEL = "ROUTINE"
DEFAULT_LABEL = "MANUAL"


def occurrence_dates(due: date, recurrence: str, start: date, end: date, today: date) -> List[date]:
    """due 다음 주기부터 [start, end] 안의 날짜들. 오늘보다 이른 날짜는 뺌 (지난 미리보기는 의미 없음)."""
    first = max(start, today)
    if first > end:
        return []
    out: List[date] = []
    if recurrence in (RecurrenceType.DAILY, RecurrenceType.WEEKLY):
        step = 1 if recurrence == RecurrenceType.DAILY else 7
        k = max(1, -(-(first - due).days // step))  # first 이상이 되는 첫 주기 (올림)
        d = due + timedelta(days=step * k)
        while d <= end:
            out.append(d)
            d += timedelta(days=step)
    elif recurrence == RecurrenceType.MONTHLY:
        k = max(1, (first.year - due.year) * 12 + first.month - due.month)
        d = add_months(due, k)
        while d <= end:
            if d >= first:
                out.append(d)
            k += 1
            d = add_months(due, k)
    return out


def preview_tasks(tasks: List[TaskItem], start: date, end: date, today: date) -> Dict[str, List[TaskItem]]:
    """미완료 반복 업무마다 앞으로의 날짜 미리보기 (날짜 → 미리보기 업무들). 같은 날 같은 제목의 업무가 있으면 뺌."""
    taken = {(t.due_date, t.title) for t in tasks if t.due_date}
    out: Dict[str, List[TaskItem]] = {}
    for t in tasks:
        if t.is_completed or t.recurrence == RecurrenceType.NONE or not t.due_date:
            continue
        try:
            due = datetime.strptime(t.due_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        for d in occurrence_dates(due, t.recurrence, start, end, today):
            ds = d.strftime("%Y-%m-%d")
            if (ds, t.title) in taken:
                continue
            taken.add((ds, t.title))
            out.setdefault(ds, []).append(replace(t, due_date=ds, is_preview=True, match_keys=[]))
    return out


def label_for_recurrence(label: str, recurrence: str) -> str:
    """반복을 고르면 라벨이 기본값(직접/수동등록)일 때만 '정기/반복업무'로. 직접 고른 다른 라벨은 그대로."""
    if recurrence != RecurrenceType.NONE and label == DEFAULT_LABEL:
        return ROUTINE_LABEL
    return label
