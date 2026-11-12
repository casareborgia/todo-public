"""업무관리시스템 문서 자동 수집기.

업무관리시스템 문서 창(Edge IE 모드)이 열려 있으면, 웹한글 기안기에서 본문을 받아(collectors/workmgmt_window.py)
공문 PDF와 같은 규칙으로 제목·문서번호(하단 '시행' 번호)·회신기한을 뽑아 등록합니다.
- 문서 창마다 본문은 한 번만 받음: 같은 창·같은 제목이면 다시 꺼내지 않음
- 중복 방지: 문서번호 키(DOC:시행번호)라서 공문 PDF·클립보드 스캔으로 받은 같은 공문과 하나가 됨.
  문서번호가 없으면 본문 기준 키(WEBHWP:…). 한 번 받은 문서는 업무를 지웠어도 다시 등록하지 않음
- 같은 일을 쪽지로 먼저 받았으면(기한이 같고 제목 주제어가 겹침) 새로 만들지 않고 그 업무에 공문 출처를 덧붙임
- 문서 본문은 EzQ 쪽지와 같은 점수표(parsers/message_scorer.py)로 판정:
  등록 / 확인 필요(업무로 만들되 확인 필요) / 제외(업무 안 만듦). 판정 근거는 workmgmt_docs에 남김
- 결재시행 목록의 배부 문서는 제목만 알 수 있어 '확인 필요'로만 두고, 문서를 열면 본문으로 판정함
  (배부 문서의 대부분이 안내·통보 공문이라 목록에 뜨는 대로 등록하면 과탐이 됨, 2026-11-11).
  사용자가 '업무 맞음'으로 확정한 배부 업무는 판정하지 않고 기한만 본문으로 확정
"""

