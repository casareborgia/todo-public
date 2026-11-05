"""사용자 저장 폴더 자동 감시(Watch Folder) 어댑터.

자동저장 기능이 없는 사내포털/메신저 환경에서 사용자가 문서를 저장하거나
내보내기 한 폴더(기본: ./scan_inbox)를 주기적으로 스캔하여
새로 추가되거나 수정된 파일에서 배부 문서·메시지를 추출하고 캘린더 DB에 자동 입력합니다.

메신저 쪽지 저장 폴더나 PDF 다운로드 폴더처럼 다른 파일이 섞인 폴더도 감시할 수 있도록
폴더마다 '업무 파일만(쪽지 txt·공문 PDF)'과 '이 날짜 이후 저장된 파일만' 옵션을 둡니다.
"""

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Union

from collectors.base_collector import BaseCollector
from core.database import DatabaseManager
from core.models import TaskItem
from parsers.portal_parser import PortalAndMessengerParser, is_work_file_name

DEFAULT_INBOX = "./scan_inbox"

# 파일별 처리 결과 (결과 목록 창에 그대로 표시)
OUTCOME_CREATED = "새 업무 등록"
OUTCOME_UPDATED = "기존 업무 갱신"
OUTCOME_KNOWN = "이미 등록됨"
OUTCOME_DONE_DOC = "완료된 문서 (등록 안 함)"
OUTCOME_NOT_TASK = "업무 아님"
OUTCOME_UNCHANGED = "이미 확인한 파일"
OUTCOME_FAILED = "읽기 실패"
EXCLUDED_PREFIX = "제외: "


@dataclass
class WatchFolder:
    path: str
    work_files_only: bool = False  # True: 메신저 쪽지 txt와 공문 PDF만 처리
    since: str = ""  # 'YYYY-MM-DD': 이 날짜 이전에 저장된 파일은 무시 (빈 값: 전체)

    @classmethod
    def from_config(cls, entry: Union[str, dict, "WatchFolder"]) -> "WatchFolder":
        if isinstance(entry, WatchFolder):
            return entry
        if isinstance(entry, dict):
            return cls(
                path=str(entry.get("path", "")),
                work_files_only=bool(entry.get("work_files_only", False)),
                since=str(entry.get("since") or ""),
            )
        return cls(path=str(entry))

    def to_config(self) -> Union[str, dict]:
        """옵션이 없으면 예전 형식(경로 문자열)으로 저장해 이전 버전과 호환."""
        if not self.work_files_only and not self.since:
            return self.path
        return {"path": self.path, "work_files_only": self.work_files_only, "since": self.since}

    def since_timestamp(self) -> Optional[float]:
        try:
            return datetime.strptime(self.since, "%Y-%m-%d").timestamp() if self.since else None
        except ValueError:
            return None


@dataclass
class FileScanResult:
    path: str
    outcome: str
    items: List[TaskItem] = field(default_factory=list)  # 파일에서 찾은 업무
    created: List[TaskItem] = field(default_factory=list)  # 그중 새로 등록된 업무

    @property
    def excluded(self) -> bool:
        return self.outcome.startswith(EXCLUDED_PREFIX) or self.outcome == OUTCOME_UNCHANGED


