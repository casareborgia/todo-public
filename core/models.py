"""할 일(Task) 및 스캔 이력 데이터 모델 정의."""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional


class TaskStatus:
    PENDING = "PENDING"         # 진행 대기 / 미완료
    IN_PROGRESS = "IN_PROGRESS" # 진행 중
    COMPLETED = "COMPLETED"     # 완료됨

    ALL_LABELS = {
        PENDING: "미완료(대기)",
        IN_PROGRESS: "진행중",
        COMPLETED: "완료",
    }


class TaskSource:
    PORTAL = "PORTAL"           # 사내포털 / 업무관리시스템
    MESSENGER = "MESSENGER"     # 사내 메신저
    CLIPBOARD = "CLIPBOARD"     # 클립보드 빠른 수집
    MANUAL = "MANUAL"           # 수동 입력

    ALL_LABELS = {
        PORTAL: "사내포털 문서",
        MESSENGER: "사내메신저",
        CLIPBOARD: "클립보드 스캔",
        MANUAL: "수동 등록",
    }


class RecurrenceType:
    NONE = "NONE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"

    ALL_LABELS = {
        NONE: "반복 없음",
        DAILY: "매일 반복",
        WEEKLY: "매주 반복",
        MONTHLY: "매월 반복",
    }


@dataclass
class TaskItem:
    id: Optional[int] = None
    title: str = ""
    description: str = ""
    source: str = TaskSource.MANUAL
    source_ref: str = ""             # 문서번호, 파일명, 발신자 등 메타정보
    content_hash: str = ""           # 중복 스캔 방지용 해시 (SHA-256)
    due_date: str = ""               # YYYY-MM-DD 형식 마감일
    due_time: str = "18:00"          # HH:MM 형식 마감시각
    status: str = TaskStatus.PENDING
    label: str = "MANUAL"            # PORTAL, MESSENGER, URGENT, MANUAL, ROUTINE
    priority: int = 2                # 1: 높음(긴급), 2: 보통, 3: 낮음
    recurrence: str = RecurrenceType.NONE
    is_new_scan: bool = False        # 자동 스캔으로 등록된 후 사용자가 아직 확인 안 한 상태
    notified_at: Optional[str] = None
    snooze_until: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    completed_at: Optional[str] = None
    external_key: str = ""           # 원본 식별키 (예: 'DOC:감사실-1204', 'MSG:이영희|2026-10-20 09:20:15|0')
    due_estimated: bool = False      # 원본에 기한이 없어 기본값으로 추정한 기한인지 (PDF 등으로 확정되면 False)
    needs_review: bool = False       # 업무인지 애매해 사용자 확인을 기다리는 쪽지 업무 (캘린더·알림에서 빠짐)
    ezq_key: str = ""                # 업무가 나온 EzQ 쪽지 키 (판정 기록 ezq_messages.msg_key)
    match_keys: List[str] = field(default_factory=list)  # 기존 업무 매칭용 보조 키 (DB 저장 안 함)
    is_preview: bool = False         # 캘린더에만 그리는 반복 업무의 다음 날짜 미리보기 (DB 저장 안 함)

    @property
    def is_completed(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    def is_overdue(self, ref_date: Optional[date] = None) -> bool:
        """기한이 지났으나 완료되지 않은(자동 이월 대상) 업무인지 판단."""
        if self.is_completed or not self.due_date:
            return False
        today = ref_date or date.today()
        try:
            d = datetime.strptime(self.due_date, "%Y-%m-%d").date()
            return d < today
        except ValueError:
            return False

    def days_remaining(self, ref_date: Optional[date] = None) -> Optional[int]:
        """오늘 기준 남은 일수 (음수이면 기한 경과 일수)."""
        if not self.due_date:
            return None
        today = ref_date or date.today()
        try:
            d = datetime.strptime(self.due_date, "%Y-%m-%d").date()
            return (d - today).days
        except ValueError:
            return None

    def d_day_badge(self, ref_date: Optional[date] = None) -> str:
        """D-Day 표시 문자열 반환."""
        if self.is_completed:
            return "완료"
        diff = self.days_remaining(ref_date)
        if diff is None:
            return "기한없음"
        if diff < 0:
            badge = f"기한경과 D+{abs(diff)}"
        elif diff == 0:
            badge = "오늘마감(D-Day)"
        else:
            badge = f"D-{diff}"
        return f"{badge}·기한추정" if self.due_estimated else badge
