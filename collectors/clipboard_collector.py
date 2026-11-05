"""클립보드 텍스트 즉시 스캔 어댑터.

사용자가 사내포털 배부 문서나 메신저 창에서 텍스트를 드래그하여 복사(Ctrl+C)한 뒤
파일로 저장할 필요 없이 즉시 스캔·등록할 수 있게 지원합니다.
"""

from typing import List, Optional

from collectors.base_collector import BaseCollector
from core.database import DatabaseManager
from core.models import TaskItem
from parsers.portal_parser import PortalAndMessengerParser


class ClipboardCollector(BaseCollector):
    def __init__(self, db: DatabaseManager, parser: PortalAndMessengerParser):
        self.db = db
        self.parser = parser
        self._last_clipboard_text: str = ""

    def parse_and_register_text(
        self, raw_text: str, source_hint: str = "AUTO", source_ref: str = "클립보드 스캔"
    ) -> List[TaskItem]:
        """전달된 텍스트(클립보드 또는 직접 붙여넣기)에서 할 일을 추출해 DB에 등록."""
        if not raw_text or not raw_text.strip():
            return []

        extracted = self.parser.parse_plain_text(
            raw_text, source_hint=source_hint, source_ref=source_ref
        )
        created_list: List[TaskItem] = []
        for item in extracted:
            task_id, action = self.db.upsert_scanned_task(item)
            if action == "created" and task_id is not None:
                item.id = task_id
                created_list.append(item)
        return created_list

    def collect_from_tk_clipboard(self, tk_widget, force: bool = True) -> List[TaskItem]:
        try:
            clip_text = tk_widget.clipboard_get()
        except Exception:
            return []

        if not clip_text or not clip_text.strip():
            return []

        if not force and clip_text == self._last_clipboard_text:
            return []

        self._last_clipboard_text = clip_text
        return self.parse_and_register_text(clip_text, source_ref="클립보드 빠른스캔")

    def collect_and_register(self) -> List[TaskItem]:
        return []