class FolderWatcherCollector(BaseCollector):
    def __init__(
        self,
        db: DatabaseManager,
        parser: PortalAndMessengerParser,
        watch_folders: Optional[List[Union[str, dict, WatchFolder]]] = None,
        supported_extensions: Optional[List[str]] = None,
    ):
        self.db = db
        self.parser = parser
        self.folders: List[WatchFolder] = [
            WatchFolder.from_config(f) for f in (watch_folders or [DEFAULT_INBOX])
        ]
        self.supported_extensions = [
            ext.lower()
            for ext in (
                # 업무관리시스템 CSV 목록에는 할 일로 쓸 정보가 없어 기본 제외 (config에 ".csv"를 넣으면 사용)
                supported_extensions
                or [".txt", ".html", ".htm", ".md", ".log", ".json", ".pdf"]
            )
        ]
        self._ensure_folders()

    @property
    def watch_folders(self) -> List[str]:
        return [f.path for f in self.folders]

    def set_folders(self, folders: List[WatchFolder]) -> None:
        self.folders = list(folders)
        self._ensure_folders()

    def _ensure_folders(self) -> None:
        # 프로그램 폴더 안의 상대 경로(scan_inbox 등)만 만들어 둠.
        # 메신저 저장 폴더 같은 외부 경로는 네트워크 드라이브가 잠시 끊긴 경우 등에 엉뚱하게 만들지 않음
        for folder in self.folders:
            if folder.path and not os.path.isabs(folder.path):
                try:
                    os.makedirs(folder.path, exist_ok=True)
                except OSError:
                    pass

    def add_watch_folder(self, folder_path: str) -> None:
        if folder_path and folder_path not in self.watch_folders:
            self.folders.append(WatchFolder(folder_path))
            self._ensure_folders()

    def _compute_file_hash(self, file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def scan_file(
        self, file_path: str, force: bool = False, folder: Optional[WatchFolder] = None
    ) -> FileScanResult:
        """파일 하나를 확인해 업무를 등록하고, 무엇을 했는지 결과로 돌려줌."""
        if not os.path.isfile(file_path):
            return FileScanResult(file_path, EXCLUDED_PREFIX + "파일 없음")
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.supported_extensions:
            return FileScanResult(file_path, EXCLUDED_PREFIX + "지원하지 않는 형식")
        work_only = bool(folder and folder.work_files_only)
        if work_only and not is_work_file_name(file_path):
            return FileScanResult(file_path, EXCLUDED_PREFIX + "쪽지·공문 파일 아님")

        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            return FileScanResult(file_path, OUTCOME_FAILED)
        since = folder.since_timestamp() if folder else None
        if since is not None and mtime < since:
            return FileScanResult(file_path, EXCLUDED_PREFIX + f"{folder.since} 이전 파일")

        # 수정 시각이 같으면 내용을 다시 읽지 않음 (큰 폴더를 15초마다 확인해도 PC가 느려지지 않게)
        previous = None if force else self.db.get_scanned_file(file_path)
        if previous and previous["file_mtime"] == mtime:
            return FileScanResult(file_path, OUTCOME_UNCHANGED)
        try:
            f_hash = self._compute_file_hash(file_path)
        except OSError:
            return FileScanResult(file_path, OUTCOME_FAILED)
        if previous and previous["file_hash"] == f_hash:
            # 내용은 같고 시각만 바뀜 (다시 저장·복사): 시각만 기록해 다음부터 건너뜀
            self.db.record_scanned_file(file_path, mtime, f_hash, previous["extracted_count"] or 0)
            return FileScanResult(file_path, OUTCOME_UNCHANGED)

        try:
            items = self.parser.parse_file(file_path, work_files_only=work_only)
        except Exception:
            return FileScanResult(file_path, OUTCOME_FAILED)

        # 문서번호·메시지 키 기준으로 신규 등록 / 기한 갱신 / 완료 반영 (같은 공문을 다시 받아도 중복 없음)
        created: List[TaskItem] = []
        actions: List[str] = []
        for item in items:
            task_id, action = self.db.upsert_scanned_task(item)
            actions.append(action)
            if action == "created" and task_id is not None:
                item.id = task_id
                created.append(item)

        self.db.record_scanned_file(file_path, mtime, f_hash, len(created))
        return FileScanResult(file_path, _outcome(items, actions, created), items, created)

    def scan_single_file(self, file_path: str, force: bool = False) -> List[TaskItem]:
        """단일 파일을 파싱하여 신규 업무를 DB에 등록 (새로 등록된 업무만 반환)."""
        return self.scan_file(file_path, force=force).created

    def scan_folder(self, folder: WatchFolder, force: bool = False) -> List[FileScanResult]:
        results: List[FileScanResult] = []
        if not os.path.isdir(folder.path):
            return results
        for root_dir, _, files in os.walk(folder.path):
            for fname in sorted(files):
                # 안내용 README 파일과 .gitkeep 같은 숨김 파일은 스캔에서 제외
                if fname.lower().startswith("readme") or fname.startswith("."):
                    continue
                results.append(self.scan_file(os.path.join(root_dir, fname), force=force, folder=folder))
        return results

    def scan_all(self, force: bool = False) -> List[FileScanResult]:
        self._ensure_folders()
        return [r for folder in self.folders for r in self.scan_folder(folder, force=force)]

    def collect_and_register(self, force: bool = False) -> List[TaskItem]:
        """감시 대상 폴더 내의 모든 지원 파일을 스캔하여 신규 업무를 등록."""
        return [t for r in self.scan_all(force=force) for t in r.created]


def _outcome(items: List[TaskItem], actions: List[str], created: List[TaskItem]) -> str:
    if created:
        return OUTCOME_CREATED
    if any(a in ("updated", "completed") for a in actions):
        return OUTCOME_UPDATED
    if not items:
        return OUTCOME_NOT_TASK
    if all(a == "skipped" for a in actions):
        return OUTCOME_DONE_DOC
    return OUTCOME_KNOWN
