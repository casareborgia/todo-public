"""업무관리시스템 문서 자동 수집기.

업무관리시스템 문서 창(Edge IE 모드)이 열려 있으면, 웹한글 기안기에서 본문을 받아(collectors/workmgmt_window.py)
공문 PDF와 같은 규칙으로 제목·문서번호(하단 '시행' 번호)·회신기한을 뽑아 등록합니다.
- 문서 창마다 본문은 한 번만 받음: 같은 창·같은 제목이면 다시 꺼내지 않음
- 중복 방지: 문서번호 키(DOC:시행번호)라서 공문 PDF·클립보드 스캔으로 받은 같은 공문과 하나가 됨.
  문서번호가 없으면 본문 기준 키(WEBHWP:…). 한 번 받은 문서는 업무를 지웠어도 다시 등록하지 않음
- 같은 일을 쪽지로 먼저 받았으면(기한이 같고 제목 주제어가 겹침) 새로 만들지 않고 그 업무에 공문 출처를 덧붙임
"""

from datetime import date, datetime, timedelta
import hashlib
import re
from typing import Callable, Dict, List, Optional, Tuple

from collectors.base_collector import BaseCollector
from collectors.ezq_collector import ezq_key_of
from collectors.workmgmt_window import (
    DEFAULT_WINDOW_CLASS,
    ERR,
    NOCTRL,
    NOJS,
    TEXT,
    DocWindowResult,
    ListWindowResult,
    read_document_windows,
    read_list_windows,
)
from core.database import DatabaseManager, topic_words
from core.models import TaskItem, TaskSource, TaskStatus
from parsers.portal_parser import PortalAndMessengerParser
from parsers.workmgmt_list import ListRow, distributed_rows

WORKMGMT_SOURCE_REF = "업무관리시스템 문서"
WORKMGMT_DESC_PREFIX = "[업무관리시스템 문서"
WORKMGMT_LIST_SOURCE_REF = "업무관리시스템 배부"   # 결재시행 목록에서 받은 배부 문서 (기한은 문서를 열 때 정함)
LIST_KEY_PREFIX = "LIST:"
LIST_DISMISSED = "업무 아님"                         # workmgmt_docs에 남기는 배부 목록 기록 키
MIN_BODY_CHARS = 20          # 이보다 짧으면 기안기가 아직 본문을 다 불러오지 않은 것으로 봄
LINK_MESSAGE_DAYS = 3       # 배부 목록 업무를 이 날수 안에 받은 같은 일 쪽지 업무에 연결
GIVE_UP_SECONDS = 60         # 기안기가 없는(목록 화면 등) 창은 이 시간 동안만 다시 확인


