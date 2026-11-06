"""SQLite3 기반 로컬 데이터베이스 관리 모듈.

외부 DB 서버나 네트워크 포트 없이 단일 파일(todo_data.db)로 동작하며,
- 일정/할일 CRUD
- 미완료 업무 자동 이월(Rollover) 조회
- 반복 일정(매일/매주/매월) 완료 시 차기 일정 자동 생성
- 중복 스캔 방지(SHA-256 해시 및 파일 수정시각 기록)
- 키워드 검색 및 다중 필터
를 담당합니다.
"""

import calendar
import difflib
import hashlib
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus


def compute_content_hash(title: str, source_ref: str = "", raw_snippet: str = "") -> str:
    """업무 제목과 출처 참조값을 조합해 중복 방지용 SHA-256 해시를 생성."""
    norm = f"{title.strip()}|{source_ref.strip()}|{raw_snippet.strip()[:120]}".lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# 주제어로 보지 않는 흔한 업무·공문 단어 (이것만 겹치면 같은 일로 보지 않음)
TOPIC_STOPWORDS = {
    "제출", "요청", "결과", "알림", "시행", "관련", "공문", "회신", "협조", "안내", "자료", "부탁", "바랍니다",
    "드립니다", "작성", "보고", "검토", "확인", "처리", "실시", "계획", "제출요청", "알려", "주시기", "부서", "처실별",
    "감사합니다", "입니다", "있습니다", "합니다", "왔습니다", "해야", "하니", "까지", "내용", "관하여", "대한",
}


def topic_words(title: str) -> List[str]:
    """제목의 주제어: 두 글자 이상, 숫자가 없는 낱말에서 흔한 업무 단어를 뺀 것."""
    words = []
    for w in re.findall(r"[가-힣A-Za-z]{2,}", title or ""):
        if w not in TOPIC_STOPWORDS and w not in words:
            words.append(w)
    return words


def shares_topic(title: str, other_text: str) -> bool:
    """title의 주제어가 other_text에 (주제어가 하나뿐이면 1개, 아니면 2개 이상) 들어 있는지."""
    words = topic_words(title)
    if not words:
        return False
    hits = sum(1 for w in words if w in (other_text or ""))
    return hits >= min(2, len(words))


def _normalize_title(title: str) -> str:
    """제목 비교용 정규화: 말머리([배부] 등)·공백·문장부호 제거."""
    t = re.sub(r"^\s*\[[^\]]{1,10}\]\s*", "", title or "")
    return re.sub(r"[\s\W_]+", "", t).lower()