from datetime import date, datetime, timedelta
import hashlib
import re
import sys
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
from parsers.message_scorer import (
    DEADLINE_RE,
    EXCLUDE,
    REGISTER,
    REVIEW,
    ScoreResult,
    has_deadline_task_line,
    has_request,
    score_message,
)
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
MAX_RETRY = 3                # 본문 처리 중 오류가 난 문서 창을 다시 시도하는 횟수
NO_REQUEST_REASON = "요청 표현 없는 문서(통보·증명) → 제외"
LIST_PENDING_REASON = "배부 문서: 문서를 열면 본문으로 업무인지 판정합니다"
NO_DEADLINE_REASON = "기한 없는 문서 → 확인 필요"
# 공문의 통보형 맺음말 ('업무에 활용하시기 바랍니다', '양지하여 주시기 바랍니다'): 할 일을 부탁하는 말이 아님.
# 쪽지 점수표(실사용 표본으로 맞춘 것)는 건드리지 않고 문서 판정에서만 부탁 표현에서 뺌
DOC_NOTICE_ASK_RE = re.compile(r"(?:활용|양지)\s*(?:(?:하여|해|하시기)\s*)?(?:주시기\s*)?바랍니다")


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
        self._fail_count: Dict[Tuple[int, str], int] = {}       # 처리 중 오류가 난 (창, 제목) → 횟수
        self._first_seen: Dict[Tuple[int, str], datetime] = {}  # (창, 제목)을 처음 본 시각
        self.merged: List[TaskItem] = []                         # 최근 확인에서 쪽지 업무에 합친 업무 (화면 표시용)
        self.removed: List[TaskItem] = []                        # 최근 확인에서 본문 판정 '제외'로 지운 배부 업무 (화면 갱신용)
        self.confirmed: List[TaskItem] = []                      # 최근 확인에서 본문 판정으로 등록·기한 확정한 배부 업무
        self._dismissed_judgment: Dict[str, str] = {}            # '업무 아님'으로 지운 배부 기록 키 → 함께 정리한 본문 판정 기록 키 (되돌리기용)
        self._via_list = False                                   # 마지막 _register가 배부 목록 기록과 이어졌는지

    def _skip(self, hwnd: int, title: str) -> bool:
        return self._done.get((hwnd, title), False)

    def judge_document(self, text: str) -> ScoreResult:
        """문서 본문의 업무 점수 판정 (EzQ 쪽지와 같은 점수표).

        문서만: 부탁 표현이 하나도 없으면(통보·증명 공문) 기한·업무 명사가 있어도 제외.
        단 기한과 업무 명사가 같은 줄('검토는 11.7.(금)까지')이면 그 줄이 요청이라 그대로 둠.
        쪽지는 짧은 지시('회의자료 제출 11/5')가 많아 이 규칙을 쓰지 않음.

        문서만: 명시 기한이 없으면 바로 등록하지 않고 확인 필요. 공문은 '…하여 주시기 바랍니다'와 붙임이
        거의 항상 있어서, 쪽지 기준(부탁 + 첨부)으로는 홍보·행사·통보 공문까지 등록됨 (2026-11-11).
        """
        # 통보형 맺음말을 지운 글로 판정 (부탁 점수와 '기한과 부탁이 같은 줄'에도 걸리지 않게:
        # '11.20.까지 유효하오니 업무에 활용하시기 바랍니다'가 등록되던 문제)
        text = DOC_NOTICE_ASK_RE.sub(" ", text)
        result = score_message(text)
        if result.verdict == EXCLUDE:
            return result
        if not has_request(text) and not has_deadline_task_line(text):
            result.verdict = EXCLUDE
            result.reasons.append(NO_REQUEST_REASON)
        elif result.verdict == REGISTER and not DEADLINE_RE.search(text):
            result.verdict = REVIEW
            result.reasons.append(NO_DEADLINE_REASON)
        return result

    def parse_document(
        self, text: str, seen_at: datetime, force: bool = False, judged: Optional[ScoreResult] = None
    ) -> List[TaskItem]:
        """기안기 본문 하나를 업무로 변환 (업무가 아니면 빈 목록). 등록은 하지 않음.

        judged 없이 부르면 지금처럼 단어 판정(is_actionable_text). judged가 있으면 그 판정을 따르고
        '확인 필요'면 needs_review. force=True: 사용자가 '업무로 등록'을 고른 문서 — 판정 무시.
        """
        if judged is not None and judged.verdict == EXCLUDE and not force:
            return []
        items = self.parser.extractor.extract_tasks(
            text,
            source_hint=TaskSource.PORTAL,
            source_ref=WORKMGMT_SOURCE_REF,
            ref_date=seen_at.date(),
            already_judged=judged is not None or force,
        )
        key = doc_key(text)
        for i, item in enumerate(items):
            if judged is not None:
                item.needs_review = judged.verdict == REVIEW and not force
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
        self.removed = []
        self.confirmed = []
        for res in results:
            wkey = (res.hwnd, res.title)
            first = self._first_seen.setdefault(wkey, now)
            if res.status == TEXT and len(res.text.strip()) >= MIN_BODY_CHARS:
                self._done[wkey] = True
                try:
                    created += self._handle_text(res, now)
                except Exception as e:  # noqa: BLE001 - 한 문서의 오류로 다른 창·목록 수집을 멈추지 않음
                    # 업무가 안 생겼는데 '본문 받음'으로 굳어 이번 실행 내내 건너뛰던 문제: 몇 번은 다시 시도
                    fails = self._fail_count[wkey] = self._fail_count.get(wkey, 0) + 1
                    print(f"[업무관리] 문서 처리 오류 ({fails}회): {e!r}", file=sys.stderr)
                    if fails < MAX_RETRY:
                        self._done.pop(wkey, None)
            elif res.status in (NOCTRL, ERR, NOJS) and now - first > timedelta(seconds=GIVE_UP_SECONDS):
                self._done[wkey] = True  # 목록 화면 등 기안기가 없는 창: 더 확인하지 않음 (제목이 바뀌면 다시)
        created += self._collect_list(now)
        return created

    # ── 결재시행 목록의 배부받은 문서 ─────────────────────────────

    def row_to_task(self, row: ListRow, kind: str, seen_at: datetime) -> TaskItem:
        """배부 목록 행 → 추정 기한의 '확인 필요' 업무 (등록은 하지 않음). kind: 'task' / 'review'(업무관리자).

        목록에는 제목뿐이라 업무인지 알 수 없음: 업무담당자 배부도 확인 필요로 두고 본문을 읽을 때 판정."""
        ref = received_date(row.received_at) or seen_at.date()
        due_date, due_time, _est = self.parser.extractor.extract_due("", ref_date=ref)
        key = f"DOC:{row.doc_no}" if row.doc_no else f"LISTTITLE:{doc_key(row.title)}"
        lines = [
            f"[업무관리시스템 배부 {seen_at:%Y-%m-%d %H:%M} 확인] 문서를 열면 본문으로 업무인지 판정하고 기한을 정합니다",
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
            needs_review=True,
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
                    # (기한 없는 문서는 확인 필요·추정 기한으로 들어와 있으므로 문서 창 업무는 추정 기한도 찾음)
                    portal = self.db.find_confirmed_portal_task_by_title(row.title, or_source_ref_contains=WORKMGMT_SOURCE_REF)
                    if portal is not None:
                        self.db.merge_into(portal, item, note, clear_review=False)
                        self.db.record_key_alias(item.external_key, portal.id)
                        self.db.record_workmgmt_doc(list_key, stamp, row.title, "이미 있음", portal.id)
                        continue
                    msg_task = self.db.find_topic_task(row.title, TaskSource.MESSENGER, since)
                    if msg_task is not None:
                        # 같은 일을 쪽지로 먼저 받음: 새로 만들지 않고 쪽지 업무에 배부 출처를 붙임.
                        # 배부만으로는 업무인지 알 수 없어 쪽지의 확인 필요는 그대로 둠 (본문을 읽으면 정해짐)
                        self.db.merge_into(msg_task, item, note, clear_review=False)
                        self.merged.append(msg_task)
                        self.db.record_workmgmt_doc(list_key, stamp, row.title, "쪽지 업무에 합침", msg_task.id)
                        continue
                    # 목록보다 먼저 문서 창에서 읽혀 '제외'로 판정된 같은 문서: 배부 업무를 만들지 않음
                    self.db.record_workmgmt_doc(list_key, stamp, row.title, REVIEW)
                    if self._already_excluded(row, list_key):
                        self.db.set_workmgmt_doc_task(list_key, None, EXCLUDE)
                        continue
                task_id, action = self.db.upsert_scanned_task(item)
                if action == "created" and task_id is not None:
                    item.id = task_id
                    created.append(item)
                    result = REVIEW
                else:
                    result = "이미 있음"
                self.db.record_workmgmt_doc(list_key, stamp, row.title, result, task_id)
                self.db.set_workmgmt_doc_task(list_key, task_id, result)
        return created

    def _already_excluded(self, row: ListRow, list_key: str) -> bool:
        """배부 목록 행과 같은 문서가 이미 본문 판정으로 '제외'됐는지 (문서 창을 목록보다 먼저 본 경우).

        같은 문서인지는 본문에서 뽑은 문서번호·제목이 이 배부 기록으로 이어지는지로 확인 (_handle_text와 같은 규칙)."""
        for rec in self.db.find_excluded_workmgmt_judgments(row.doc_no, row.title):
            seen_at = datetime.strptime(rec["first_seen"], "%Y-%m-%d %H:%M:%S")
            items = self.parse_document(rec["body"], seen_at, force=True)
            linked = self._list_record(items[0]) if items else None
            if linked and linked.get("doc_key") == list_key:
                return True
        return False

    def _handle_text(self, res: DocWindowResult, now: datetime) -> List[TaskItem]:
        key = doc_key(res.text)
        if self.db.get_workmgmt_doc(key) is not None:
            return []  # 이미 받은 문서 (지운 업무를 다시 만들지 않음)
        stamp = f"{now:%Y-%m-%d %H:%M:%S}"
        judged = self.judge_document(res.text)
        # 배부 목록 기록과 이어지는 문서인지 (단어 판정과 상관없이 제목·문서번호만 뽑아 찾음)
        probe = self.parse_document(res.text, now, force=True)
        rec = self._list_record(probe[0]) if probe else None
        linked = self._list_linked_task(rec)
        if rec is not None and rec.get("result") == LIST_DISMISSED:
            # 사용자가 배부 건을 '업무 아님'으로 지움: 문서를 열어도 다시 만들지 않음
            self.db.record_workmgmt_doc(key, stamp, probe[0].title, "업무 아님(배부 목록)")
            return []
        if rec is not None and linked is None and rec.get("task_id"):
            # 배부 업무를 사용자가 지웠거나 이미 완료함: 문서를 열어도 새 업무로 되살리지 않음
            self.db.record_workmgmt_doc(key, stamp, probe[0].title, "이미 처리")
            return []
        if linked is not None and linked.needs_review:
            # 아직 확인 필요인 배부 업무: 본문으로 판정 (등록 / 확인 필요 유지 / 제외)
            return self._judge_list_document(key, stamp, res.text, now, judged, rec, linked, probe)
        if linked is not None:
            # 사용자가 '업무 맞음'으로 확정한 배부 업무: 점수 판정 없이 본문 기한만 맞춤
            self._confirm_with_document(linked, probe[0], self._doc_note(probe[0]))
            merged_before = len(self.merged)
            self._fold_messenger_duplicate(linked)
            self.db.record_workmgmt_doc(key, stamp, probe[0].title, "등록")
            task_id = self._existing_task_id([linked], [], self.merged[merged_before:])
            if task_id is not None:
                self.db.set_workmgmt_doc_task(key, task_id, "등록")
                kept = self.db.get_task(task_id)
                if kept is not None:
                    self.confirmed.append(kept)  # 화면 갱신용
            return []
        if judged.verdict == EXCLUDE:
            title = self.parser.extractor.extract_title(res.text) or res.title
            self.db.record_workmgmt_doc(key, stamp, title, EXCLUDE)
            self._record_judgment(key, judged, res.text)
            return []
        items = self.parse_document(res.text, now, judged=judged)
        merged_before = len(self.merged)
        created, result, via_list = self._register_items(items, keep_review=(judged.verdict == REVIEW))
        title = items[0].title if items else (self.parser.extractor.extract_title(res.text) or res.title)
        if via_list:
            # 단어 판정에선 빠졌지만 판정 후 만든 업무가 배부 기록과 이어짐: 배부 문서로 보고 판정 기록 안 남김
            self.db.record_workmgmt_doc(key, stamp, title, result)
            return created
        if judged.verdict == REVIEW:
            result = REVIEW
        self.db.record_workmgmt_doc(key, stamp, title, result)
        self._record_judgment(key, judged, res.text)
        # 새 업무가 쪽지 업무와 하나로 합쳐져 지워졌으면 남은 업무 번호 (지워진 번호는 남기지 않음)
        folded = self.merged[merged_before:]
        task_id = self._existing_task_id(created, [], folded) if (created or folded) else None
        if task_id is not None:
            self.db.set_workmgmt_doc_task(key, task_id, result)
        return created

    def _judge_list_document(
        self, key: str, stamp: str, text: str, now: datetime, judged: ScoreResult, rec: dict, linked: TaskItem,
        probe: List[TaskItem],
    ) -> List[TaskItem]:
        """확인 필요로 둔 배부 업무(linked)를 문서 본문 판정대로 정리. 새 업무는 만들지 않음."""
        title = probe[0].title
        if judged.verdict == EXCLUDE:
            self.db.record_workmgmt_doc(key, stamp, title, EXCLUDE)
            self._record_judgment(key, judged, text)
            # 목록만으로 만든 업무는 지움 ('제외된 쪽지·문서'에서 되살릴 수 있음).
            # 쪽지 업무에 배부 출처를 붙인 경우는 쪽지가 따로 판정된 것이라 그대로 둠
            # 쪽지 업무는 남기되 배부 기록과의 연결은 끊어, 다시 쪽지 업무로만 정리되게 함
            self.db.set_workmgmt_doc_task(rec["doc_key"], None, EXCLUDE)
            if (linked.source_ref or "").startswith(WORKMGMT_LIST_SOURCE_REF):
                self.db.delete_task(linked.id)
                self.removed.append(linked)
            return []
        items = self.parse_document(text, now, judged=judged) or probe
        review = judged.verdict == REVIEW
        self._confirm_with_document(linked, items[0], self._doc_note(items[0]), keep_review=review)
        merged_before = len(self.merged)
        if not review:
            self._fold_messenger_duplicate(linked)
        result = REVIEW if review else "등록"
        self.db.record_workmgmt_doc(key, stamp, title, result)
        self._record_judgment(key, judged, text)
        task_id = self._existing_task_id([linked], [], self.merged[merged_before:])
        if task_id is not None:
            self.db.set_workmgmt_doc_task(key, task_id, result)
            kept = self.db.get_task(task_id)
            if kept is not None:
                self.confirmed.append(kept)
        return []

    def _register_items(self, items: List[TaskItem], keep_review: bool = False) -> Tuple[List[TaskItem], str, bool]:
        """업무들을 등록 → (새로 만든 업무, 결과 글자, 배부 기록과 이어졌는지). 결과 글자는 예전 그대로."""
        result = "업무 아님" if not items else "등록"
        created: List[TaskItem] = []
        via_list = False
        for item in items:
            outcome = self._register(item, keep_review)
            via_list = via_list or self._via_list
            if outcome == "created":
                created.append(item)
            elif outcome == "merged":
                result = "쪽지 업무에 합침"
            elif outcome == "dismissed":
                result = "업무 아님(배부 목록)"
        return created, result, via_list

    def _record_judgment(self, key: str, judged: ScoreResult, text: str) -> None:
        self.db.record_workmgmt_judgment(key, judged.verdict, judged.score, list(judged.reasons), text.strip())

    @staticmethod
    def _doc_note(item: TaskItem) -> str:
        return f"{WORKMGMT_SOURCE_REF} {item.external_key.replace('DOC:', '문서번호 ')}"

    def _register(self, item: TaskItem, keep_review: bool = False) -> str:
        note = self._doc_note(item)
        self._via_list = False
        if self.db.find_task_by_keys([item.external_key] + list(item.match_keys)) is None:
            rec = self._list_record(item)
            if rec and rec.get("result") == LIST_DISMISSED:
                self._via_list = True
                return "dismissed"  # 사용자가 배부 건을 '업무 아님'으로 지움: 문서를 열어도 다시 만들지 않음
            linked = self._list_linked_task(rec)
            if linked is not None:
                self._via_list = True
                # 배부 목록으로 먼저 만든(또는 쪽지 업무에 합친) 업무: 본문의 기한으로 확정
                self._confirm_with_document(linked, item, note, keep_review=keep_review and linked.needs_review)
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
            if action == "created" and self.db.get_task(task_id) is None:
                return "merged"  # 먼저 있던 쪽지 업무에 합쳐져 새 업무는 지워짐: 새로 등록한 것으로 세지 않음
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

    # ── 확인 필요 정리 창: 배부 건·판정 문서 '업무 아님' / 되돌리기 ─────────────

    def is_list_task(self, task: TaskItem) -> bool:
        """배부 목록 기록이 가리키는 업무 (쪽지 업무에 합친 경우 포함: 공식 배부 건이라 배부 쪽에서 정리)."""
        return task.id is not None and self.db.find_workmgmt_doc_by_task(task.id) is not None

    def is_doc_task(self, task: TaskItem) -> bool:
        """점수표로 판정한 문서 기록이 가리키는 업무 (배부 문서는 판정하지 않아 해당 없음)."""
        return task.id is not None and self.db.find_workmgmt_judgment_by_task(task.id) is not None

    def review_reason(self, task: TaskItem) -> str:
        """확인 필요 이유. 판정 문서 업무는 판정 근거 목록만 (접두어는 호출부가 붙임)."""
        if task.id is not None and not self.is_list_task(task):
            rec = self.db.find_workmgmt_judgment_by_task(task.id)
            if rec is not None:
                return ", ".join(rec["reasons"])
        if task.id is not None:
            rec = self.db.find_workmgmt_judgment_by_task(task.id)
            if rec is not None:
                return ", ".join(rec["reasons"])  # 배부 문서를 열어 본문도 애매하다고 판정된 경우
        m = re.search(r"^처리구분: (.+)$", task.description or "", re.MULTILINE)
        role = m.group(1).strip() if m else "업무관리자"
        return f"{LIST_PENDING_REASON} (처리구분: {role})"

    def register_as_task(self, key: str) -> List[TaskItem]:
        """'제외된 문서'에서 사용자가 업무라고 고른 문서를 등록 (기한 기준일은 처음 본 시각).

        새로 만든 업무를 돌려줌. 배부 목록 업무·쪽지 업무와 이어지면 새 업무 없이 그 업무를 기록에 남김.
        """
        rec = self.db.get_workmgmt_judgment(key)
        if not rec or not rec.get("body"):
            return []
        seen_at = datetime.strptime(rec["first_seen"], "%Y-%m-%d %H:%M:%S")
        items = self.parse_document(rec["body"], seen_at, force=True)
        merged_before = len(self.merged)
        created, _result, _via_list = self._register_items(items)
        task_id = self._existing_task_id(created, items, self.merged[merged_before:])
        if task_id is not None:
            # 실제로 업무가 있을 때만 정답 표시 (EzQ와 같은 규칙)
            self.db.set_workmgmt_user_label(key, DatabaseManager.EZQ_LABEL_TASK)
            self.db.set_workmgmt_doc_task(key, task_id, "등록")
        return created

    def _existing_task_id(self, created: List[TaskItem], items: List[TaskItem], merged: List[TaskItem]) -> Optional[int]:
        """등록 뒤 그 문서의 업무로 볼 지금 있는 업무 번호 (새 업무 → 같은 키(배부 연결 포함) → 합친 쪽지 업무)."""
        candidates = [item.id for item in created]
        for item in items:
            found = self.db.find_task_by_keys([item.external_key] + list(item.match_keys))
            candidates.append(found.id if found else None)
        candidates += [task.id for task in reversed(merged)]
        for task_id in candidates:
            if task_id is not None and self.db.get_task(task_id) is not None:
                return task_id  # 쪽지 업무와 하나로 합치며 지워진 업무는 건너뜀
        return None

    def has_task(self, key: str) -> bool:
        """그 문서 기록이 가리키는 업무가 지금 있는지. 없으면 '업무로 등록'으로 되살릴 수 있음."""
        rec = self.db.get_workmgmt_doc(key)
        return bool(rec and rec.get("task_id")) and self.db.get_task(rec["task_id"]) is not None

    def confirm_review(self, task: TaskItem) -> Optional[TaskItem]:
        """'업무 맞음': 확인 필요를 풀고 문서 기록에 '업무' 정답 표시 (EzQ와 같음)."""
        if task.id is None:
            return None
        # 추정 기한은 확인한 날 기준으로 다시 잡음 (명시 기한은 confirm_review_task가 그대로 둠)
        due_date, due_time, _est = self.parser.extractor.extract_due("", ref_date=self.clock().date())
        rec = self.db.find_workmgmt_judgment_by_task(task.id)
        confirmed = self.db.confirm_review_task(task.id, new_due=(due_date, due_time))
        if confirmed and rec is not None:
            self.db.set_workmgmt_user_label(rec["doc_key"], DatabaseManager.EZQ_LABEL_TASK)
        return confirmed

    def dismiss_task(self, task: TaskItem) -> Optional[str]:
        """'업무 아님': 기록에 남기고 업무를 지움. 다시 목록에 나오거나 문서를 열어도 만들지 않음.
        되돌리기용 기록 키를 돌려줌 (배부·판정 문서 업무가 아니면 None)."""
        if task.id is None:
            return None
        if self.is_list_task(task):
            rec = self.db.find_workmgmt_doc_by_task(task.id)
            if rec is None:
                return None
            judged_rec = self.db.find_workmgmt_judgment_by_task(task.id)
            if judged_rec is not None:
                # 문서를 열어 본문 판정 기록도 이 업무를 가리키는 경우: 그 기록도 같이 정리
                self.db.set_workmgmt_user_label(judged_rec["doc_key"], DatabaseManager.EZQ_LABEL_NOT_TASK)
                self.db.set_workmgmt_doc_task(judged_rec["doc_key"], None, LIST_DISMISSED)
                self._dismissed_judgment[rec["doc_key"]] = judged_rec["doc_key"]
            self.db.set_workmgmt_doc_task(rec["doc_key"], None, LIST_DISMISSED)
            self.db.delete_task(task.id)
            return rec["doc_key"]
        rec = self.db.find_workmgmt_judgment_by_task(task.id)
        if rec is None:
            return None
        self.db.set_workmgmt_user_label(rec["doc_key"], DatabaseManager.EZQ_LABEL_NOT_TASK)
        self.db.set_workmgmt_doc_task(rec["doc_key"], None, LIST_DISMISSED)
        self.db.delete_task(task.id)
        return rec["doc_key"]

    def undo_dismiss(self, task: TaskItem, list_key: str) -> Optional[int]:
        """'업무 아님' 되돌리기: 지운 업무를 그대로 다시 넣고 기록을 되돌림 (판정 문서는 정답 표시도 지움)."""
        task.id = None
        task_id, _created = self.db.add_task(task, skip_if_duplicate=False)
        result = "확인 필요" if task.needs_review else "등록"
        if not list_key.startswith(LIST_KEY_PREFIX):
            self.db.set_workmgmt_user_label(list_key, "")
        judged_key = self._dismissed_judgment.pop(list_key, None)
        if judged_key:
            self.db.set_workmgmt_user_label(judged_key, "")
            self.db.set_workmgmt_doc_task(judged_key, task_id, result)
        self.db.set_workmgmt_doc_task(list_key, task_id, result)
        task.id = task_id
        return task_id

    def _confirm_with_document(self, task: TaskItem, item: TaskItem, note: str, keep_review: bool = False) -> None:
        if not item.due_estimated:
            task.due_date, task.due_time, task.due_estimated = item.due_date, item.due_time, False
            task.start_date = item.start_date  # 기간 공문이면 시작일도 (시작 > 끝 방지)
            task.notified_at = None  # 바뀐 기한으로 다시 알림
        if not task.external_key.startswith("DOC:") and item.external_key.startswith("DOC:"):
            task.external_key = item.external_key
        elif item.external_key.startswith("DOC:") and item.external_key != task.external_key:
            # 목록 번호(접수번호)와 본문 시행번호가 다름: 나중에 같은 공문 PDF·클립보드가 와도 이 업무로
            self.db.record_key_alias(item.external_key, task.id)
        if not keep_review:
            task.needs_review = False  # 공문 본문이 업무로 판정됨 (keep_review: 본문도 애매해 확인 필요 유지)
        if note not in task.source_ref:
            task.source_ref = f"{task.source_ref} | {note}" if task.source_ref else note
        # 공문 본문을 설명 뒤에 덧붙임 (배부 목록으로 만든 업무에는 제목뿐이라 상세 화면에 본문이 안 보였음).
        # 앞이 아니라 뒤: 쪽지 업무는 설명 첫 줄('[EzQ 쪽지 …')로 쪽지를 찾음
        if item.description and WORKMGMT_DESC_PREFIX not in (task.description or ""):
            task.description = f"{task.description}\n\n{item.description}" if task.description else item.description
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
