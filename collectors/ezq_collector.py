"""EzQ 메신저 받은쪽지 창 자동 수집기.

몇 초마다 열려 있는 받은쪽지 창의 본문을 읽어, 처음 보는 쪽지만 업무 판단·기한 추출 후 등록합니다.
- 받은 시각: 창에서 읽을 수 없어 쪽지 창을 처음 발견한 시각으로 대신함 ('내일까지' 등의 기준일)
- 보낸 사람: 창에서 읽을 수 없어 표시하지 않음
- 중복 방지: 본문 기준 키(EZQ:...)라서 프로그램을 다시 켜도 열려 있는 같은 쪽지는 다시 등록되지 않음
"""

from datetime import datetime
import hashlib
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

from collectors.base_collector import BaseCollector
from collectors.ezq_window import DEFAULT_WINDOW_CLASS, read_message_windows
from core.database import DatabaseManager
from core.models import TaskItem, TaskSource
from parsers.message_scorer import EXCLUDE, REVIEW, ScoreResult, score_message, strip_copied_lines
from parsers.portal_parser import (
    MESSENGER_SEPARATOR_RE,
    REFERS_TO_QUOTE_RE,
    PortalAndMessengerParser,
    _parse_messenger_block,
)

EZQ_SOURCE_REF = "EzQ 쪽지"


def split_quoted_history(body: str) -> Tuple[str, List[dict]]:
    """쪽지 창 본문을 (맨 위 받은 글, [인용된 이전 글...])로 나눔. 인용이 없으면 (본문, [])."""
    parts = MESSENGER_SEPARATOR_RE.split(body.replace("\r\n", "\n"))
    history = [m for m in (_parse_messenger_block(p) for p in parts[1:]) if m and m.get("body")]
    return parts[0].strip(), history


def message_key(body: str) -> str:
    """띄어쓰기·줄바꿈 차이는 같은 쪽지로 봄."""
    return hashlib.sha1(re.sub(r"\s+", "", body).encode("utf-8")).hexdigest()[:16]