def add_months(src_date: date, months: int = 1) -> date:
    """월 단위 반복 일정 계산 유틸리티."""
    month = src_date.month - 1 + months
    year = src_date.year + month // 12
    month = month % 12 + 1
    day = min(src_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_period(start: date, end: date, recurrence: str, today: date) -> Tuple[date, date]:
    """반복 기간 업무의 다음 기간. 시작·끝을 한 주기씩 함께 밀고, 끝이 오늘 이후가 될 때까지 반복.

    완료 시·만료 시 모두 같은 값이 나와야 같은 다음 기간을 두 번 만들지 않음.
    월 반복은 기존 반복 업무처럼 직전 날짜에 add_months(·, 1) (말일 보정은 누적됨).
    """
    while True:
        if recurrence == RecurrenceType.DAILY:
            start, end = start + timedelta(days=1), end + timedelta(days=1)
        elif recurrence == RecurrenceType.WEEKLY:
            start, end = start + timedelta(days=7), end + timedelta(days=7)
        elif recurrence == RecurrenceType.MONTHLY:
            start, end = add_months(start, 1), add_months(end, 1)
        else:
            raise ValueError(f"반복 아님: {recurrence}")
        if end >= today:
            return start, end


class DatabaseManager:
    def __init__(self, db_path: str = "todo_data.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._next_task_created = True  # 마지막 set_task_status가 다음 업무를 새로 만들었는지
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        source TEXT DEFAULT 'MANUAL',
                        source_ref TEXT DEFAULT '',
                        content_hash TEXT DEFAULT '',
                        due_date TEXT DEFAULT '',
                        due_time TEXT DEFAULT '18:00',
                        status TEXT DEFAULT 'PENDING',
                        label TEXT DEFAULT 'MANUAL',
                        priority INTEGER DEFAULT 2,
                        recurrence TEXT DEFAULT 'NONE',
                        is_new_scan INTEGER DEFAULT 0,
                        notified_at TEXT,
                        snooze_until TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_hash ON tasks(content_hash)"
                )
                # 기존 DB 자동 마이그레이션: 원본 식별키 / 기한 추정 여부 컬럼 추가
                existing_cols = {r["name"] for r in cur.execute("PRAGMA table_info(tasks)").fetchall()}
                if "external_key" not in existing_cols:
                    cur.execute("ALTER TABLE tasks ADD COLUMN external_key TEXT DEFAULT ''")
                if "due_estimated" not in existing_cols:
                    cur.execute("ALTER TABLE tasks ADD COLUMN due_estimated INTEGER DEFAULT 0")
                if "needs_review" not in existing_cols:
                    # 업무인지 애매한 쪽지('확인 필요'): 사용자가 정리하기 전까지 캘린더·알림·브리핑에서 뺌.
                    # 예전 버전은 제목 앞 '[확인 필요] '로만 표시했으므로 그 업무를 옮겨 옴.
                    # EzQ 쪽지 업무만 (직접 만든 '[확인 필요] 계약서 검토' 같은 업무는 그대로 둠)
                    cur.execute("ALTER TABLE tasks ADD COLUMN needs_review INTEGER DEFAULT 0")
                    cur.execute(
                        "UPDATE tasks SET needs_review = 1, title = substr(title, 9)"
                        " WHERE title LIKE '[확인 필요] %' AND status != 'COMPLETED'"
                        " AND (external_key LIKE 'EZQ:%' OR description LIKE '[EzQ 쪽지%')"
                    )
                if "ezq_key" not in existing_cols:
                    # 업무가 나온 EzQ 쪽지의 키. 문서번호가 있어 DOC: 키가 된 쪽지나 설명을 고친 업무도 쪽지를 찾게 함
                    cur.execute("ALTER TABLE tasks ADD COLUMN ezq_key TEXT DEFAULT ''")
                    cur.execute(
                        "UPDATE tasks SET ezq_key = substr(external_key, 5, instr(external_key, '|') - 5)"
                        " WHERE external_key LIKE 'EZQ:%|%'"
                    )
                if "start_date" not in existing_cols:
                    # 기간 업무의 시작일 (due_date는 기간의 끝). 빈 값이면 일반 마감 업무
                    cur.execute("ALTER TABLE tasks ADD COLUMN start_date TEXT DEFAULT ''")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_external_key ON tasks(external_key)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scanned_files (
                        file_path TEXT PRIMARY KEY,
                        file_mtime REAL NOT NULL,
                        file_hash TEXT NOT NULL,
                        scanned_at TEXT NOT NULL,
                        extracted_count INTEGER DEFAULT 0
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                # EzQ 쪽지 판정 기록: 한 번 판정한 쪽지는 다시 등록하지 않고(삭제했어도),
                # 제외된 쪽지 확인·사용자 정답 표시(업무 / 업무아님)에 씀
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ezq_messages (
                        msg_key TEXT PRIMARY KEY,
                        first_seen TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        score INTEGER DEFAULT 0,
                        reasons TEXT DEFAULT '',
                        body TEXT NOT NULL,
                        user_label TEXT DEFAULT ''
                    )
                    """
                )
                # 업무관리시스템 문서 수집 기록: 본문을 한 번 받은 문서는 업무를 지웠어도 다시 등록하지 않음
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workmgmt_docs (
                        doc_key TEXT PRIMARY KEY,
                        first_seen TEXT NOT NULL,
                        title TEXT DEFAULT '',
                        result TEXT DEFAULT ''
                    )
                    """
                )
                # 다른 업무(공문 등)에 합쳐진 쪽지가 가리키는 업무: 그 쪽지도 '업무 있음'으로 봄
                ezq_cols = {r["name"] for r in cur.execute("PRAGMA table_info(ezq_messages)").fetchall()}
                if "merged_task_id" not in ezq_cols:
                    cur.execute("ALTER TABLE ezq_messages ADD COLUMN merged_task_id INTEGER")
                # 배부 목록·문서 기록이 가리키는 업무 (목록 업무와 문서 본문을 잇고, 쪽지 업무에 합친 경우도 찾음)
                wm_cols = {r["name"] for r in cur.execute("PRAGMA table_info(workmgmt_docs)").fetchall()}
                if "task_id" not in wm_cols:
                    cur.execute("ALTER TABLE workmgmt_docs ADD COLUMN task_id INTEGER")
                # 문서 본문 판정 기록 (EzQ 쪽지와 같은 점수표). 이전 기록은 body가 비어 목록에 안 나옴
                for col, ddl in (
                    ("verdict", "TEXT DEFAULT ''"),
                    ("score", "INTEGER DEFAULT 0"),
                    ("reasons", "TEXT DEFAULT ''"),
                    ("body", "TEXT DEFAULT ''"),
                    ("user_label", "TEXT DEFAULT ''"),
                ):
                    if col not in wm_cols:
                        cur.execute(f"ALTER TABLE workmgmt_docs ADD COLUMN {col} {ddl}")
                conn.commit()
            finally:
                conn.close()

    def _row_to_task(self, row: sqlite3.Row) -> TaskItem:
        return TaskItem(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            source=row["source"] or TaskSource.MANUAL,
            source_ref=row["source_ref"] or "",
            content_hash=row["content_hash"] or "",
            due_date=row["due_date"] or "",
            due_time=row["due_time"] or "18:00",
            status=row["status"] or TaskStatus.PENDING,
            label=row["label"] or "MANUAL",
            priority=int(row["priority"] if row["priority"] is not None else 2),
            recurrence=row["recurrence"] or RecurrenceType.NONE,
            is_new_scan=bool(row["is_new_scan"]),
            notified_at=row["notified_at"],
            snooze_until=row["snooze_until"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            external_key=row["external_key"] or "",
            due_estimated=bool(row["due_estimated"]),
            needs_review=bool(row["needs_review"]),
            ezq_key=row["ezq_key"] or "",
            start_date=row["start_date"] or "",
        )

    @staticmethod
    def _normalized_start(task: TaskItem) -> str:
        """시작일이 마감일과 같거나 늦으면 기간이 아니므로 빈 값으로 저장."""
        start = (task.start_date or "").strip()
        due = (task.due_date or "").strip()
        if start and due and start >= due:
            return ""
        return start

    def task_exists_by_hash(self, content_hash: str) -> bool:
        if not content_hash:
            return False
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT 1 FROM tasks WHERE content_hash = ? LIMIT 1",
                    (content_hash,),
                )
                return cur.fetchone() is not None
            finally:
                conn.close()

    def add_task(self, task: TaskItem, skip_if_duplicate: bool = True) -> Tuple[Optional[int], bool]:
        """새 할 일을 등록하고 (task_id, is_created) 튜플을 반환."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not task.content_hash:
            task.content_hash = compute_content_hash(task.title, task.source_ref, task.description)

        with self._lock:
            if skip_if_duplicate and self.task_exists_by_hash(task.content_hash):
                return None, False

            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO tasks (
                        title, description, source, source_ref, content_hash,
                        due_date, due_time, status, label, priority,
                        recurrence, is_new_scan, notified_at, snooze_until,
                        created_at, updated_at, completed_at,
                        external_key, due_estimated, needs_review, ezq_key, start_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.title.strip(),
                        task.description.strip(),
                        task.source,
                        task.source_ref.strip(),
                        task.content_hash,
                        task.due_date.strip(),
                        task.due_time.strip() or "18:00",
                        task.status,
                        task.label,
                        task.priority,
                        task.recurrence,
                        1 if task.is_new_scan else 0,
                        task.notified_at,
                        task.snooze_until,
                        now_str,
                        now_str,
                        task.completed_at,
                        task.external_key,
                        1 if task.due_estimated else 0,
                        1 if task.needs_review else 0,
                        task.ezq_key,
                        self._normalized_start(task),
                    ),
                )
                conn.commit()
                return cur.lastrowid, True
            finally:
                conn.close()

    def find_task_by_keys(self, keys: List[str]) -> Optional[TaskItem]:
        """원본 식별키(문서번호·메시지 키) 중 하나라도 일치하는 업무를 찾음.

        업무의 키가 아니어도 같은 공문의 다른 번호로 기록해 둔 별칭(workmgmt_docs의 'ALIAS:키')이면 그 업무.
        """
        keys = [k for k in keys if k]
        if not keys:
            return None
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                placeholders = ",".join("?" for _ in keys)
                cur.execute(
                    f"SELECT * FROM tasks WHERE external_key IN ({placeholders}) ORDER BY id LIMIT 1",
                    keys,
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        f"SELECT t.* FROM workmgmt_docs w JOIN tasks t ON t.id = w.task_id"
                        f" WHERE w.doc_key IN ({placeholders}) ORDER BY t.id LIMIT 1",
                        [f"ALIAS:{k}" for k in keys],
                    )
                    row = cur.fetchone()
                return self._row_to_task(row) if row else None
            finally:
                conn.close()

    def record_key_alias(self, key: str, task_id: Optional[int]) -> None:
        """같은 공문의 다른 번호(목록 접수번호 ↔ 본문 시행번호)를 그 업무의 별칭으로 기록."""
        if not key or task_id is None:
            return
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO workmgmt_docs (doc_key, first_seen, title, result, task_id)"
                    " VALUES (?, ?, '', '별칭', ?)",
                    (f"ALIAS:{key}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id),
                )
                conn.commit()
            finally:
                conn.close()

    def find_confirmed_portal_task_by_title(self, title: str, threshold: float = 0.85) -> Optional[TaskItem]:
        """기한이 확정된(공문 본문·PDF로 받은) 최근 30일 미완료 사내포털 업무를 제목으로 찾음.

        배부 목록 번호가 접수번호라 키로 못 찾을 때. 숫자만 다른 제목은 다른 문서 (find_estimated_task_by_title과 같은 규칙).
        """
        target = _normalize_title(title)
        if not target:
            return None
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE due_estimated = 0 AND source = ? AND status != ? AND created_at >= ?",
                    (TaskSource.PORTAL, TaskStatus.COMPLETED, since),
                ).fetchall()
            finally:
                conn.close()
        best, best_ratio = None, threshold
        for row in rows:
            cand = _normalize_title(row["title"])
            if re.findall(r"\d+", cand) != re.findall(r"\d+", target):
                continue
            ratio = difflib.SequenceMatcher(None, cand, target).ratio()
            if ratio >= best_ratio:
                best, best_ratio = row, ratio
        return self._row_to_task(best) if best else None

    def find_estimated_task_by_title(self, title: str, threshold: float = 0.85) -> Optional[TaskItem]:
        """문서번호로 못 찾은 PDF를, 기한 확정을 기다리는(추정 기한) 사내포털 업무와 제목으로 매칭.

        '10월 실적 보고'와 '11월 실적 보고'처럼 숫자만 다른 제목은 다른 문서로 봄.
        """
        target = _normalize_title(title)
        if not target:
            return None
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM tasks WHERE due_estimated = 1 AND source = ? AND status != ?",
                    (TaskSource.PORTAL, TaskStatus.COMPLETED),
                )
                rows = cur.fetchall()
            finally:
                conn.close()
        best, best_ratio = None, threshold
        for row in rows:
            cand = _normalize_title(row["title"])
            if re.findall(r"\d+", cand) != re.findall(r"\d+", target):
                continue
            ratio = difflib.SequenceMatcher(None, cand, target).ratio()
            if ratio >= best_ratio:
                best, best_ratio = row, ratio
        return self._row_to_task(best) if best else None

    def upsert_scanned_task(self, task: TaskItem) -> Tuple[Optional[int], str]:
        """자동 스캔 결과를 원본 식별키 기준으로 등록·갱신.

        반환 상태: created(신규) / updated(기한 등 갱신) / completed(원본에서 완료 처리됨)
                  / unchanged(이미 동일) / skipped(완료 문서라 등록 안 함) / duplicate(내용 중복)
        """
        existing = self.find_task_by_keys([task.external_key] + list(task.match_keys))
        if existing is None and task.source == TaskSource.PORTAL and task.match_keys and not task.due_estimated:
            existing = self.find_estimated_task_by_title(task.title)

        if existing is None:
            if task.status == TaskStatus.COMPLETED:
                return None, "skipped"
            task_id, created = self.add_task(task, skip_if_duplicate=True)
            return task_id, ("created" if created else "duplicate")

        if task.status == TaskStatus.COMPLETED and existing.status != TaskStatus.COMPLETED:
            self.set_task_status(existing.id, TaskStatus.COMPLETED)
            return existing.id, "completed"

        changed = False
        if not task.due_estimated and (
            existing.due_estimated
            or existing.due_date != task.due_date
            or existing.due_time != task.due_time
            or (task.start_date and existing.start_date != task.start_date)  # 기간 없는 재스캔은 갱신 사유가 아님
        ):
            existing.due_date, existing.due_time = task.due_date, task.due_time
            existing.start_date = task.start_date  # 기간 공문이면 시작일도 (시작 > 끝 방지)
            existing.due_estimated = False
            existing.notified_at = None  # 바뀐 기한으로 다시 알림
            changed = True
        if not existing.external_key and task.external_key:
            existing.external_key = task.external_key
            changed = True
        if existing.needs_review and not task.needs_review:
            # 확인 필요였던 쪽지 업무를 공문 PDF 등 확실한 원본이 다시 가리키면 업무로 확정
            existing.needs_review = False
            changed = True
        for part in (p.strip() for p in task.source_ref.split("|")):
            if part and part not in existing.source_ref:
                existing.source_ref = f"{existing.source_ref} | {part}" if existing.source_ref else part
                changed = True
        if not changed:
            return existing.id, "unchanged"
        existing.is_new_scan = True
        self.update_task(existing)
        return existing.id, "updated"

    def update_task(self, task: TaskItem) -> bool:
        """사용자가 자유 편집한 일정 내용을 업데이트."""
        if task.id is None:
            return False
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE tasks SET
                        title = ?,
                        description = ?,
                        source = ?,
                        source_ref = ?,
                        due_date = ?,
                        due_time = ?,
                        status = ?,
                        label = ?,
                        priority = ?,
                        recurrence = ?,
                        is_new_scan = ?,
                        notified_at = ?,
                        snooze_until = ?,
                        updated_at = ?,
                        completed_at = ?,
                        external_key = ?,
                        due_estimated = ?,
                        needs_review = ?,
                        ezq_key = ?,
                        start_date = ?
                    WHERE id = ?
                    """,
                    (
                        task.title.strip(),
                        task.description.strip(),
                        task.source,
                        task.source_ref.strip(),
                        task.due_date.strip(),
                        task.due_time.strip() or "18:00",
                        task.status,
                        task.label,
                        task.priority,
                        task.recurrence,
                        1 if task.is_new_scan else 0,
                        task.notified_at,
                        task.snooze_until,
                        now_str,
                        task.completed_at,
                        task.external_key,
                        1 if task.due_estimated else 0,
                        1 if task.needs_review else 0,
                        task.ezq_key,
                        self._normalized_start(task),
                        task.id,
                    ),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def set_task_status(self, task_id: int, new_status: str) -> Optional[int]:
        """업무 상태 변경. 반복 일정(DAILY/WEEKLY/MONTHLY)이 완료되면 다음 주기 일정을 자동 생성."""
        task = self.get_task(task_id)
        if not task:
            return None

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        was_completed = task.status == TaskStatus.COMPLETED
        self._next_task_created = True
        task.status = new_status
        task.is_new_scan = False
        task.completed_at = now_str if new_status == TaskStatus.COMPLETED else None
        self.update_task(task)

        if (
            new_status == TaskStatus.COMPLETED
            and not was_completed
            and task.recurrence != RecurrenceType.NONE
            and task.is_period
        ):
            # 반복 기간 업무: 같은 길이의 다음 기간 (만료 때 이미 만들었으면 그 업무 id, 지웠으면 None)
            next_id, self._next_task_created = self._create_next_period(task, date.today())
            return next_id

        next_task_id = None
        if (
            new_status == TaskStatus.COMPLETED
            and not was_completed
            and task.recurrence != RecurrenceType.NONE
            and task.due_date
        ):
            try:
                base_date = datetime.strptime(task.due_date, "%Y-%m-%d").date()
                if base_date < date.today():
                    base_date = date.today()
                if task.recurrence == RecurrenceType.DAILY:
                    next_date = base_date + timedelta(days=1)
                elif task.recurrence == RecurrenceType.WEEKLY:
                    next_date = base_date + timedelta(days=7)
                elif task.recurrence == RecurrenceType.MONTHLY:
                    next_date = add_months(base_date, 1)
                else:
                    next_date = None

                if next_date:
                    next_due_str = next_date.strftime("%Y-%m-%d")
                    next_hash = compute_content_hash(
                        task.title, f"{task.source_ref}|recur|{next_due_str}", task.description
                    )
                    next_item = TaskItem(
                        title=task.title,
                        description=task.description,
                        source=task.source,
                        source_ref=task.source_ref,
                        content_hash=next_hash,
                        due_date=next_due_str,
                        due_time=task.due_time,
                        status=TaskStatus.PENDING,
                        label=task.label,
                        priority=task.priority,
                        recurrence=task.recurrence,
                        is_new_scan=False,
                    )
                    next_task_id, _ = self.add_task(next_item, skip_if_duplicate=True)
            except ValueError:
                pass

        return next_task_id

    def next_task_was_created(self) -> bool:
        """마지막 set_task_status가 돌려준 다음 업무를 그때 새로 만들었는지 (False = 만료 때 이미 만든 기간)."""
        return self._next_task_created

    def _get_setting(self, key: str) -> Optional[str]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
                return row["value"] if row else None
            finally:
                conn.close()

    def _set_setting(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value)
                )
                conn.commit()
            finally:
                conn.close()

    def _task_id_by_hash(self, content_hash: str) -> Optional[int]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT id FROM tasks WHERE content_hash = ? ORDER BY id LIMIT 1", (content_hash,)
                ).fetchone()
                return row["id"] if row else None
            finally:
                conn.close()

    def _create_next_period(self, task: TaskItem, today: date) -> Tuple[Optional[int], bool]:
        """반복 기간 업무의 다음 기간 업무를 만들고 (id, 새로 만들었는지) 반환.

        표시는 다음 기간 업무의 해시로 남김 → 어느 원본(사슬의 앞 업무)에서 오든 그 기간은 한 번만 생기고,
        지운 뒤에도 다시 생기지 않음. 이미 있으면 그 업무 id, 지워졌으면 None.
        """
        try:
            start = datetime.strptime(task.start_date, "%Y-%m-%d").date()
            end = datetime.strptime(task.due_date, "%Y-%m-%d").date()
            next_start, next_end = next_period(start, end, task.recurrence, today)
        except ValueError:
            return None, False
        next_due_str = next_end.strftime("%Y-%m-%d")
        next_hash = compute_content_hash(
            task.title, f"{task.source_ref}|recur|{next_due_str}", task.description
        )
        marker = f"period_next:{next_hash}"
        with self._lock:
            if self._get_setting(marker) is not None:
                return self._task_id_by_hash(next_hash), False
            next_item = TaskItem(
                title=task.title,
                description=task.description,
                source=task.source,
                source_ref=task.source_ref,
                content_hash=next_hash,
                due_date=next_due_str,
                due_time=task.due_time,
                status=TaskStatus.PENDING,
                label=task.label,
                priority=task.priority,
                recurrence=task.recurrence,
                is_new_scan=False,
                start_date=next_start.strftime("%Y-%m-%d"),  # 같은 날이면 add_task가 기한 업무로 저장
            )
            next_task_id, created = self.add_task(next_item, skip_if_duplicate=True)
            self._set_setting(marker, "1")
            if created:
                return next_task_id, True
            return self._task_id_by_hash(next_hash), False

    def activate_expired_periods(self, today: Optional[date] = None) -> List[int]:
        """끝날이 지난 미완료 반복 기간 업무마다 다음 기간 업무를 만들고 새 id 목록을 반환.

        원본은 미완료로 남겨 기한경과·이월로 계속 보이게 함 (구글 캘린더 여러 날 일정처럼 다음 기간이 열림).
        """
        today = today or date.today()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status != ? AND recurrence != ? AND start_date != ''
                      AND needs_review = 0 AND due_date != '' AND due_date < ?
                    ORDER BY id
                    """,
                    (TaskStatus.COMPLETED, RecurrenceType.NONE, today.strftime("%Y-%m-%d")),
                ).fetchall()
            finally:
                conn.close()
            created: List[int] = []
            for row in rows:
                task = self._row_to_task(row)
                if not task.is_period:
                    continue
                new_id, is_new = self._create_next_period(task, today)
                if is_new and new_id:
                    created.append(new_id)
            return created

    def delete_task(self, task_id: int) -> bool:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_task(self, task_id: int) -> Optional[TaskItem]:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                row = cur.fetchone()
                return self._row_to_task(row) if row else None
            finally:
                conn.close()

    def mark_scan_reviewed(self, task_id: Optional[int] = None) -> None:
        """새로 스캔된 항목 확인 완료 처리."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                if task_id is not None:
                    cur.execute("UPDATE tasks SET is_new_scan = 0 WHERE id = ?", (task_id,))
                else:
                    cur.execute("UPDATE tasks SET is_new_scan = 0 WHERE is_new_scan = 1")
                conn.commit()
            finally:
                conn.close()

    def snooze_task(self, task_id: int, minutes: int) -> Optional[str]:
        """알림 다시 알림(Snooze) 시각 설정."""
        target_dt = datetime.now() + timedelta(minutes=minutes)
        snooze_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tasks SET snooze_until = ?, notified_at = ? WHERE id = ?",
                    (snooze_str, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id),
                )
                conn.commit()
                return snooze_str
            finally:
                conn.close()

    def snooze_until_tomorrow_morning(self, task_id: int, hour: int = 9, minute: int = 0) -> str:
        """내일 오전 출근 시각(기본 09:00)으로 다시 알림 설정."""
        tomorrow = date.today() + timedelta(days=1)
        target_dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, 0)
        snooze_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tasks SET snooze_until = ?, notified_at = ? WHERE id = ?",
                    (snooze_str, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id),
                )
                conn.commit()
                return snooze_str
            finally:
                conn.close()

    def mark_notified(self, task_id: int) -> None:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tasks SET notified_at = ? WHERE id = ?",
                    (now_str, task_id),
                )
                conn.commit()
            finally:
                conn.close()

    def reschedule_task(self, task_id: int, new_due_date: str) -> Optional[TaskItem]:
        """기한 날짜만 옮김 (끌어다 놓기·미루기). 시각은 유지하고, 이미 보낸 알림과 다시 알림은 초기화.
        기간 업무는 시작일도 같은 날수만큼 옮겨 기간 길이를 유지."""
        new_due = datetime.strptime(new_due_date, "%Y-%m-%d").date()  # 형식이 틀리면 ValueError
        task = self.get_task(task_id)
        if not task:
            return None
        if task.is_period:
            delta = new_due - datetime.strptime(task.due_date, "%Y-%m-%d").date()
            start = datetime.strptime(task.start_date, "%Y-%m-%d").date() + delta
            task.start_date = start.strftime("%Y-%m-%d")
        task.due_date = new_due_date
        task.due_estimated = False
        task.notified_at = None
        task.snooze_until = None
        task.is_new_scan = False
        self.update_task(task)
        return self.get_task(task_id)

    def get_work_stats(self, ref_date: Optional[date] = None, weeks: int = 4) -> Dict[str, object]:
        """처리 실적 요약.

        - 기간별(이번 주 월~일, 이번 달): 새로 등록, 그중 자동 수집(수동 등록 제외), 완료, 기한 내 완료
        - 현재: 미완료, 기한경과, 오늘 마감
        - 최근 N주 주별 완료 건수 (오래된 주 → 이번 주)
        완료 날짜는 completed_at, 등록 날짜는 created_at 기준. 기한 내 완료 = 완료 날짜 <= 마감일.
        """
        today = ref_date or date.today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        periods = {
            "week": (week_start, week_start + timedelta(days=6)),
            "month": (month_start, add_months(month_start, 1) - timedelta(days=1)),
        }
        week_starts = [week_start - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]

        def day_of(ts: Optional[str]) -> Optional[date]:
            try:
                return datetime.strptime((ts or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                return None

        stats: Dict[str, object] = {
            p: {"created": 0, "auto": 0, "completed": 0, "on_time": 0} for p in periods
        }
        weekly = [0] * weeks
        open_count = overdue = due_today = 0
        today_str = today.strftime("%Y-%m-%d")

        for t in self.query_tasks():
            created = day_of(t.created_at)
            done = day_of(t.completed_at) if t.is_completed else None
            for key, (start, end) in periods.items():
                bucket = stats[key]
                if created and start <= created <= end:
                    bucket["created"] += 1
                    if t.source != TaskSource.MANUAL:
                        bucket["auto"] += 1
                if done and start <= done <= end:
                    bucket["completed"] += 1
                    if not t.due_date or done.strftime("%Y-%m-%d") <= t.due_date:
                        bucket["on_time"] += 1
            if done:
                for i, ws in enumerate(week_starts):
                    if ws <= done <= ws + timedelta(days=6):
                        weekly[i] += 1
            if not t.is_completed:
                open_count += 1
                if t.due_date and t.due_date < today_str:
                    overdue += 1
                elif t.due_date == today_str:
                    due_today += 1

        stats["open"] = open_count
        stats["overdue"] = overdue
        stats["due_today"] = due_today
        stats["weekly"] = list(zip(week_starts, weekly))
        stats["ranges"] = periods
        return stats

    def query_tasks(
        self,
        keyword: str = "",
        status_filter: str = "ALL",        # ALL, INCOMPLETE, PENDING, IN_PROGRESS, COMPLETED, OVERDUE, NEW_SCAN
        label_filter: str = "ALL",         # ALL or specific label key
        source_filter: str = "ALL",        # ALL or specific source key
        target_date: Optional[str] = None, # YYYY-MM-DD
        include_rollover_on_today: bool = True,
        year_month: Optional[str] = None,  # YYYY-MM
        needs_review: Optional[bool] = False,
        include_period_span: bool = False,
    ) -> List[TaskItem]:
        """다중 조건 검색 및 미완료 업무 자동 이월(Rollover) 지원 조회.

        include_period_span: True면 target_date가 시작~끝 사이인 기간 업무도 그날 업무로 (오른쪽 날짜 목록용).

        needs_review: 기본(False)은 '확인 필요' 업무를 빼서 캘린더·목록·브리핑·알림·통계에 나오지 않게 함.
        True면 확인 필요 업무만, None이면 모두.
        """
        today_str = date.today().strftime("%Y-%m-%d")
        clauses = []
        params: list = []

        if needs_review is not None:
            clauses.append("needs_review = ?")
            params.append(1 if needs_review else 0)

        if keyword.strip():
            kw = f"%{keyword.strip()}%"
            clauses.append("(title LIKE ? OR description LIKE ? OR source_ref LIKE ?)")
            params.extend([kw, kw, kw])

        if status_filter == "INCOMPLETE":
            clauses.append("status != ?")
            params.append(TaskStatus.COMPLETED)
        elif status_filter == "OVERDUE":
            clauses.append("(status != ? AND due_date != '' AND due_date < ?)")
            params.extend([TaskStatus.COMPLETED, today_str])
        elif status_filter == "NEW_SCAN":
            clauses.append("is_new_scan = 1")
        elif status_filter in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED):
            clauses.append("status = ?")
            params.append(status_filter)

        if label_filter != "ALL":
            clauses.append("label = ?")
            params.append(label_filter)

        if source_filter != "ALL":
            clauses.append("source = ?")
            params.append(source_filter)

        if target_date:
            day_sql, day_params = "due_date = ?", [target_date]
            if include_period_span:
                day_sql = "(due_date = ? OR (start_date != '' AND start_date <= ? AND due_date >= ?))"
                day_params = [target_date, target_date, target_date]
            if include_rollover_on_today and target_date == today_str:
                # 오늘 날짜 선택 시: 오늘 마감 업무 + 과거 기한 경과 미완료 업무(Rollover) 함께 표시
                clauses.append(
                    f"({day_sql} OR (status != ? AND due_date != '' AND due_date < ?))"
                )
                params.extend(day_params + [TaskStatus.COMPLETED, today_str])
            else:
                clauses.append(day_sql)
                params.extend(day_params)

        if year_month:
            clauses.append("due_date LIKE ?")
            params.append(f"{year_month}-%")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM tasks
            {where_sql}
            ORDER BY
                CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END ASC,
                CASE WHEN due_date = '' THEN '9999-12-31' ELSE due_date END ASC,
                priority ASC,
                due_time ASC,
                id DESC
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(sql, params)
                return [self._row_to_task(r) for r in cur.fetchall()]
            finally:
                conn.close()

    def get_briefing_summary(self, ref_date: Optional[date] = None) -> Dict[str, List[TaskItem]]:
        """출근 시 자동 브리핑 팝업용 카테고리별 업무 분류."""
        today = ref_date or date.today()
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        week_later_str = (today + timedelta(days=7)).strftime("%Y-%m-%d")

        all_incomplete = self.query_tasks(status_filter="INCOMPLETE")
        overdue_list: List[TaskItem] = []
        today_list: List[TaskItem] = []
        tomorrow_list: List[TaskItem] = []
        upcoming_week_list: List[TaskItem] = []
        new_scan_list: List[TaskItem] = []

        for t in all_incomplete:
            if t.is_new_scan:
                new_scan_list.append(t)
            if not t.due_date:
                continue
            if t.due_date < today_str:
                overdue_list.append(t)
            elif t.due_date == today_str:
                today_list.append(t)
            elif t.due_date == tomorrow_str:
                tomorrow_list.append(t)
            elif today_str < t.due_date <= week_later_str:
                upcoming_week_list.append(t)

        return {
            "overdue": overdue_list,
            "today": today_list,
            "tomorrow": tomorrow_list,
            "upcoming_week": upcoming_week_list,
            "new_scan": new_scan_list,
        }

    def get_due_tasks_for_reminder(self, now: Optional[datetime] = None) -> List[TaskItem]:
        """현재 시각 기준으로 기한이 임박/도래했거나 Snooze 시각이 도달한 알림 대상 반환."""
        now_dt = now or datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now_dt.strftime("%Y-%m-%d")
        current_hm = now_dt.strftime("%H:%M")

        incomplete = self.query_tasks(status_filter="INCOMPLETE")
        due_items: List[TaskItem] = []

        for t in incomplete:
            # 1) Snooze 설정이 있는 경우: Snooze 시각이 지났을 때만 알림
            if t.snooze_until:
                if t.snooze_until <= now_str:
                    due_items.append(t)
                continue

            # 2) 이미 오늘 알림을 띄운 적이 있으면 중복 팝업 방지 (Snooze 제외)
            if t.notified_at and t.notified_at.startswith(today_str):
                continue

            if not t.due_date:
                continue

            # 3) 기한 경과(Overdue)이거나, 오늘 마감인데 마감 시각 1시간 전~도래 시점인 경우
            if t.due_date < today_str:
                due_items.append(t)
            elif t.due_date == today_str:
                try:
                    due_dt = datetime.strptime(f"{t.due_date} {t.due_time}", "%Y-%m-%d %H:%M")
                    # 마감 2시간 전부터 알림 대상
                    if now_dt >= (due_dt - timedelta(hours=2)):
                        due_items.append(t)
                except ValueError:
                    if current_hm >= "09:00":
                        due_items.append(t)

        return due_items

    def get_scanned_file(self, file_path: str) -> Optional[dict]:
        """이전에 확인한 파일의 기록 {file_mtime, file_hash, extracted_count}. 없으면 None."""
        abs_path = os.path.abspath(file_path)
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT file_mtime, file_hash, extracted_count FROM scanned_files WHERE file_path = ?",
                    (abs_path,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def is_file_already_scanned(self, file_path: str, file_mtime: float, file_hash: str) -> bool:
        abs_path = os.path.abspath(file_path)
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT file_mtime, file_hash FROM scanned_files WHERE file_path = ?",
                    (abs_path,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                return row["file_hash"] == file_hash
            finally:
                conn.close()

    def record_scanned_file(
        self, file_path: str, file_mtime: float, file_hash: str, extracted_count: int
    ) -> None:
        abs_path = os.path.abspath(file_path)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO scanned_files (file_path, file_mtime, file_hash, scanned_at, extracted_count)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        file_mtime = excluded.file_mtime,
                        file_hash = excluded.file_hash,
                        scanned_at = excluded.scanned_at,
                        extracted_count = excluded.extracted_count
                    """,
                    (abs_path, file_mtime, file_hash, now_str, extracted_count),
                )
                conn.commit()
            finally:
                conn.close()

    # ── 업무관리시스템 문서 수집 기록 ─────────────────────────────

    def record_workmgmt_doc(
        self, doc_key: str, first_seen: str, title: str, result: str, task_id: Optional[int] = None
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO workmgmt_docs (doc_key, first_seen, title, result, task_id)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (doc_key, first_seen, title, result, task_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_workmgmt_doc(self, doc_key: str) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM workmgmt_docs WHERE doc_key = ?", (doc_key,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def set_workmgmt_doc_task(self, doc_key: str, task_id: Optional[int], result: str) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("UPDATE workmgmt_docs SET task_id = ?, result = ? WHERE doc_key = ?", (task_id, result, doc_key))
                conn.commit()
            finally:
                conn.close()

    # ── 업무관리시스템 문서 판정 기록 (ezq_messages와 같은 모양) ──────────

    @staticmethod
    def _workmgmt_judgment_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["msg_key"] = d["doc_key"]
        d["reasons"] = [r for r in (d.get("reasons") or "").split("\n") if r]
        return d

    def record_workmgmt_judgment(
        self, doc_key: str, verdict: str, score: int, reasons: List[str], body: str
    ) -> None:
        """문서 판정 결과를 기록. 행이 있으면 판정 열만 갱신(사용자 표시·처음 본 시각 보존), 없으면 새로 만듦."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO workmgmt_docs (doc_key, first_seen, title, verdict, score, reasons, body)"
                    " VALUES (?, ?, '', ?, ?, ?, ?)"
                    " ON CONFLICT(doc_key) DO UPDATE SET verdict = excluded.verdict, score = excluded.score,"
                    " reasons = excluded.reasons, body = excluded.body",
                    (doc_key, now, verdict, score, "\n".join(reasons), body),
                )
                conn.commit()
            finally:
                conn.close()

    def set_workmgmt_user_label(self, doc_key: str, label: str) -> None:
        """사용자 정답 표시 ('업무' / '업무아님' / '' 취소)."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("UPDATE workmgmt_docs SET user_label = ? WHERE doc_key = ?", (label, doc_key))
                conn.commit()
            finally:
                conn.close()

    def get_workmgmt_judgment(self, doc_key: str) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM workmgmt_docs WHERE doc_key = ?", (doc_key,)).fetchone()
                return self._workmgmt_judgment_row(row) if row else None
            finally:
                conn.close()

    def list_workmgmt_judgments(
        self, verdict: Optional[str] = None, user_label: Optional[str] = None, limit: int = 300
    ) -> List[dict]:
        """본문을 판정한 문서 기록 (최신순). 판정 도입 전 기록(본문 없음)과 배부 목록 기록은 제외."""
        sql, args = "SELECT * FROM workmgmt_docs WHERE body != '' AND doc_key NOT LIKE 'LIST:%'", []
        if verdict is not None:
            sql += " AND verdict = ?"
            args.append(verdict)
        if user_label is not None:
            sql += " AND user_label = ?"
            args.append(user_label)
        sql += " ORDER BY first_seen DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            conn = self._get_conn()
            try:
                return [self._workmgmt_judgment_row(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

    def find_workmgmt_doc_by_task(self, task_id: int, prefix: str = "LIST:") -> Optional[dict]:
        """그 업무를 가리키는 업무관리시스템 기록 (기본: 배부 목록 기록)."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM workmgmt_docs WHERE task_id = ? AND doc_key LIKE ? ORDER BY first_seen LIMIT 1",
                    (task_id, f"{prefix}%"),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def find_workmgmt_judgment_by_task(self, task_id: int) -> Optional[dict]:
        """그 업무를 가리키는 문서 판정 기록 (배부 목록·별칭 기록과 판정 없는 기록은 제외)."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM workmgmt_docs WHERE task_id = ? AND doc_key NOT LIKE 'LIST:%'"
                    " AND COALESCE(verdict, '') != '' ORDER BY first_seen LIMIT 1",
                    (task_id,),
                ).fetchone()
                return self._workmgmt_judgment_row(row) if row else None
            finally:
                conn.close()

    def find_workmgmt_list_doc(self, doc_no: str, title: str, threshold: float = 0.85) -> Optional[dict]:
        """배부 목록 기록 찾기: 'LIST:DOC:문서번호' 키, 번호가 다르면(목록은 접수번호) 최근 30일 기록의 제목 유사도.

        제목 비교는 find_estimated_task_by_title과 같은 규칙 (숫자만 다른 제목은 다른 문서).
        """
        if doc_no:
            rec = self.get_workmgmt_doc("LIST:DOC:" + re.sub(r"\s+", "", doc_no))
            if rec is not None:
                return rec
        target = _normalize_title(title)
        if not target:
            return None
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM workmgmt_docs WHERE doc_key LIKE 'LIST:%' AND first_seen >= ?", (since,)
                ).fetchall()
            finally:
                conn.close()
        best, best_ratio = None, threshold
        for row in rows:
            cand = _normalize_title(row["title"])
            if re.findall(r"\d+", cand) != re.findall(r"\d+", target):
                continue
            ratio = difflib.SequenceMatcher(None, cand, target).ratio()
            if ratio >= best_ratio:
                best, best_ratio = row, ratio
        return dict(best) if best else None

    def find_excluded_workmgmt_judgments(self, doc_no: str, title: str, threshold: float = 0.85) -> List[dict]:
        """배부 목록 행과 같은 문서일 수 있는 '제외' 판정 문서 기록 (업무 없음, '업무아님' 표시 안 함, 최근 30일).

        배부 목록보다 먼저 읽혀 제외된 배부 문서를 찾는 후보: 본문에 문서번호가 있는 기록 먼저, 다음은 제목 유사도
        (find_workmgmt_list_doc과 같은 규칙). 같은 문서인지는 호출부가 본문을 다시 읽어 확인.
        """
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM workmgmt_docs WHERE verdict = '제외' AND result = '제외' AND body != ''"
                    " AND task_id IS NULL AND COALESCE(user_label, '') != ? AND doc_key NOT LIKE 'LIST:%' AND first_seen >= ?"
                    " ORDER BY first_seen DESC",
                    (self.EZQ_LABEL_NOT_TASK, since),
                ).fetchall()
            finally:
                conn.close()
        no = re.sub(r"\s+", "", doc_no or "")
        target = _normalize_title(title)
        by_no, by_title = [], []
        for row in rows:
            if no and no in re.sub(r"\s+", "", row["body"]):
                by_no.append(row)
                continue
            cand = _normalize_title(row["title"])
            if not target or not cand or re.findall(r"\d+", cand) != re.findall(r"\d+", target):
                continue
            ratio = difflib.SequenceMatcher(None, cand, target).ratio()
            if ratio >= threshold:
                by_title.append((ratio, row))
        by_title.sort(key=lambda x: -x[0])
        return [self._workmgmt_judgment_row(r) for r in by_no + [r for _ratio, r in by_title]]

    def find_topic_task(self, title: str, source: str, since: str) -> Optional[TaskItem]:
        """기한과 상관없이 주제어로 같은 일 찾기 (배부 목록 업무는 기한이 추정이라 find_similar_open_task로는 못 찾음).

        잘못 합치지 않도록 엄격하게: 양쪽 제목 모두 주제어가 2개 이상이고, 양방향 모두 겹치고,
        since('YYYY-MM-DD HH:MM:SS') 이후에 만든 미완료 업무만.
        """
        if len(topic_words(title)) < 2:
            return None
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE source = ? AND status != ? AND created_at >= ? ORDER BY id",
                    (source, TaskStatus.COMPLETED, since),
                ).fetchall()
            finally:
                conn.close()
        for row in rows:
            cand = self._row_to_task(row)
            if len(topic_words(cand.title)) < 2:
                continue
            if self.find_workmgmt_doc_by_task(cand.id) is not None:
                continue  # 이미 다른 배부 문서와 이어진 쪽지 업무 (후속 공문을 같은 업무로 합치지 않음)
            if shares_topic(title, f"{cand.title}\n{cand.description}") and shares_topic(cand.title, title):
                return cand
        return None

    def fold_task_into(self, keep: TaskItem, drop: TaskItem, official: TaskItem) -> bool:
        """같은 일인 두 업무를 keep 하나로: drop의 출처를 덧붙이고 drop을 지움. 기한·문서번호는 official(공문) 값.

        배부 목록·문서 기록이 drop을 가리키면 keep으로 바꿈. 같은 업무면 False.
        """
        if keep.id is None or drop.id is None or keep.id == drop.id:
            return False
        if official.id != keep.id:
            keep.due_date, keep.due_time, keep.due_estimated = official.due_date, official.due_time, official.due_estimated
            keep.start_date = official.start_date
            keep.notified_at = None
            if official.external_key.startswith("DOC:") and not keep.external_key.startswith("DOC:"):
                keep.external_key = official.external_key
            if not official.needs_review:
                keep.needs_review = False
        for part in (p.strip() for p in drop.source_ref.split("|")):
            if part and part not in keep.source_ref:
                keep.source_ref = f"{keep.source_ref} | {part}" if keep.source_ref else part
        # 지우는 업무에 사용자가 고친 내용(진행 상태·중요도·메모)을 잃지 않게 옮김
        if drop.status == TaskStatus.IN_PROGRESS and keep.status == TaskStatus.PENDING:
            keep.status = TaskStatus.IN_PROGRESS
        keep.priority = min(keep.priority, drop.priority)
        if drop.description and drop.description.strip() not in (keep.description or ""):
            keep.description = f"{keep.description}\n\n── 합친 업무 ──\n{drop.description}" if keep.description else drop.description
        if not keep.ezq_key and drop.ezq_key:
            keep.ezq_key = drop.ezq_key
        elif drop.ezq_key and drop.ezq_key != keep.ezq_key:
            self.set_ezq_merged_task(drop.ezq_key, keep.id)
        keep.is_new_scan = True
        self.update_task(keep)
        self.delete_task(drop.id)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("UPDATE workmgmt_docs SET task_id = ? WHERE task_id = ?", (keep.id, drop.id))
                conn.commit()
            finally:
                conn.close()
        return True

    # ── 같은 일을 다른 경로로 받은 업무 찾기 (공문 ↔ 쪽지) ─────────

    def find_similar_open_task(self, task: TaskItem, source: str) -> Optional[TaskItem]:
        """기한이 같은 날이고 주제어가 겹치는, 다른 경로(source)의 미완료 업무.

        예: 동료가 '감사실 내부통제 자가진단 공문 왔습니다. 11/7까지 제출 부탁' 쪽지를 보낸 뒤
        같은 공문('2026년 내부통제 자가진단 결과 제출 시행 알림', 기한 11.7.)을 업무관리시스템에서 열면 같은 일.
        쪽지에는 문서번호가 없어 키로는 못 찾으므로 기한 + 제목 주제어로 봄. 기한이 추정이면 찾지 않음.
        """
        if task.due_estimated or not task.due_date:
            return None
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE source = ? AND status != ? AND due_estimated = 0 AND due_date = ?"
                    " ORDER BY id",
                    (source, TaskStatus.COMPLETED, task.due_date),
                ).fetchall()
            finally:
                conn.close()
        new_text = f"{task.title}\n{task.description}"
        for row in rows:
            cand = self._row_to_task(row)
            # 반대 방향(상대 제목의 주제어가 이쪽 긴 본문에 있는지)은 주제어가 2개 이상일 때만 (낱말 하나로 잘못 합치지 않게)
            if shares_topic(task.title, f"{cand.title}\n{cand.description}") or (
                len(topic_words(cand.title)) >= 2 and shares_topic(cand.title, new_text)
            ):
                return cand
        return None

    def merge_into(self, existing: TaskItem, task: TaskItem, note: str, clear_review: bool = True) -> bool:
        """task를 새로 만들지 않고 같은 일인 existing 업무에 출처를 덧붙임. 이미 붙어 있으면 False.

        clear_review=False: 쪽지처럼 근거가 약한 쪽이 합쳐질 때는 existing의 '확인 필요'를 풀지 않음.
        """
        if note in existing.source_ref:
            return False
        existing.source_ref = f"{existing.source_ref} | {note}" if existing.source_ref else note
        if clear_review and existing.needs_review and not task.needs_review:
            existing.needs_review = False  # 확실한 원본(공문)이 같은 일을 가리키면 업무로 확정
        existing.is_new_scan = True
        self.update_task(existing)
        return True

    # ── EzQ 쪽지 판정 기록 ──────────────────────────────────────
    EZQ_LABEL_TASK = "업무"
    EZQ_LABEL_NOT_TASK = "업무아님"

    def record_ezq_message(
        self, msg_key: str, first_seen: str, verdict: str, score: int, reasons: List[str], body: str
    ) -> None:
        """처음 판정한 쪽지를 기록. 이미 있으면 그대로 둠 (처음 본 시각·사용자 표시 보존)."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO ezq_messages (msg_key, first_seen, verdict, score, reasons, body)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (msg_key, first_seen, verdict, score, "\n".join(reasons), body),
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _ezq_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["reasons"] = [r for r in (d.get("reasons") or "").split("\n") if r]
        return d

    def get_ezq_message(self, msg_key: str) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM ezq_messages WHERE msg_key = ?", (msg_key,)).fetchone()
                return self._ezq_row(row) if row else None
            finally:
                conn.close()

    def set_ezq_user_label(
        self, msg_key: str, label: str, body_if_missing: str = "", first_seen_if_missing: str = ""
    ) -> None:
        """사용자 정답 표시 ('업무' / '업무아님' / '' 취소). 기록이 없던 쪽지(판정 기록 도입 전 등록)는 새로 만듦.

        first_seen_if_missing: 새로 만들 때 쓸 처음 받은 시각 (없으면 지금). 나중에 '업무로 등록'할 때 기한 기준일이 됨.
        """
        first_seen = first_seen_if_missing or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO ezq_messages (msg_key, first_seen, verdict, body, user_label) VALUES (?, ?, '', ?, ?)"
                    " ON CONFLICT(msg_key) DO UPDATE SET user_label = excluded.user_label",
                    (msg_key, first_seen, body_if_missing, label),
                )
                conn.commit()
            finally:
                conn.close()

    def list_ezq_messages(
        self, verdict: Optional[str] = None, user_label: Optional[str] = None, limit: int = 300
    ) -> List[dict]:
        """최근 판정 기록 (최신순). verdict·user_label로 거를 수 있음."""
        sql, args = "SELECT * FROM ezq_messages WHERE 1=1", []
        if verdict is not None:
            sql += " AND verdict = ?"
            args.append(verdict)
        if user_label is not None:
            sql += " AND user_label = ?"
            args.append(user_label)
        sql += " ORDER BY first_seen DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            conn = self._get_conn()
            try:
                return [self._ezq_row(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

    def count_review_tasks(self) -> int:
        """정리하지 않은 '확인 필요' 업무 수 (완료 제외)."""
        with self._lock:
            conn = self._get_conn()
            try:
                return conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE needs_review = 1 AND status != ?", (TaskStatus.COMPLETED,)
                ).fetchone()[0]
            finally:
                conn.close()

    def confirm_review_task(
        self, task_id: int, new_due: Optional[Tuple[str, str]] = None
    ) -> Optional[TaskItem]:
        """'업무 맞음': 확인 필요를 풀어 캘린더·알림에 나오게 함. 새로 들어온 업무처럼 신규 표시.

        new_due=(날짜, 시각): 기한이 추정값일 때 확인한 날 기준으로 다시 잡은 기한
        (늦게 정리하면 받은 날 기준 추정 기한이 이미 지나 바로 기한경과 알림이 뜨던 문제).
        """
        task = self.get_task(task_id)
        if not task:
            return None
        task.needs_review = False
        task.is_new_scan = True
        if new_due and task.due_estimated:
            task.due_date, task.due_time = new_due
            task.start_date = ""  # 추정 기한 다시 잡기: 기간이 아니라 마감일
            task.notified_at = None
        self.update_task(task)
        return task

    def set_ezq_merged_task(self, msg_key: str, task_id: Optional[int]) -> None:
        """쪽지가 다른 업무에 합쳐졌을 때 그 업무 번호를 쪽지 기록에 남김."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("UPDATE ezq_messages SET merged_task_id = ? WHERE msg_key = ?", (task_id, msg_key))
                conn.commit()
            finally:
                conn.close()

    def find_task_by_ezq_key(self, msg_key: str) -> Optional[TaskItem]:
        """그 EzQ 쪽지에서 나온 업무 (확인 필요 포함). 없으면 None."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE ezq_key = ? OR external_key LIKE ?"
                    " OR id = (SELECT merged_task_id FROM ezq_messages WHERE msg_key = ?) ORDER BY id LIMIT 1",
                    (msg_key, f"EZQ:{msg_key}|%", msg_key),
                ).fetchone()
                return self._row_to_task(row) if row else None
            finally:
                conn.close()
