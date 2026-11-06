"""반복 업무의 다음 날짜 미리보기와 반복을 고를 때의 라벨.

반복 업무는 DB에 1건만 두고, 완료하면 다음 주기 업무를 1건 만듦 (DatabaseManager.set_task_status).
캘린더에는 앞으로의 날짜를 미리보기로 함께 그려 반복 일정이 한눈에 보이게 함. 미리보기는 저장하지 않음.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from core.database import add_months, next_period  # noqa: F401  next_period: 반복 모듈에서도 쓰게 다시 내보냄
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
            out.setdefault(ds, []).append(
                replace(t, due_date=ds, start_date=_shifted_start(t, due, d), is_preview=True, match_keys=[])
            )
    return out


def _shifted_start(t: TaskItem, due: date, new_due: date) -> str:
    """미리보기 회차의 시작일. 기간 업무면 next_period처럼 시작도 같은 주기만큼 옮김 (기간 아니면 '')."""
    if not t.is_period:
        return t.start_date
    start = datetime.strptime(t.start_date, "%Y-%m-%d").date()
    if t.recurrence == RecurrenceType.MONTHLY:
        k = (new_due.year - due.year) * 12 + new_due.month - due.month
        return add_months(start, k).strftime("%Y-%m-%d")
    return (start + (new_due - due)).strftime("%Y-%m-%d")


def _parse(ds: str) -> Optional[date]:
    try:
        return datetime.strptime(ds, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def span_dates(task: TaskItem, first: date, last: date) -> List[date]:
    """달력 [first, last] 안에서 업무가 보일 날짜들. 기간 업무는 시작~끝의 모든 날, 아니면 마감일 하루."""
    due = _parse(task.due_date)
    if due is None:
        return []
    start = _parse(task.start_date) if task.is_period else None
    lo, hi = max(start or due, first), min(due, last)
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


def drag_due_date(task: TaskItem, from_date: str, to_date: str) -> str:
    """달력에서 끌어 놓은 뒤의 새 마감일. 기간 업무는 어느 칸에서 끌었든 옮긴 날수만큼 끝을 밀고
    (시작은 reschedule_task가 같은 날수만큼 옮김), 아니면 놓은 칸 날짜."""
    if not task.is_period:
        return to_date
    delta = _parse(to_date) - _parse(from_date)
    return (_parse(task.due_date) + delta).strftime("%Y-%m-%d")


def label_for_recurrence(label: str, recurrence: str) -> str:
    """반복을 고르면 라벨이 기본값(직접/수동등록)일 때만 '정기/반복업무'로. 직접 고른 다른 라벨은 그대로."""
    if recurrence != RecurrenceType.NONE and label == DEFAULT_LABEL:
        return ROUTINE_LABEL
    return label


def next_task_message(next_task: TaskItem, created: bool) -> str:
    """반복 업무 완료 뒤 안내 문구. 만료 때 이미 만든 다음 기간이면 '이미 등록'으로 알림."""
    next_line = (
        f"• 다음 기간: {next_task.start_date} ~ {next_task.due_date}"
        if next_task.is_period
        else f"• 다음 마감일: {next_task.due_date}"
    )
    if not created:
        return f"다음 기간 업무가 이미 등록되어 있습니다.\n{next_line}"
    return f"정기/반복 업무가 완료되어 다음 주기 일정이 자동 등록되었습니다.\n{next_line}"
