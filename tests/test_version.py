"""버전 번호가 한곳(core/version.py)과 맞는지."""
import json
import os
import re
import unittest

from core.version import APP_VERSION

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VersionTest(unittest.TestCase):
    def test_format(self):
        self.assertRegex(APP_VERSION, r"^\d+\.\d+\.\d+$")

    def test_config_json_matches(self):
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8-sig") as f:
            self.assertEqual(json.load(f)["version"], APP_VERSION)

    def test_changelog_top_entry_matches(self):
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            first = re.search(r"^## (\d+\.\d+\.\d+)", f.read(), re.MULTILINE)
        self.assertIsNotNone(first)
        self.assertEqual(first.group(1), APP_VERSION)

    def test_user_guide_title_matches(self):
        path = os.path.join(ROOT, "docs", "portable_guide.txt")
        if not os.path.exists(path):
            self.skipTest("사용 안내 파일 없음 (회사 PC 보안 프로그램이 txt를 지우는 경우)")
        with open(path, encoding="utf-8") as f:
            self.assertIn(f"v{APP_VERSION}", f.readline())


if __name__ == "__main__":
    unittest.main()
