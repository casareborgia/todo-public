"""사내 개인용 투두리스트 & 캘린더 매니저 진입점 (main.py).

특징:
- 외부 네트워크 통신이 일체 없는 100% 로컬 오프라인 데스크톱 프로그램
- Python 기본 내장 표준 라이브러리(tkinter, sqlite3, re, ctypes 등)만으로 동작하여
  공공기관/정부기관 사내 보안솔루션 충돌 및 반입 승인 문제를 최소화
"""

import json
import os
import sys

# 실행 경로를 main.py(또는 패키징된 .exe)가 위치한 폴더 기준으로 고정
# PyInstaller 빌드 시 __file__은 _internal 폴더를 가리키므로 exe 폴더를 사용해야
# config.json / todo_data.db / scan_inbox가 exe 옆에 위치함
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from collectors.clipboard_collector import ClipboardCollector
from collectors.ezq_collector import EzqMessageCollector
from collectors.workmgmt_collector import WorkMgmtDocCollector
from collectors.folder_watcher import FolderWatcherCollector
from core.database import DatabaseManager
from parsers.portal_parser import PortalAndMessengerParser
from parsers.rule_parser import RuleBasedExtractor


def load_config(config_path: str = "config.json") -> dict:
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def set_windows_dpi_awareness() -> None:
    """Windows 고해상도 모니터에서 글자가 흐려지지 않도록 DPI 설정 적용."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes

                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def main() -> None:
    set_windows_dpi_awareness()
    config_path = os.path.join(BASE_DIR, "config.json")
    config = load_config(config_path)
    db = DatabaseManager(os.path.join(BASE_DIR, "todo_data.db"))
    extractor = RuleBasedExtractor(config)
    parser = PortalAndMessengerParser(extractor)

    watch_folders = config.get("watch_folders", ["./scan_inbox"])
    supported_exts = config.get(
        "supported_extensions",
        [".txt", ".html", ".htm", ".md", ".log", ".json", ".pdf"],
    )

    folder_collector = FolderWatcherCollector(
        db=db,
        parser=parser,
        watch_folders=watch_folders,
        supported_extensions=supported_exts,
    )
    clipboard_collector = ClipboardCollector(db=db, parser=parser)
    ezq_collector = EzqMessageCollector(
        db=db,
        parser=parser,
        window_class=config.get("ezq_window_class", "TfrmReceiveMessage"),
        enabled=bool(config.get("ezq_auto_collect", True)),
    )
    workmgmt_collector = WorkMgmtDocCollector(
        db=db,
        parser=parser,
        window_class=config.get("workmgmt_window_class", "Chrome_WidgetWin_1|IEFrame"),
        enabled=bool(config.get("workmgmt_auto_collect", True)),
    )

    if "--headless-scan" in sys.argv:
        created = folder_collector.collect_and_register()
        summary = db.get_briefing_summary()
        print(
            f"[Headless Scan Complete] 신규 등록: {len(created)}건 | "
            f"기한경과(이월): {len(summary['overdue'])}건 | "
            f"오늘마감: {len(summary['today'])}건 | "
            f"내일/금주예정: {len(summary['tomorrow']) + len(summary['upcoming_week'])}건"
        )
        return

    from ui.app_window import TodoCalendarApp

    is_startup = "--startup" in sys.argv
    app = TodoCalendarApp(
        config=config,
        db=db,
        folder_collector=folder_collector,
        clipboard_collector=clipboard_collector,
        ezq_collector=ezq_collector,
        workmgmt_collector=workmgmt_collector,
        is_startup_mode=is_startup,
        config_path=config_path,
    )
    app.mainloop()


if __name__ == "__main__":
    main()
