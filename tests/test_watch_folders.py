"""감시 폴더 옵션(업무 파일만·가져올 범위), 변경 없는 파일 건너뛰기, 설정 저장 테스트 (가상 데이터)."""

from datetime import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from collectors.folder_watcher import (
    EXCLUDED_PREFIX,
    OUTCOME_CREATED,
    OUTCOME_KNOWN,
    OUTCOME_NOT_TASK,
    OUTCOME_UNCHANGED,
    FolderWatcherCollector,
    WatchFolder,
)
from core.config_store import save_config_values
from core.database import DatabaseManager
from parsers.portal_parser import PortalAndMessengerParser
from test_pdf_variants import _korean_font_pdf

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
MESSENGER_NAME = "홍길동-2026-10-29_093507.txt"
MESSENGER_TEXT = "송신:홍길동\n시간:2026-10-29 09:35:07:000\n제목:출장 계획 작성 요청\n내용:출장 계획 내일 오전까지 작성 부탁드립니다.\n"


class TestWatchFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "다운로드")
        os.makedirs(self.folder)
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data, mtime=None):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("cp949"))
        if mtime:
            ts = datetime.strptime(mtime, "%Y-%m-%d").timestamp()
            os.utime(path, (ts, ts))
        return path

    def watcher(self, **opts):
        return FolderWatcherCollector(
            self.db, PortalAndMessengerParser(), watch_folders=[WatchFolder(self.folder, **opts)]
        )

    def outcomes(self, results):
        return {os.path.basename(r.path): r.outcome for r in results}

    def test_work_files_only_skips_unrelated_files(self):
        self.put(MESSENGER_NAME, MESSENGER_TEXT)
        shutil.copy(os.path.join(FIXTURES, "sample_gongmun.pdf"), os.path.join(self.folder, "공문.pdf"))
        self.put("설명서.pdf", _korean_font_pdf(b"UniKS-UCS2-H", "utf-16-be"))  # 제목·기한은 있지만 공문 아님
        self.put("메모.txt", "보고서 내일까지 제출 바랍니다.")  # 업무 문장이지만 쪽지 저장 파일 아님
        self.put("페이지.html", "<p>점검 결과 회신 요청</p>")

        results = self.watcher(work_files_only=True).scan_all()
        self.assertEqual(self.outcomes(results), {
            MESSENGER_NAME: OUTCOME_CREATED,
            "공문.pdf": OUTCOME_CREATED,
            "설명서.pdf": OUTCOME_NOT_TASK,
            "메모.txt": EXCLUDED_PREFIX + "쪽지·공문 파일 아님",
            "페이지.html": EXCLUDED_PREFIX + "쪽지·공문 파일 아님",
        })
        self.assertEqual(len(self.db.query_tasks()), 2)

    def test_all_files_folder_keeps_old_behavior(self):
        self.put("메모.txt", "보고서 내일까지 제출 바랍니다.")
        self.assertEqual(len(self.watcher().collect_and_register()), 1)

    def test_files_before_start_date_are_ignored(self):
        self.put("홍길동-2026-09-01_100000.txt", MESSENGER_TEXT.replace("10-29", "09-01"), mtime="2026-09-01")
        self.put(MESSENGER_NAME, MESSENGER_TEXT, mtime="2026-10-29")
        results = self.watcher(work_files_only=True, since="2026-10-01").scan_all()
        self.assertEqual(self.outcomes(results)["홍길동-2026-09-01_100000.txt"], EXCLUDED_PREFIX + "2026-10-01 이전 파일")
        self.assertEqual([t.title for r in results for t in r.created], ["출장 계획 작성 요청"])

    def test_unchanged_files_are_not_read_again(self):
        path = self.put(MESSENGER_NAME, MESSENGER_TEXT)
        self.put("메모.txt", "점심 메뉴")
        watcher = self.watcher()
        with mock.patch.object(watcher, "_compute_file_hash", wraps=watcher._compute_file_hash) as hashed:
            watcher.scan_all()
            self.assertEqual(hashed.call_count, 2)
            self.assertEqual(set(self.outcomes(watcher.scan_all()).values()), {OUTCOME_UNCHANGED})
            self.assertEqual(hashed.call_count, 2)  # 두 번째 확인에서는 내용을 읽지 않음

            os.utime(path, (os.path.getmtime(path) + 60,) * 2)  # 내용 그대로 다시 저장
            self.assertEqual(self.outcomes(watcher.scan_all())[MESSENGER_NAME], OUTCOME_UNCHANGED)
            watcher.scan_all()
            self.assertEqual(hashed.call_count, 3)
        self.assertEqual(len(self.db.query_tasks()), 1)

    def test_reimporting_same_file_reports_known(self):
        path = self.put(MESSENGER_NAME, MESSENGER_TEXT)
        watcher = self.watcher()
        self.assertEqual(watcher.scan_file(path, force=True).outcome, OUTCOME_CREATED)
        again = watcher.scan_file(path, force=True)
        self.assertEqual((again.outcome, again.created), (OUTCOME_KNOWN, []))

    def test_config_round_trip(self):
        legacy = WatchFolder.from_config("./scan_inbox")
        self.assertEqual(legacy.to_config(), "./scan_inbox")  # 옵션 없으면 예전 형식 그대로
        custom = WatchFolder(r"D:\쪽지", work_files_only=True, since="2026-10-29")
        self.assertEqual(WatchFolder.from_config(custom.to_config()), custom)


class TestConfigStore(unittest.TestCase):
    def test_saves_only_given_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"app_name": "앱", "watch_folders": ["./scan_inbox"]}, f, ensure_ascii=False)
            save_config_values(path, watch_folders=["./scan_inbox", {"path": "D:/쪽지", "work_files_only": True}])
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["app_name"], "앱")
        self.assertEqual(data["watch_folders"][1]["path"], "D:/쪽지")

    def test_broken_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{ 깨진 설정")
            with self.assertRaises(ValueError):
                save_config_values(path, watch_folders=[])
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "{ 깨진 설정")


if __name__ == "__main__":
    unittest.main()
