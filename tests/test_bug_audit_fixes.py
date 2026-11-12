"""2026-11-10 버그 점검에서 찾은 1~7번 재현 테스트."""
import json
import os
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta

from core.database import DatabaseManager
from core.models import TaskItem


class SnoozeLoopTest(unittest.TestCase):
    """1. Snooze 시각이 지난 뒤 알림을 띄우면 그날은 다시 뜨지 않아야 한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = DatabaseManager(os.path.join(self.tmp, "t.db"))

    def test_expired_snooze_fires_once(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tid, _ = self.db.add_task(TaskItem(title="기한 지난 보고", due_date=yesterday, content_hash="a"))
        now = datetime.now()
        past = (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        t = self.db.get_task(tid)
        t.snooze_until = past
        self.db.update_task(t)

        due = [x.id for x in self.db.get_due_tasks_for_reminder(now)]
        self.assertIn(tid, due)
        self.db.mark_notified(tid, now)
        later = now + timedelta(seconds=30)
        self.assertNotIn(tid, [x.id for x in self.db.get_due_tasks_for_reminder(later)])
        self.assertIsNone(self.db.get_task(tid).snooze_until)

    def test_future_snooze_is_kept(self):
        tid, _ = self.db.add_task(TaskItem(title="미룬 업무", due_date=date.today().isoformat(), content_hash="b"))
        self.db.snooze_task(tid, 30)
        self.db.mark_notified(tid)
        self.assertIsNotNone(self.db.get_task(tid).snooze_until)


class RelativeDateTest(unittest.TestCase):
    """2. 상대 날짜가 여러 개면 '까지'가 붙은 쪽을 기한으로, 낱말 일부('해당일')는 무시."""

    TODAY = date(2026, 11, 10)  # 화요일

    def setUp(self):
        from parsers.rule_parser import RuleBasedExtractor
        self.p = RuleBasedExtractor({})

    def due(self, text):
        return self.p._parse_date_from_chunk(text, self.TODAY)

    def test_marked_expression_wins(self):
        self.assertEqual(self.due("오늘 회의에서 논의된 보고서를 다음주 금요일까지 제출 바랍니다"), date(2026, 11, 20))
        self.assertEqual(self.due("이번주 월요일 회의 결과를 다음주 금요일까지 정리 바랍니다"), date(2026, 11, 20))

    def test_substrings(self):
        self.assertEqual(self.due("내일모레까지 제출"), date(2026, 11, 12))
        self.assertEqual(self.due("모레까지 제출"), date(2026, 11, 12))
        self.assertIsNone(self.due("해당일까지 제출"))

    def test_single_expressions_unchanged(self):
        self.assertEqual(self.due("오늘까지"), self.TODAY)
        self.assertEqual(self.due("내일 오후 3시까지"), date(2026, 11, 11))
        self.assertEqual(self.due("이번주 금요일까지"), date(2026, 11, 13))
        self.assertEqual(self.due("금요일까지"), date(2026, 11, 13))
        self.assertEqual(self.due("3일 이내"), date(2026, 11, 13))

    def test_quick_entry_uses_same_rule(self):
        from parsers.quick_entry import parse_quick_entry
        r = parse_quick_entry(self.p, "오늘 회의 내용 다음주 금요일까지 정리", "2026-11-10", ref_date=self.TODAY)
        self.assertEqual(r.due_date, "2026-11-20")


class DecimalNotDateTest(unittest.TestCase):
    """3. '3.5 이상', '1.5 배', 'v1.2'는 날짜가 아니고, '11.20 회의'는 날짜."""

    TODAY = date(2026, 11, 10)

    def setUp(self):
        from parsers.rule_parser import RuleBasedExtractor
        self.p = RuleBasedExtractor({})

    def test_decimals(self):
        for text in ["평균 3.5 이상 유지 보고", "만족도 4.5 점 이상 작성 요청", "1.5 배 증가 보고",
                     "버전 v1.2 배포", "회의실 3.1 호", "3.5 배로 확대"]:
            self.assertIsNone(self.p._parse_date_from_chunk(text, self.TODAY), text)

    def test_dates_with_unit_like_words(self):
        for text in ["11.20 까지 제출", "11.20 회의 자료", "11.20 점검", "11.20 개최", "11.20 등록 마감"]:
            self.assertEqual(self.p._parse_date_from_chunk(text, self.TODAY), date(2026, 11, 20), text)


class ClickPairTest(unittest.TestCase):
    """4. 캘린더 일정 칸은 첫 클릭에 새로 그려지므로 두 번 누르기를 직접 판단."""

    def test_double_click_same_task(self):
        from ui.app_window import ClickPairDetector
        c = ClickPairDetector(500)
        self.assertFalse(c.click(("task", 1), 1000))
        self.assertTrue(c.click(("task", 1), 1300))
        self.assertFalse(c.click(("task", 1), 1500))  # 세 번째는 새 시작

    def test_slow_or_other_task(self):
        from ui.app_window import ClickPairDetector
        c = ClickPairDetector(500)
        c.click(("task", 1), 1000)
        self.assertFalse(c.click(("task", 2), 1200))
        self.assertFalse(c.click(("task", 2), 1800))


def _timed(fn, *args):
    t0 = time.perf_counter()
    try:
        result = fn(*args)
    except Exception as e:  # noqa: BLE001 - 결과 형태만 확인
        result = e
    return result, time.perf_counter() - t0


_HEAD = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "


class HostilePdfTest(unittest.TestCase):
    """5. 손상·악성 PDF가 멈추거나 예상 못한 예외를 내지 않아야 한다."""

    def extract(self, data):
        from parsers.pdf_text import extract_pdf_text_from_bytes
        return _timed(extract_pdf_text_from_bytes, data)

    def test_page_tree_cycle_finishes(self):
        result, sec = self.extract(_HEAD + b"2 0 obj<</Type/Pages/Kids[2 0 R 2 0 R]>>endobj")
        self.assertLess(sec, 2)

    def test_form_xobject_fanout_finishes(self):
        data = (_HEAD + b"2 0 obj<</Type/Pages/Kids[3 0 R]>>endobj "
                b"3 0 obj<</Type/Page/Resources 5 0 R/Contents 4 0 R>>endobj "
                b"5 0 obj<</XObject<</X 4 0 R>>>>endobj "
                b"4 0 obj<</Subtype/Form/Resources 5 0 R/Length 1200>>stream\n" + b"/X Do " * 200
                + b"\nendstream endobj")
        result, sec = self.extract(data)
        self.assertLess(sec, 2)

    def test_broken_structures_raise_pdf_error(self):
        from parsers.pdf_text import PDFTextError
        page = b"2 0 obj<</Type/Pages/Kids[3 0 R]>>endobj "
        content = b"4 0 obj<</Length 20>>stream\nBT /F1 12 Tf (ab) Tj ET\nendstream endobj"
        cases = {
            "깨진 압축": _HEAD + page + b"3 0 obj<</Type/Page/Contents 4 0 R>>endobj "
                         b"4 0 obj<</Filter/FlateDecode/Length 5>>stream\nXXXXX\nendstream endobj",
            "폰트가 배열": _HEAD + page + b"3 0 obj<</Type/Page/Resources<</Font[1 2]>>/Contents 4 0 R>>endobj "
                          + content,
            "W에 null": _HEAD + page + b"3 0 obj<</Type/Page/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj "
                        + content + b" 5 0 obj<</Subtype/Type0/Encoding/Identity-H/DescendantFonts[6 0 R]>>endobj "
                        b"6 0 obj<</W[null 5 600]>>endobj",
        }
        for name, data in cases.items():
            result, _ = self.extract(data)
            self.assertTrue(isinstance(result, (str, PDFTextError)), f"{name}: {result!r}")


class SlowRegexTest(unittest.TestCase):
    """6. 날짜 뒤에 빈칸·줄바꿈이 길게 이어져도 바로 끝나야 한다 (예전: 2~54초)."""

    def test_long_whitespace_after_date(self):
        from parsers.message_scorer import score_message
        from parsers.rule_parser import RuleBasedExtractor
        p = RuleBasedExtractor({})
        for text in ["회의 11.10" + "\n" * 300 + "자료 제출 부탁드립니다", "11.10" + " " * 2000 + "a"]:
            for fn in (score_message, p.extract_tasks):
                _, sec = _timed(fn, text)
                self.assertLess(sec, 0.5, f"{fn.__name__}: {sec:.2f}s")


class ConfigBomTest(unittest.TestCase):
    """7. 메모장이 BOM을 붙여 저장한 config.json도 읽고, 앱에서 저장해도 깨지지 않아야 한다."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "config.json")

    def write(self, data: bytes):
        with open(self.path, "wb") as f:
            f.write(data)

    def test_bom_and_cp949(self):
        from core.config_store import read_config_file, save_config_values
        from main import load_config
        body = json.dumps({"watch_folders": ["D:\\받은쪽지"]}, ensure_ascii=False)
        for enc_bytes in (b"\xef\xbb\xbf" + body.encode("utf-8"), body.encode("cp949")):
            self.write(enc_bytes)
            self.assertEqual(load_config(self.path)["watch_folders"], ["D:\\받은쪽지"])
            save_config_values(self.path, ezq_auto_collect=True)
            data = read_config_file(self.path)
            self.assertEqual(data["watch_folders"], ["D:\\받은쪽지"])
            self.assertTrue(data["ezq_auto_collect"])

    def test_broken_json_is_not_overwritten(self):
        from core.config_store import save_config_values
        self.write(b"{ broken")
        with self.assertRaises(ValueError):  # JSONDecodeError는 ValueError의 하위
            save_config_values(self.path, ezq_auto_collect=True)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"{ broken")


if __name__ == "__main__":
    unittest.main()