class EzqMessageCollector(BaseCollector):
    MAX_RETRY = 3  # 처리 중 오류가 난 쪽지를 다시 시도하는 횟수 (계속 실패하는 쪽지가 3초마다 반복되지 않게)

    def __init__(
        self,
        db: DatabaseManager,
        parser: PortalAndMessengerParser,
        window_class: str = DEFAULT_WINDOW_CLASS,
        enabled: bool = True,
        reader: Callable[[str], List[Tuple[int, str]]] = read_message_windows,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.db = db
        self.parser = parser
        self.window_class = window_class
        self.enabled = enabled
        self.reader = reader
        self.clock = clock
        self._seen: Dict[str, datetime] = {}  # 이번 실행에서 이미 처리한 쪽지 → 처음 발견한 시각
        self._first_seen: Dict[str, datetime] = {}  # 오류로 다시 시도할 쪽지 → 처음 발견한 시각
        self._fail_count: Dict[str, int] = {}

    @staticmethod
    def _split(body: str) -> Tuple[str, str, List[dict]]:
        """(판단할 맨 위 글, 원래 맨 위 글, 인용 목록). 판단할 글은 인용 글에서 옮겨 붙인 줄을 뺀 것."""
        top, history = split_quoted_history(body)
        return strip_copied_lines(top, [h["body"] for h in history]), top, history

    def judge(self, body: str) -> ScoreResult:
        """쪽지 본문의 업무 점수 판정 (맨 위 받은 글 기준, 인용을 가리키면 첫 인용 글도 함께)."""
        top, _orig, history = self._split(body)
        return score_message(top, history[0]["body"] if history else "")

    def parse_message(self, body: str, received_at: datetime, force: bool = False) -> List[TaskItem]:
        """쪽지 본문 하나를 업무로 변환 (업무가 아니면 빈 목록). 등록은 하지 않음.

        답장 쪽지 창에는 받은 글 아래 '-----' 줄로 이전 대화가 인용됨 ('발신자 :/수신자 :/날 짜 :').
        업무 판단은 맨 위 받은 글로만 하고(인용 머리말 때문에 잡담·답장이 오늘 마감 업무로 들어가던 문제),
        '아래 내용 확인 부탁드립니다'처럼 인용을 가리킬 때만 인용된 글의 기한을 씀. 설명에는 전체를 남김.
        판단은 점수 방식(parsers/message_scorer.py): 공지·참여 독려처럼 부탁 표현만 있는 쪽지를 걸러냄.
        force=True: 사용자가 '업무로 등록'을 고른 쪽지 — 판정과 상관없이 업무로 만듦.
        """
        top, orig_top, history = self._split(body)
        if force and not top.strip():
            top = orig_top  # 사용자가 고른 쪽지는 옮겨 붙인 줄뿐이어도 업무로 만듦
        if not top.strip():
            return []
        judged = self.judge(body)
        if judged.verdict == EXCLUDE and not force:
            return []
        items = self.parser.extractor.extract_tasks(
            top,
            source_hint=TaskSource.MESSENGER,
            source_ref=EZQ_SOURCE_REF,
            ref_date=received_at.date(),
            already_judged=True,
        )
        if history:
            for item in items:
                if item.due_estimated and REFERS_TO_QUOTE_RE.search(top):
                    self.parser._take_due_from_history(item, history)
        key = message_key(body)
        for i, item in enumerate(items):
            # 애매한 쪽지는 버리지 않고 '확인 필요'로 남김: 사용자가 정리하기 전까지 캘린더·알림에서 빠짐 (누락 방지)
            item.needs_review = judged.verdict == REVIEW and not force
            item.ezq_key = key
            # 판정 근거는 설명에 넣지 않고 판정 기록(ezq_messages)에 둠 → 상세 화면 정보 칸에 표시
            item.description = f"{EZQ_DESC_PREFIX} {received_at:%Y-%m-%d %H:%M} 확인]\n{body.strip()}"
            if not item.external_key.startswith("DOC:"):
                item.external_key = f"EZQ:{key}|{i}"
        return items

    def collect_and_register(self) -> List[TaskItem]:
        if not self.enabled:
            return []
        try:
            windows = self.reader(self.window_class)
        except Exception:
            return []
        created: List[TaskItem] = []
        for _hwnd, body in windows:
            key = message_key(body)
            if key in self._seen:
                continue
            # 실패해서 다시 시도하는 쪽지는 처음 발견한 시각을 그대로 씀 ('내일까지'의 기준)
            received_at = self._first_seen.get(key) or self.clock()
            self._seen[key] = received_at
            if self.db.get_ezq_message(key) is not None or self.db.find_task_by_keys([f"EZQ:{key}|0"]) is not None:
                # 이미 판정한 쪽지 (프로그램을 다시 켰을 때 열려 있던 창, 사용자가 지웠거나 '업무 아님'으로 표시한 쪽지):
                # 다시 등록하거나 오늘 날짜로 기한을 다시 계산하지 않음
                continue
            try:
                judged = self.judge(body)
                items = self.parse_message(body, received_at)
                self.db.record_ezq_message(
                    key, f"{received_at:%Y-%m-%d %H:%M:%S}", judged.verdict, judged.score, judged.reasons, body.strip()
                )
                created += self._register(items)
                self._first_seen.pop(key, None)
            except Exception as e:  # noqa: BLE001 - 한 쪽지의 오류로 다른 쪽지 수집을 멈추지 않음
                # 판정 기록만 남고 업무가 안 생긴 채 '이미 본 쪽지'로 굳지 않게 되돌림 (DB 잠김 등 일시 오류는 다음 확인 때 다시 시도)
                self._fail_count[key] = self._fail_count.get(key, 0) + 1
                print(f"[EzQ] 쪽지 처리 오류 ({self._fail_count[key]}회): {e!r}", file=sys.stderr)
                if self._fail_count[key] < self.MAX_RETRY:
                    self._seen.pop(key, None)
                    self._first_seen[key] = received_at
                    try:
                        if self.db.find_task_by_ezq_key(key) is None:
                            self.db.forget_ezq_message(key)
                    except Exception:  # noqa: BLE001
                        pass
        return created

    def _register(self, items: List[TaskItem], merge_similar: bool = True) -> List[TaskItem]:
        created = []
        for item in items:
            if merge_similar and self.db.find_task_by_keys([item.external_key] + list(item.match_keys)) is None:
                # 같은 공문을 업무관리시스템 문서로 이미 받았으면(기한이 같고 제목 주제어가 겹침) 새로 만들지 않고 출처만 덧붙임
                similar = self.db.find_similar_open_task(item, TaskSource.PORTAL)
                if similar is not None:
                    self.db.merge_into(similar, item, f"{EZQ_SOURCE_REF} (같은 일)", clear_review=False)  # 쪽지로는 확인 필요를 풀지 않음
                    if item.ezq_key:
                        self.db.set_ezq_merged_task(item.ezq_key, similar.id)  # 판정 기록 창에서 '업무 있음'으로
                    continue
            task_id, action = self.db.upsert_scanned_task(item)
            if action == "created" and task_id is not None:
                item.id = task_id
                created.append(item)
        return created

    # ── 사용자 정답 표시 ────────────────────────────────────────

    def register_as_task(self, msg_key: str) -> List[TaskItem]:
        """'제외된 쪽지'에서 사용자가 업무라고 고른 쪽지를 등록 (기한 기준일은 처음 받은 시각)."""
        rec = self.db.get_ezq_message(msg_key)
        if not rec or not rec.get("body"):
            return []
        received_at = datetime.strptime(rec["first_seen"], "%Y-%m-%d %H:%M:%S")
        # 사용자가 직접 고른 쪽지는 비슷한 공문 업무가 있어도 따로 등록
        created = self._register(self.parse_message(rec["body"], received_at, force=True), merge_similar=False)
        if created:
            # 실제로 업무가 생겼을 때만 정답 표시 (실패했는데 '업무'로 바뀌어 목록에서 사라지던 문제)
            self.db.set_ezq_user_label(msg_key, DatabaseManager.EZQ_LABEL_TASK)
        return created

    def has_task(self, msg_key: str) -> bool:
        """그 쪽지에서 나온 업무가 지금 있는지 (확인 필요 포함). 없으면 '업무로 등록'으로 되살릴 수 있음."""
        return self.db.find_task_by_ezq_key(msg_key) is not None

    def dismiss_task(self, task: TaskItem) -> Optional[str]:
        """'업무 아님': 쪽지에 정답 표시를 남기고 업무를 지움 (확인 필요 업무 포함). 되돌리기용 쪽지 키를 돌려줌 (EzQ 업무가 아니면 None)."""
        key = ezq_key_of(task)
        if key is None or task.id is None:
            return None
        self.db.set_ezq_user_label(
            key, DatabaseManager.EZQ_LABEL_NOT_TASK,
            body_if_missing=ezq_body_of(task), first_seen_if_missing=ezq_received_at_of(task),
        )
        self.db.delete_task(task.id)
        return key

    def confirm_review(self, task: TaskItem) -> Optional[TaskItem]:
        """'업무 맞음': 확인 필요를 풀어 캘린더·알림에 나오게 하고, 쪽지에 '업무' 정답 표시를 남김."""
        if task.id is None:
            return None
        # 추정 기한은 확인한 날 기준으로 다시 잡음 (늦게 정리하면 이미 지난 기한으로 바로 알림이 뜨던 문제)
        due_date, due_time, _est = self.parser.extractor.extract_due("", ref_date=self.clock().date())
        confirmed = self.db.confirm_review_task(task.id, new_due=(due_date, due_time))
        key = ezq_key_of(task)
        if confirmed and key:
            self.db.set_ezq_user_label(
                key, DatabaseManager.EZQ_LABEL_TASK,
                body_if_missing=ezq_body_of(task), first_seen_if_missing=ezq_received_at_of(task),
            )
        return confirmed

    def undo_dismiss(self, task: TaskItem, msg_key: str) -> Optional[int]:
        """'업무 아님' 되돌리기: 지운 업무를 그대로 다시 넣고 정답 표시를 지움."""
        task.id = None
        task_id, _created = self.db.add_task(task, skip_if_duplicate=False)
        self.db.set_ezq_user_label(msg_key, "")
        task.id = task_id
        return task_id


EZQ_DESC_PREFIX = "[EzQ 쪽지"


def ezq_body_of(task: TaskItem) -> str:
    """EzQ 업무 설명에서 쪽지 본문 (첫 줄 '[EzQ 쪽지 … 확인]'과, 예전 버전의 '[판정: …]' 줄 제외)."""
    desc = task.description or ""
    if not desc.startswith(EZQ_DESC_PREFIX):
        return ""
    lines = desc.split("\n")[1:]
    if lines and lines[0].startswith("[판정:"):
        lines = lines[1:]
    return "\n".join(lines)


def ezq_received_at_of(task: TaskItem) -> str:
    """EzQ 업무 설명 첫 줄 '[EzQ 쪽지 2026-10-28 14:05 확인]'의 받은 시각 ('YYYY-MM-DD HH:MM:SS'). 없으면 ''."""
    m = re.match(r"\[EzQ 쪽지 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", task.description or "")
    return f"{m.group(1)}:00" if m else ""


def ezq_key_of(task: TaskItem) -> Optional[str]:
    """EzQ 쪽지에서 온 업무면 쪽지 키, 아니면 None.

    저장된 쪽지 키(ezq_key)를 먼저 씀. 예전 업무는 EZQ: 원본 키, 그것도 없으면(DOC: 키) 설명의 본문으로 계산.
    """
    if task.ezq_key:
        return task.ezq_key
    if task.external_key.startswith("EZQ:"):
        return task.external_key[4:].split("|", 1)[0]
    body = ezq_body_of(task)
    return message_key(body) if body.strip() else None