def doc_key(text: str) -> str:
    """띄어쓰기·줄바꿈 차이는 같은 문서로 봄."""
    return hashlib.sha1(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()[:16]


class WorkMgmtDocCollector(BaseCollector):
    def __init__(
        self,
        db: DatabaseManager,
        parser: PortalAndMessengerParser,
        window_class: str = DEFAULT_WINDOW_CLASS,
        enabled: bool = True,
        reader: Callable[..., List[DocWindowResult]] = read_document_windows,
        list_reader: Callable[..., List[ListWindowResult]] = read_list_windows,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.db = db
        self.parser = parser
        self.window_class = window_class
        self.enabled = enabled
        self.reader = reader
        self.list_reader = list_reader
        self.clock = clock
        self._done: Dict[Tuple[int, str], bool] = {}            # 본문을 받았거나 포기한 (창, 제목)
        self._first_seen: Dict[Tuple[int, str], datetime] = {}  # (창, 제목)을 처음 본 시각
        self.merged: List[TaskItem] = []                         # 최근 확인에서 쪽지 업무에 합친 업무 (화면 표시용)

    def _skip(self, hwnd: int, title: str) -> bool:
        return self._done.get((hwnd, title), False)

    def parse_document(self, text: str, seen_at: datetime) -> List[TaskItem]:
        """기안기 본문 하나를 업무로 변환 (업무가 아니면 빈 목록). 등록은 하지 않음."""
        items = self.parser.extractor.extract_tasks(
            text,
            source_hint=TaskSource.PORTAL,
            source_ref=WORKMGMT_SOURCE_REF,
            ref_date=seen_at.date(),
        )
        key = doc_key(text)
        for i, item in enumerate(items):
            item.description = f"{WORKMGMT_DESC_PREFIX} {seen_at:%Y-%m-%d %H:%M} 확인]\n{text.strip()}"
            if not item.external_key:
                item.external_key = f"WEBHWP:{key}|{i}"
        return items

    def collect_and_register(self) -> List[TaskItem]:
        if not self.enabled:
            return []
        try:
            results = self.reader(self.window_class, skip=self._skip)
        except Exception:
            return []
        now = self.clock()
        created: List[TaskItem] = []
        self.merged = []
        for res in results:
            wkey = (res.hwnd, res.title)
            first = self._first_seen.setdefault(wkey, now)
            if res.status == TEXT and len(res.text.strip()) >= MIN_BODY_CHARS:
                self._done[wkey] = True
                created += self._handle_text(res, now)
            elif res.status in (NOCTRL, ERR, NOJS) and now - first > timedelta(seconds=GIVE_UP_SECONDS):
                self._done[wkey] = True  # 목록 화면 등 기안기가 없는 창: 더 확인하지 않음 (제목이 바뀌면 다시)
        created += self._collect_list(now)
        return created

    # ── 결재시행 목록의 배부받은 문서 ─────────────────────────────

    def row_to_task(self, row: ListRow, kind: str, seen_at: datetime) -> TaskItem:
        """배부 목록 행 → 추정 기한 업무 (등록은 하지 않음). kind: 'task' / 'review'(업무관리자)."""
        ref = received_date(row.received_at) or seen_at.date()
        due_date, due_time, _est = self.parser.extractor.extract_due("", ref_date=ref)
        key = f"DOC:{row.doc_no}" if row.doc_no else f"LISTTITLE:{doc_key(row.title)}"
        lines = [
            f"[업무관리시스템 배부 {seen_at:%Y-%m-%d %H:%M} 확인] 문서를 열면 회신기한으로 기한을 바꿉니다",
            f"처리구분: {row.role}",
        ]
        if row.sender_dept:
            lines.append(f"보낸 부서: {row.sender_dept}")
        if row.received_at:
            lines.append(f"수신일시: {row.received_at}")
        if row.doc_no:
            lines.append(f"문서번호: {row.doc_no}")
        return TaskItem(
            title=row.title,
            description="\n".join(lines),
            source=TaskSource.PORTAL,
            source_ref=WORKMGMT_LIST_SOURCE_REF,
            label=TaskSource.PORTAL,
            due_date=due_date,
            due_time=due_time,
            due_estimated=True,
            external_key=key,
            match_keys=[key],
            needs_review=(kind == "review"),
            is_new_scan=True,
        )

    def _collect_list(self, now: datetime) -> List[TaskItem]:
        try:
            windows = self.list_reader(self.window_class)
        except Exception:
            return []
        created: List[TaskItem] = []
        for win in windows:
            for row, kind in distributed_rows(win.rows):
                item = self.row_to_task(row, kind, now)
                list_key = f"{LIST_KEY_PREFIX}{item.external_key}"
                if self.db.get_workmgmt_doc(list_key) is not None:
                    continue  # 이미 본 배부 건 (업무를 지웠거나 재지정 후 다시 나타나도 새로 만들지 않음)
                stamp = f"{now:%Y-%m-%d %H:%M:%S}"
                ref = received_date(row.received_at) or now.date()
                since = f"{ref - timedelta(days=LINK_MESSAGE_DAYS):%Y-%m-%d} 00:00:00"
                note = f"{WORKMGMT_LIST_SOURCE_REF} 문서번호 {row.doc_no}" if row.doc_no else WORKMGMT_LIST_SOURCE_REF
                if self.db.find_task_by_keys([item.external_key]) is None:
                    # 같은 공문을 문서 창·PDF로 먼저 받음 (목록 번호는 접수번호라 키가 다를 수 있음): 그 업무에 이음
                    portal = self.db.find_confirmed_portal_task_by_title(row.title)
                    if portal is not None:
                        self.db.merge_into(portal, item, note, clear_review=(kind == "task"))
                        self.db.record_key_alias(item.external_key, portal.id)
                        self.db.record_workmgmt_doc(list_key, stamp, row.title, "이미 있음", portal.id)
                        continue
                    msg_task = self.db.find_topic_task(row.title, TaskSource.MESSENGER, since)
                    if msg_task is not None:
                        # 같은 일을 쪽지로 먼저 받음: 새로 만들지 않고 쪽지 업무에 배부 출처를 붙임.
                        # 업무담당자 배부는 확실한 원본이라 쪽지의 확인 필요를 풂, 업무관리자 배부는 그대로 둠
                        self.db.merge_into(msg_task, item, note, clear_review=(kind == "task"))
                        self.merged.append(msg_task)
                        self.db.record_workmgmt_doc(list_key, stamp, row.title, "쪽지 업무에 합침", msg_task.id)
                        continue
                task_id, action = self.db.upsert_scanned_task(item)
                if action == "created" and task_id is not None:
                    item.id = task_id
                    created.append(item)
                    result = "확인 필요" if item.needs_review else "등록"
                else:
                    result = "이미 있음"
                self.db.record_workmgmt_doc(list_key, stamp, row.title, result, task_id)
        return created

    def _handle_text(self, res: DocWindowResult, now: datetime) -> List[TaskItem]:
        key = doc_key(res.text)
        if self.db.get_workmgmt_doc(key) is not None:
            return []  # 이미 받은 문서 (지운 업무를 다시 만들지 않음)
        items = self.parse_document(res.text, now)
        result = "업무 아님" if not items else "등록"
        created: List[TaskItem] = []
        for item in items:
            outcome = self._register(item)
            if outcome == "created":
                created.append(item)
            elif outcome == "merged":
                result = "쪽지 업무에 합침"
            elif outcome == "dismissed":
                result = "업무 아님(배부 목록)"
        self.db.record_workmgmt_doc(key, f"{now:%Y-%m-%d %H:%M:%S}", items[0].title if items else res.title, result)
        return created

    def _register(self, item: TaskItem) -> str:
        note = f"{WORKMGMT_SOURCE_REF} {item.external_key.replace('DOC:', '문서번호 ')}"
        if self.db.find_task_by_keys([item.external_key] + list(item.match_keys)) is None:
            rec = self._list_record(item)
            if rec and rec.get("result") == LIST_DISMISSED:
                return "dismissed"  # 사용자가 배부 건을 '업무 아님'으로 지움: 문서를 열어도 다시 만들지 않음
            linked = self._list_linked_task(rec)
            if linked is not None:
                # 배부 목록으로 먼저 만든(또는 쪽지 업무에 합친) 업무: 본문의 기한으로 확정
                self._confirm_with_document(linked, item, note)
                self._fold_messenger_duplicate(linked)
                return "updated"
            similar = self.db.find_similar_open_task(item, TaskSource.MESSENGER)
            if similar is not None:
                self.db.merge_into(similar, item, note)
                self.merged.append(similar)
                return "merged"
        task_id, action = self.db.upsert_scanned_task(item)
        if action == "created" and task_id is not None:
            item.id = task_id
        if task_id is not None and action in ("created", "updated"):
            task = self.db.get_task(task_id)
            if task is not None:
                self._fold_messenger_duplicate(task)
        return action

    def _list_record(self, item: TaskItem) -> Optional[dict]:
        """본문 업무와 같은 배부 목록 기록 (문서번호, 번호가 다르면 제목)."""
        doc_no = item.external_key[4:] if item.external_key.startswith("DOC:") else ""
        return self.db.find_workmgmt_list_doc(doc_no, item.title)

    def _list_linked_task(self, rec: Optional[dict]) -> Optional[TaskItem]:
        """배부 목록 기록이 가리키는 미완료 업무."""
        if not rec or not rec.get("task_id"):
            return None
        task = self.db.get_task(rec["task_id"])
        if task is None or task.status == TaskStatus.COMPLETED:
            return None
        return task

    # ── 확인 필요 정리 창: 배부 건 '업무 아님' / 되돌리기 ─────────────

    def is_list_task(self, task: TaskItem) -> bool:
        """배부 목록 기록이 가리키는 업무 (쪽지 업무에 합친 경우 포함: 공식 배부 건이라 배부 쪽에서 정리)."""
        return task.id is not None and self.db.find_workmgmt_doc_by_task(task.id) is not None

    def review_reason(self, task: TaskItem) -> str:
        m = re.search(r"^처리구분: (.+)$", task.description or "", re.MULTILINE)
        role = m.group(1).strip() if m else "업무관리자"
        return f"{role}로 지정된 배부 문서 (처리구분: {role})"

    def dismiss_task(self, task: TaskItem) -> Optional[str]:
        """'업무 아님': 배부 기록에 남기고 업무를 지움. 다시 목록에 나오거나 문서를 열어도 만들지 않음.
        되돌리기용 기록 키를 돌려줌 (배부 업무가 아니면 None)."""
        if task.id is None or not self.is_list_task(task):
            return None
        rec = self.db.find_workmgmt_doc_by_task(task.id)
        if rec is None:
            return None
        self.db.set_workmgmt_doc_task(rec["doc_key"], None, LIST_DISMISSED)
        self.db.delete_task(task.id)
        return rec["doc_key"]

    def undo_dismiss(self, task: TaskItem, list_key: str) -> Optional[int]:
        """'업무 아님' 되돌리기: 지운 업무를 그대로 다시 넣고 배부 기록을 되돌림."""
        task.id = None
        task_id, _created = self.db.add_task(task, skip_if_duplicate=False)
        self.db.set_workmgmt_doc_task(list_key, task_id, "확인 필요" if task.needs_review else "등록")
        task.id = task_id
        return task_id

    def _confirm_with_document(self, task: TaskItem, item: TaskItem, note: str) -> None:
        if not item.due_estimated:
            task.due_date, task.due_time, task.due_estimated = item.due_date, item.due_time, False
            task.notified_at = None  # 바뀐 기한으로 다시 알림
        if not task.external_key.startswith("DOC:") and item.external_key.startswith("DOC:"):
            task.external_key = item.external_key
        elif item.external_key.startswith("DOC:") and item.external_key != task.external_key:
            # 목록 번호(접수번호)와 본문 시행번호가 다름: 나중에 같은 공문 PDF·클립보드가 와도 이 업무로
            self.db.record_key_alias(item.external_key, task.id)
        task.needs_review = False  # 공문 본문이 확인됨
        if note not in task.source_ref:
            task.source_ref = f"{task.source_ref} | {note}" if task.source_ref else note
        task.is_new_scan = True
        self.db.update_task(task)

    def _fold_messenger_duplicate(self, task: TaskItem) -> None:
        """기한이 정해진 공문 업무와 같은 일(같은 날 + 주제어)인 다른 쪽지 업무가 있으면 하나로 (먼저 있던 업무를 남김)."""
        if task.due_estimated or len(topic_words(task.title)) < 2:
            return  # 업무 하나를 지우는 합치기라 주제어 1개로는 하지 않음
        other = self.db.find_similar_open_task(task, TaskSource.MESSENGER)
        if other is None or other.id == task.id:
            return
        keep, drop = (other, task) if other.id < task.id else (task, other)
        if self.db.fold_task_into(keep, drop, official=task):
            self.merged.append(keep)


def received_date(text: str) -> Optional[date]:
    """목록의 수신일시 글자('2026-11-04 09:12', '2026.11.04' 등)에서 날짜. 못 읽으면 None."""
    m = re.search(r"(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})", text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
