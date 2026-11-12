"""오래 켜 두고 쓸 때의 문제 (2026-11-10 스트레스 테스트에서 찾음)."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from core.database import DatabaseManager
from core.models import RecurrenceType, TaskItem, TaskStatus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VisibleRangeTest(unittest.TestCase):
    """월간 달력은 보이는 6주에 걸치는 업무 + 기한경과·반복 미완료 업무만 읽는다."""

    def test_only_needed_tasks(self):
        db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))
        today = date.today()
        lo, hi = today - timedelta(days=10), today + timedelta(days=31)

        def add(title, due, status=TaskStatus.PENDING, start="", rec=RecurrenceType.NONE):
            tid, _ = db.add_task(TaskItem(title=title, due_date=due.isoformat(), start_date=start, status=status,
                                          recurrence=rec, content_hash=title))
            return title

        want = {
            add("보이는 날", today + timedelta(days=3)),
            add("보이는 날 완료", today + timedelta(days=3), TaskStatus.COMPLETED),
            add("기간이 걸침", hi + timedelta(days=5), start=(hi - timedelta(days=2)).isoformat()),
            add("오래된 기한경과", today - timedelta(days=200)),
            add("오래된 반복", hi + timedelta(days=60), rec=RecurrenceType.WEEKLY),
        }
        add("오래전 완료", today - timedelta(days=200), TaskStatus.COMPLETED)
        add("먼 미래", hi + timedelta(days=90))
        got = {t.title for t in db.query_tasks(visible_range=(lo.isoformat(), hi.isoformat()))}
        self.assertEqual(got, want)


class AgendaHistoryTest(unittest.TestCase):
    def test_old_completed_hidden_but_old_incomplete_kept(self):
        db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))
        old = (date.today() - timedelta(days=200)).isoformat()
        recent = (date.today() - timedelta(days=10)).isoformat()
        for title, due, st in [("오래된 완료", old, TaskStatus.COMPLETED), ("오래된 미완료", old, TaskStatus.PENDING),
                               ("최근 완료", recent, TaskStatus.COMPLETED)]:
            db.add_task(TaskItem(title=title, due_date=due, status=st, content_hash=title))
        cutoff = (date.today() - timedelta(days=90)).isoformat()
        got = {t.title for t in db.query_tasks(hide_completed_before=cutoff)}
        self.assertEqual(got, {"오래된 미완료", "최근 완료"})
        self.assertEqual(len(db.query_tasks()), 3)


class NewDayTest(unittest.TestCase):
    """켜 둔 채 자정을 넘기면 화면을 다시 그리고, 어제(옛 오늘)를 보고 있었으면 새 오늘로 옮긴다."""

    def _fake(self, selected, ym):
        calls = []
        return SimpleNamespace(selected_date_str=selected, selected_task=object(), current_year=ym[0],
                               current_month=ym[1], refresh_all=lambda: calls.append(1)), calls

    def test_moves_selection_from_old_today(self):
        from ui.app_window import TodoCalendarApp
        old = date.today() - timedelta(days=1)
        app, calls = self._fake(old.isoformat(), (old.year, old.month))
        TodoCalendarApp._on_new_day(app, old)
        self.assertEqual(app.selected_date_str, date.today().isoformat())
        self.assertEqual((app.current_year, app.current_month), (date.today().year, date.today().month))
        self.assertEqual(calls, [1])

    def test_keeps_other_selection(self):
        from ui.app_window import TodoCalendarApp
        old = date.today() - timedelta(days=1)
        app, calls = self._fake("2026-01-15", (2026, 1))
        TodoCalendarApp._on_new_day(app, old)
        self.assertEqual((app.selected_date_str, app.current_month), ("2026-01-15", 1))
        self.assertEqual(calls, [1])  # 그래도 다시 그림 (오늘 표시·기한경과 색)


class FolderScanCostTest(unittest.TestCase):
    """큰 폴더: 바뀌지 않은 파일은 DB·디스크를 다시 묻지 않고, 처음 가져오기는 시간 한도로 나눠 처리."""

    def setUp(self):
        from collectors.folder_watcher import FolderWatcherCollector, WatchFolder
        from parsers.portal_parser import PortalAndMessengerParser
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "하위"))
        for i in range(30):
            sub = self.dir if i % 2 else os.path.join(self.dir, "하위")
            with open(os.path.join(sub, f"홍길동-2026-11-{i % 28 + 1:02d}_{i:06d}.txt"), "w", encoding="utf-8") as f:
                f.write(f"송신: 홍길동\n시간: 2026-11-01 09:00\n제목: 자료 {i}\n내용: 자료 {i} 제출 부탁드립니다\n")
        self.db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.c = FolderWatcherCollector(db=self.db, parser=PortalAndMessengerParser(), watch_folders=[self.dir],
                                        supported_extensions=[".txt"])
        self.c.set_folders([WatchFolder(path=self.dir, work_files_only=True, since="")])

    def test_unchanged_files_skip_db(self):
        self.assertEqual(len(self.c.collect_and_register()), 30)
        calls = []
        real = self.db.get_scanned_file
        self.db.get_scanned_file = lambda p: (calls.append(p), real(p))[1]
        self.assertEqual(self.c.collect_and_register(), [])
        self.assertEqual(calls, [])

    def test_time_budget_splits_first_import(self):
        real = self.c.parser.parse_file

        def slow(*a, **kw):  # 새 파일 읽기만 느리게 (이미 확인한 파일은 실제로도 바로 건너뜀)
            time.sleep(0.02)
            return real(*a, **kw)
        self.c.parser.parse_file = slow
        first = self.c.collect_and_register(time_budget=0.1)
        self.assertLess(len(first), 30)
        total = len(first)
        for _ in range(30):
            total += len(self.c.collect_and_register(time_budget=0.1))
        self.assertEqual(total, 30)  # 나눠서라도 빠짐없이, 중복 없이


class CalendarRedrawLeakTest(unittest.TestCase):
    """달력을 다시 그려도 칸의 클릭 연결이 쌓이지 않는다 (2026-11-11 장기 실행 테스트: 2000회에 167MB 증가)."""

    def test_cell_bindings_do_not_pile_up(self):
        import json
        import tkinter as tk
        from collectors.clipboard_collector import ClipboardCollector
        from collectors.folder_watcher import FolderWatcherCollector
        from parsers.portal_parser import PortalAndMessengerParser
        from ui.app_window import TodoCalendarApp
        tmp = tempfile.mkdtemp()
        db = DatabaseManager(os.path.join(tmp, "t.db"))
        db.add_task(TaskItem(title="오늘 업무", due_date=date.today().isoformat(), content_hash="a"))
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            config = json.load(f)
        config["show_startup_briefing"] = False
        parser = PortalAndMessengerParser()
        os.makedirs(os.path.join(tmp, "in"))
        try:
            app = TodoCalendarApp(
                config=config, db=db,
                folder_collector=FolderWatcherCollector(db=db, parser=parser, watch_folders=[os.path.join(tmp, "in")],
                                                        supported_extensions=[".txt"]),
                clipboard_collector=ClipboardCollector(db=db, parser=parser),
                config_path=os.path.join(tmp, "c.json"))
        except tk.TclError as exc:
            self.skipTest(f"화면을 열 수 없음: {exc}")
        try:
            app.scheduler.stop()
            app.update()
            app.refresh_all()
            before = sum(len(c._tclCommands or ()) for c in app.day_cells)
            for _ in range(5):
                app._next_month()
                app.refresh_all()
            after = sum(len(c._tclCommands or ()) for c in app.day_cells)
            self.assertEqual(after, before)
            # 달을 옮긴 뒤에도 칸을 누르면 그 칸에 지금 표시된 날짜가 선택됨
            cell = app.day_cells[10]
            app._cell_click(cell, app._select_date)
            self.assertEqual(app.selected_date_str, app._cell_dates[str(cell)])
        finally:
            app.destroy()


class CalendarReuseTest(unittest.TestCase):
    """달력을 다시 그릴 때 칸 부품을 지우지 않고 다시 쓴다 (1년치 데이터에서 새로고침 0.65초 → 0.06초)."""

    def setUp(self):
        import json
        import tkinter as tk
        from collectors.clipboard_collector import ClipboardCollector
        from collectors.folder_watcher import FolderWatcherCollector
        from parsers.portal_parser import PortalAndMessengerParser
        from ui.app_window import TodoCalendarApp
        tmp = tempfile.mkdtemp()
        self.db = DatabaseManager(os.path.join(tmp, "t.db"))
        self.today = date.today()
        first = self.today.replace(day=1)
        # 이번 달 10일에 6건(칸에 다 안 들어가 '+ 외 N건'이 생김), 다음 달 10일에 1건
        self.day10 = first.replace(day=10)
        for i in range(6):
            self.db.add_task(TaskItem(title=f"이번 달 업무 {i}", due_date=self.day10.isoformat(), content_hash=f"a{i}"))
        nxt = (first + timedelta(days=32)).replace(day=10)
        self.next10 = nxt
        self.db.add_task(TaskItem(title="다음 달 업무", due_date=nxt.isoformat(), content_hash="b"))
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            config = json.load(f)
        config["show_startup_briefing"] = False
        parser = PortalAndMessengerParser()
        os.makedirs(os.path.join(tmp, "in"))
        try:
            self.app = TodoCalendarApp(
                config=config, db=self.db,
                folder_collector=FolderWatcherCollector(db=self.db, parser=parser,
                                                        watch_folders=[os.path.join(tmp, "in")],
                                                        supported_extensions=[".txt"]),
                clipboard_collector=ClipboardCollector(db=self.db, parser=parser),
                config_path=os.path.join(tmp, "c.json"))
        except tk.TclError as exc:
            self.skipTest(f"화면을 열 수 없음: {exc}")
        self.addCleanup(self.app.destroy)
        self.app.scheduler.stop()
        self.app.geometry("1200x800+0+0")
        self.app.update()
        self.app.refresh_all()
        self.app.update()

    def _cell(self, day):
        for cell in self.app.day_cells:
            if self.app._cell_dates[str(cell)] == day.isoformat():
                return cell
        self.fail(f"{day} 칸이 없음")

    def _texts(self, cell):
        return [w.cget("text") for w in cell.pack_slaves()[1:]]  # 첫 줄(날짜 숫자) 빼고

    def test_widgets_are_kept_across_redraws(self):
        before = {str(w) for c in self.app.day_cells for w in c.winfo_children()}
        for _ in range(3):
            self.app.refresh_all()
        self.app._select_date(self.day10.isoformat())
        after = {str(w) for c in self.app.day_cells for w in c.winfo_children()}
        self.assertEqual(after, before)

    def test_selecting_a_cell_does_not_hide_its_chips(self):
        """칸을 선택해도(테두리 1px → 2px) 보이던 업무 수가 그대로여야, 누른 업무가 바로 숨지 않는다."""
        other = self.day10.replace(day=20)
        self.app._select_date(other.isoformat())
        self.app.update()
        before = self._texts(self._cell(self.day10))
        self.app._select_date(self.day10.isoformat())
        self.app.update()
        self.assertEqual(self._texts(self._cell(self.day10)), before)

    def test_month_move_shows_that_month_and_more_line_stays_last(self):
        texts = self._texts(self._cell(self.day10))
        # 앞의 기호는 날짜에 따라 •(예정) 또는 🚨(기한경과), 뒤는 칸 너비에 따라 잘릴 수 있음
        self.assertIn("이번 달", texts[0])
        self.app._next_month()
        self.app.update()
        next_texts = self._texts(self._cell(self.next10))
        self.assertEqual(len(next_texts), 1)
        self.assertTrue(next_texts[0].startswith("• 다음 달"))
        self.app._prev_month()
        self.app.update()
        self.assertEqual(self._texts(self._cell(self.day10)), texts)
        # 업무가 넘치는 칸에서는 '+ 외 N건' 줄이 항상 맨 아래
        if len(texts) < 6:
            self.assertTrue(texts[-1].startswith("+"))
            self.assertFalse(any(t.startswith("+") for t in texts[:-1]))

    def test_chip_click_uses_task_now_shown(self):
        """칩은 다시 쓰이므로, 누르면 예전에 보여 주던 업무가 아니라 지금 보여 주는 업무여야 한다."""
        self.app._next_month()
        self.app.update()
        chip = self._cell(self.next10).pack_slaves()[1]
        task, d_str = self.app._chip_data[str(chip)]
        self.assertEqual((task.title, d_str), ("다음 달 업무", self.next10.isoformat()))
        pressed = []
        self.app._chip_press = lambda event, t, ds: pressed.append((t.title, ds))
        self.app._chip_event(chip, SimpleNamespace(time=0), menu=False)
        self.assertEqual(pressed, [("다음 달 업무", self.next10.isoformat())])
        # 숨겨진(쓰지 않는) 칩은 눌러도 아무 일도 없음
        self.app._prev_month()
        self.app._next_month()
        hidden = [c for parts in self.app._cell_parts for c in parts["chips"][parts["shown"]:]]
        for c in hidden:
            self.assertNotIn(str(c), self.app._chip_data)


class SingleInstanceTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows 전용")
    def test_second_copy_is_refused(self):
        base = tempfile.mkdtemp()
        code = ("import sys,time; sys.path.insert(0, r'%s'); from main import acquire_single_instance as a; "
                "print(a(r'%s'), flush=True); time.sleep(%s)")
        first = subprocess.Popen([sys.executable, "-c", code % (ROOT, base, 5)], stdout=subprocess.PIPE, text=True,
                                 cwd=ROOT)
        try:
            self.assertEqual(first.stdout.readline().strip(), "True")
            second = subprocess.run([sys.executable, "-c", code % (ROOT, base, 0)], capture_output=True, text=True,
                                    cwd=ROOT, timeout=60)
            self.assertEqual(second.stdout.strip(), "False")
            other = subprocess.run([sys.executable, "-c", code % (ROOT, tempfile.mkdtemp(), 0)],
                                   capture_output=True, text=True, cwd=ROOT, timeout=60)
            self.assertEqual(other.stdout.strip(), "True")  # 다른 폴더(다른 DB)의 사본은 막지 않음
        finally:
            first.kill()
            first.wait()
            first.stdout.close()


if __name__ == "__main__":
    unittest.main()
