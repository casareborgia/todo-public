"""기능 전반 시연영상용 장면 캡처: 실제 앱(v1.2.0)을 가상 데이터로 띄워 창만 PNG로 찍습니다.

사용: (먼저 make_demo_data.py 실행) python shoot_all.py [작업 폴더, 기본 C:\\tc\\video2]
- 앱 코드는 고치지 않습니다. 공개 메서드·위젯 값 변경·파일 선택 창 대체(촬영 스크립트 안에서만)로 화면을 바꿉니다.
- 앱 창·팝업·시연용 재현 쪽지 창만 PrintWindow로 찍으므로 바탕화면이나 다른 창은 담기지 않습니다.
- 결과: <작업 폴더>\\shots\\*.png
"""

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else r"C:\tc\video2")
APP = os.path.join(BASE, "demo_app")
FILES = os.path.join(BASE, "demo_files")
OUT = os.path.join(BASE, "shots")
SCALE = float(os.environ.get("DEMO_SCALE", "1.25"))  # 1280x820 창을 1600x1025로 (글자를 키워 1080p에서 선명하게)

sys.path.insert(0, APP)
sys.path.insert(0, HERE)
os.makedirs(OUT, exist_ok=True)
os.chdir(APP)
ctypes.windll.shcore.SetProcessDpiAwareness(1)

_orig_tk_init = tk.Tk.__init__


def _scaled_init(self, *a, **k):
    _orig_tk_init(self, *a, **k)
    self.tk.call("tk", "scaling", 96 / 72 * SCALE)


tk.Tk.__init__ = _scaled_init

import wincap  # noqa: E402
import main as entry  # noqa: E402  (시연용 사본의 main.py: 설정 읽기만 사용)
import ui.app_window as app_window  # noqa: E402
from collectors.clipboard_collector import ClipboardCollector  # noqa: E402
from collectors.ezq_collector import EzqMessageCollector  # noqa: E402
from collectors.folder_watcher import FolderWatcherCollector, WatchFolder  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.models import RecurrenceType  # noqa: E402
from parsers.portal_parser import PortalAndMessengerParser  # noqa: E402
from parsers.rule_parser import RuleBasedExtractor  # noqa: E402

os.chdir(APP)  # main.py가 작업 폴더를 바꿨을 수 있으므로 다시 시연용 사본으로
assert os.path.abspath(os.getcwd()) == os.path.abspath(APP)

cfg = entry.load_config(os.path.join(APP, "config.json"))
db = DatabaseManager(os.path.join(APP, "todo_data.db"))
parser = PortalAndMessengerParser(RuleBasedExtractor(cfg))
folder = FolderWatcherCollector(db=db, parser=parser, watch_folders=[], supported_extensions=cfg.get("supported_extensions"))
ezq = EzqMessageCollector(db=db, parser=parser, window_class=cfg["ezq_window_class"])
app = app_window.TodoCalendarApp(cfg, db, folder, ClipboardCollector(db=db, parser=parser), ezq_collector=ezq,
                                 config_path=os.path.join(APP, "config.json"))
W, H = int(1280 * SCALE), int(820 * SCALE)
app.geometry(f"{W}x{H}+0+0")

# 감시 폴더로 보일 경로 (C:\ 바로 아래에 못 만들면 작업 폴더 아래에)
WATCH_ROOT = r"C:\업무자료"
try:
    os.makedirs(WATCH_ROOT, exist_ok=True)
except OSError:
    WATCH_ROOT = os.path.join(BASE, "업무자료")
MSG_DIR, PDF_DIR = os.path.join(WATCH_ROOT, "메신저쪽지"), os.path.join(WATCH_ROOT, "공문다운로드")
for p in (MSG_DIR, PDF_DIR):
    shutil.rmtree(p, ignore_errors=True)
    os.makedirs(p)

T = date.today()
FRIDAY = T + timedelta(days=(4 - T.weekday()) % 7)
procs = []
log = []


# ---------------------------------------------------------------- 도우미
def hwnd_of(top):
    top.update_idletasks()
    return wincap.user32.GetAncestor(top.winfo_id(), 2)  # GA_ROOT


RECTS = {}  # 캡처 이미지 픽셀 기준 위치 (x, y, w, h). build_composition.py가 강조 상자·확대 위치로 씀


def _origin():
    fr = wincap._frame_rect(hwnd_of(app))
    return fr.left, fr.top


def rect(key, widget):
    ox, oy = _origin()
    RECTS[key] = [widget.winfo_rootx() - ox, widget.winfo_rooty() - oy, widget.winfo_width(), widget.winfo_height()]


def cell_widget(date_str):
    for c in app.day_cells:
        if app._cell_dates.get(str(c)) == date_str:
            return c
    return None


def record_main_rects(name):
    """메인 창을 찍을 때마다 주요 위젯 위치를 남김 (빠른 입력 줄이 생겨 아래쪽 위치가 바뀌어도 맞도록)."""
    import tkinter.font as tkfont

    app.update_idletasks()
    for key, w in [("status", app.status_sub_lbl), ("legend", app.legend_frame), ("cal", app.cal_grid_frame),
                   ("month", app.month_view_frame), ("right", app.right_container), ("daylist", app.day_tree),
                   ("detail", app.detail_box), ("meta", app.detail_meta_lbl), ("text", app.detail_text),
                   ("filter", app.filter_bar), ("status_cb", app.status_cb), ("label_cb", app.label_cb),
                   ("quick", app.quick_bar), ("quick_entry", app.quick_entry),
                   ("quick_preview", app.quick_preview_lbl), ("quick_hint", app.quick_hint_lbl),
                   ("agenda", app.agenda_view_frame)]:
        rect(f"{name}.{key}", w)
    RECTS[f"{name}.meta_ls"] = tkfont.Font(font=app.detail_meta_lbl.cget("font")).metrics("linespace")
    RECTS[f"{name}.text_ls"] = tkfont.Font(font=app.detail_text.cget("font")).metrics("linespace")
    for c in app.day_cells:
        if str(c) in app._cell_dates:
            rect(f"{name}.cell.{app._cell_dates[str(c)]}", c)
    sel = app.day_tree.selection()
    if sel and app.day_tree.bbox(sel[0]):
        x, y, w, h = app.day_tree.bbox(sel[0])
        ox, oy = _origin()
        RECTS[f"{name}.sel_row"] = [app.day_tree.winfo_rootx() - ox + x, app.day_tree.winfo_rooty() - oy + y, w, h]


def shot(name, win=None, hwnd=None):
    app.update()
    if win is not None:
        win.update()
        hwnd = hwnd_of(win)
    if win is None and hwnd is None:
        record_main_rects(name)
    size = wincap.capture(hwnd or hwnd_of(app), os.path.join(OUT, name + ".png"))
    print("captured", name, size, flush=True)


class Ev:  # 마우스 이벤트 대신 넘기는 화면 좌표
    def __init__(self, x, y):
        self.x_root, self.y_root, self.x, self.y = x, y, 0, 0


def chip_of(task):
    cell = cell_widget(task.due_date)
    for w in cell.winfo_children():
        if isinstance(w, tk.Label) and w.cget("cursor") == "hand2" and task.title[:8] in w.cget("text"):
            return w
    raise RuntimeError(f"chip not found: {task.title}")


def center(w):
    return w.winfo_rootx() + w.winfo_width() // 2, w.winfo_rooty() + w.winfo_height() // 2


def toplevels():
    return [w for w in app.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]


def newest_toplevel(before):
    new = [w for w in toplevels() if w not in before]
    return new[-1] if new else None


def grow(top, w, h, x=None, y=None):
    """팝업은 픽셀 크기가 고정이라 글자 배율에 맞춰 키우고 화면 가운데에 둠."""
    w, h = int(w * SCALE), int(h * SCALE)
    x = (1920 - w) // 2 if x is None else x
    y = max(0, (1080 - h) // 2) if y is None else y
    top.geometry(f"{w}x{h}+{x}+{y}")
    top.update()


def close_all_popups():
    for t in toplevels():
        t.destroy()
    app.update()


WM_CLOSE = 0x0010


def catch_native(title, name, delay=0.8):
    """앱이 띄우는 Windows 기본 메시지 상자(모달)를 다른 스레드에서 찍고 닫음."""

    def run():
        for _ in range(100):
            hwnd = wincap.find(title)
            if hwnd:
                time.sleep(delay)
                wincap.capture(hwnd, os.path.join(OUT, name + ".png"))
                print("captured", name, "(native)", flush=True)
                ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return
            time.sleep(0.1)
        print("TIMEOUT native", title, flush=True)

    threading.Thread(target=run, daemon=True).start()


def wait_for(cond, then, timeout_ms=40000, step=250):
    left = [timeout_ms]

    def tick():
        if cond():
            then()
        elif left[0] <= 0:
            print("TIMEOUT", then.__name__, flush=True)
            then()
        else:
            left[0] -= step
            app.after(step, tick)

    app.after(step, tick)


def open_msg(sender, sent, body_html):
    path = os.path.join(BASE, "msg_body.txt")
    open(path, "w", encoding="utf-8").write(body_html)
    p = subprocess.Popen(["powershell", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          os.path.join(HERE, "msgwin.ps1"), "-Sender", sender, "-Sent", sent, "-BodyFile", path])
    procs.append(p)


def kill_msgs():
    for p in procs:
        p.kill()
    procs.clear()


def msg_hwnd():
    return wincap.find("받은쪽지 (시연용 재현)")


def tasks_where(pred):
    return [t for t in db.query_tasks() if pred(t)]


def select(task):
    app._select_date_and_task(task.due_date, task)
    app.update()


def fake_file_dialog(paths):
    app_window.filedialog.askopenfilenames = lambda **_k: tuple(paths)


SEQ = []


def step(delay_ms=900):
    def deco(fn):
        SEQ.append((fn, delay_ms))
        return fn
    return deco


def run_next(i=0):
    if i >= len(SEQ):
        finish()
        return
    fn, delay = SEQ[i]
    res = fn()
    if callable(res):  # 기다림이 필요한 단계: res(다음 단계 호출)를 넘겨받아 직접 이어감
        res(lambda: app.after(delay, lambda: run_next(i + 1)))
    else:
        app.after(delay, lambda: run_next(i + 1))


# ---------------------------------------------------------------- 장면
@step(1200)
def s01_briefing():
    before = toplevels()
    app.open_briefing_popup(1)
    app.update()
    grow(newest_toplevel(before), 760, 560)


@step(600)
def s01_shot():
    shot("01_briefing", toplevels()[-1])
    close_all_popups()
    app.startup_var.set(True)  # 화면 표시만 (레지스트리는 바꾸지 않음)
    app.focus_force()


@step(800)
def s02_main():
    shot("02_main_month")


@step(800)
def s03_select():
    overdue = tasks_where(lambda t: t.title == "정보보안 서약서 제출")[0]
    app._select_date(T.strftime("%Y-%m-%d"))
    app.update()
    # 오늘 칸에 이월된 업무를 고른 상태 (기한은 3일 전)
    app.selected_task = overdue
    app._show_task_detail(overdue)
    for iid, t in app._right_task_map.items():
        if t.id == overdue.id:
            app.day_tree.selection_set(iid)


@step(600)
def s03_shot():
    shot("03_detail")


@step(1000)
def s04_folders():
    folders = [WatchFolder(MSG_DIR, work_files_only=True, since=T.strftime("%Y-%m-%d")),
               WatchFolder(PDF_DIR, work_files_only=True, since=T.strftime("%Y-%m-%d"))]
    app._demo_folders = folders
    folder.set_folders(folders)
    before = toplevels()
    app.open_folder_settings_dialog()
    app.update()
    dlg = newest_toplevel(before)
    grow(dlg, 760, 470)
    try:
        dlg.tree.selection_set(dlg.tree.get_children()[0])
        dlg._load_options()
    except Exception as e:  # 선택 표시는 없어도 됨
        print("folder select:", e)


@step(600)
def s04_shot():
    shot("04_folder_settings", toplevels()[-1])
    close_all_popups()
    app._apply_folder_settings(app._demo_folders)  # 실제 저장 경로: 시연용 사본의 config.json
    app.update()
    close_all_popups()  # 빈 폴더 확인 결과 창


@step(800)
def s05_before():
    app._select_date(T.strftime("%Y-%m-%d"))
    shot("05_auto_before")
    for f in os.listdir(os.path.join(FILES, "auto")):
        dst = MSG_DIR if f.endswith(".txt") else PDF_DIR
        shutil.copy(os.path.join(FILES, "auto", f), dst)  # copy: 수정 시각이 지금이 되어 '오늘부터' 조건에 맞음
    log.append(("auto_copied", time.time()))


@step(1200)
def s05_wait():
    return lambda nxt: wait_for(lambda: tasks_where(lambda t: t.external_key == "DOC:기획팀-1234")
                                and tasks_where(lambda t: t.external_key.startswith("MSG:홍길동")), nxt)


@step(800)
def s05_after():
    print("auto registered after %.1fs" % (time.time() - log[-1][1]), flush=True)
    RECTS["date.pdf"] = tasks_where(lambda t: t.external_key == "DOC:기획팀-1234")[0].due_date
    RECTS["date.msg"] = tasks_where(lambda t: t.external_key.startswith("MSG:홍길동"))[0].due_date
    shot("05_auto_after")
    select(tasks_where(lambda t: t.external_key == "DOC:기획팀-1234")[0])


@step(600)
def s05_detail():
    shot("05_auto_detail")
    select(tasks_where(lambda t: t.external_key.startswith("MSG:홍길동"))[0])


@step(600)
def s05_detail_msg():
    shot("05_auto_detail_msg")
    d = os.path.join(FILES, "import")
    fake_file_dialog([os.path.join(d, f) for f in sorted(os.listdir(d))])
    before = toplevels()
    app.import_file_dialog()
    app.update()
    grow(newest_toplevel(before), 860, 480)


@step(600)
def s06_shot():
    shot("06_import_result", toplevels()[-1])
    close_all_popups()
    kill_msgs()
    open_msg("김철수 (가상)", f"{T:%Y-%m-%d} 10:05",
             "안녕하세요, 총무팀 김철수입니다.<br>다음 주 월요일 오전까지 교육 결과 보고서 작성 부탁드립니다.")
    return lambda nxt: wait_for(lambda: msg_hwnd(), lambda: app.after(1500, nxt))


@step(200)
def s07_msg():
    shot("07_ezq_msg", hwnd=msg_hwnd())
    log.append(("ezq_open", time.time()))
    return lambda nxt: wait_for(lambda: tasks_where(lambda t: t.external_key.startswith("EZQ:")), nxt)


@step(800)
def s07_registered():
    print("ezq registered after %.1fs" % (time.time() - log[-1][1]), flush=True)
    RECTS["date.ezq"] = tasks_where(lambda t: t.external_key.startswith("EZQ:"))[0].due_date
    shot("07_ezq_registered")
    select(tasks_where(lambda t: t.external_key.startswith("EZQ:"))[0])


@step(600)
def s07_selected():
    shot("07_ezq_selected")
    kill_msgs()
    app.after(1200, lambda: open_msg("김철수 (가상)", f"{T:%Y-%m-%d} 10:07", "확인했습니다."))
    return lambda nxt: app.after(1300, lambda: wait_for(lambda: msg_hwnd(), lambda: app.after(1500, nxt)))


@step(6000)  # 수집기는 3초마다 확인: 답장이 등록되지 않는지 6초 기다림
def s07_reply():
    shot("07_ezq_reply_msg", hwnd=msg_hwnd())


@step(600)
def s07_after_reply():
    n = len(tasks_where(lambda t: t.external_key.startswith("EZQ:")))
    print("EZQ tasks after reply:", n, flush=True)
    shot("07_ezq_after_reply")
    kill_msgs()


@step(800)
def s08_clipboard():
    app.clipboard_clear()
    app.clipboard_append("예산 내역서 다음 주 수요일 오후 3시까지 검토 부탁드립니다.")
    app.update()
    catch_native("클립보드 즉시 스캔 완료", "08_clipboard_msg")
    app.scan_clipboard_now()  # 메시지 상자를 다른 스레드가 찍고 닫을 때까지 여기서 멈춤


@step(600)
def s08_after():
    t = tasks_where(lambda t: "예산 내역서" in t.title)
    if t:
        select(t[0])
    shot("08_clipboard_after")
    before = toplevels()
    app.open_paste_scan_dialog()
    app.update()
    dlg = newest_toplevel(before)
    grow(dlg, 640, 480)
    dlg.text_area.delete("1.0", "end")  # 창이 클립보드 내용을 미리 넣어 둠
    dlg.text_area.insert("1.0", "[공문] 연말 사무실 환경정비 실시 안내\n문서번호: 총무팀-0521\n"
                                "각 부서는 환경정비 담당자를 지정하여 11월 20일까지 회신하여 주시기 바랍니다.")


QUICK_TEXT = "모레 오후 2시까지 출장 결과 보고서 작성"


@step(600)
def s08_paste_shot():
    shot("08_paste_dialog", toplevels()[-1])
    close_all_popups()
    # 빠른 입력: 한 줄로 쓰는 중 (미리보기)
    app._select_date(T.strftime("%Y-%m-%d"))
    app.quick_entry.focus_force()
    app.quick_var.set(QUICK_TEXT)
    app.quick_entry.icursor("end")


@step(600)
def s09q_preview():
    shot("09_quick_preview")
    saved = app.add_quick_entry()
    RECTS["date.quick"] = saved.due_date
    print("quick entry:", saved.title, saved.due_date, saved.due_time, flush=True)


@step(600)
def s09q_added():
    shot("09_quick_added")


@step(600)
def s09_open_new_task():
    app.quick_var.set("")
    before = toplevels()
    app.open_add_task_dialog(FRIDAY.strftime("%Y-%m-%d"))
    app.update()
    dlg = newest_toplevel(before)
    grow(dlg, 580, 620)
    dlg.title_var.set("팀 주간회의 자료 준비")
    dlg.due_time_var.set("10:00")
    dlg.label_var.set(dlg.label_display_map["ROUTINE"])
    dlg.recur_var.set(dlg.recur_map[RecurrenceType.WEEKLY])
    dlg.desc_text.insert("1.0", "매주 금요일 오전 팀 회의 자료 정리")


@step(600)
def s09_shot():
    shot("09_new_task", toplevels()[-1])
    close_all_popups()
    weekly = tasks_where(lambda t: t.title == "주간업무실적 제출")[0]
    select(weekly)


@step(600)
def s10_before():
    shot("10_recurring_before")
    catch_native("반복 일정 자동 생성", "10_recurring_msg")
    app.toggle_selected_task_complete()


@step(600)
def s10_after():
    nxt = tasks_where(lambda t: t.title == "주간업무실적 제출" and t.due_date == (FRIDAY + timedelta(days=7)).strftime("%Y-%m-%d"))
    if nxt:
        select(nxt[0])
    shot("10_recurring_after")


DRAG_TITLE = "협력사 계약 검토 의견 회신"


@step(600)
def s10m_menu():
    """일정 우클릭 메뉴. 메뉴가 떠 있는 동안 앱은 멈추므로, 다른 스레드가 앱+메뉴를 찍고 메뉴에만 Esc를 보냄."""
    task = tasks_where(lambda t: t.title == DRAG_TITLE)[0]
    x, y = center(chip_of(task))
    app_hwnd = hwnd_of(app)
    path = os.path.join(OUT, "10_ctx_menu.png")
    ox, oy = _origin()

    def worker():
        for _ in range(60):
            menus = wincap.process_windows("#32768")
            if menus:
                time.sleep(1.5)  # 메뉴가 서서히 나타나는 효과가 끝나고 모든 항목이 그려질 때까지
                wincap.capture_with(app_hwnd, menus, path)
                r = wincap._frame_rect(menus[0])
                RECTS["10_ctx_menu.menu"] = [r.left - ox, r.top - oy, r.right - r.left, r.bottom - r.top]
                print("captured 10_ctx_menu (with menu)", flush=True)
                for m in menus:
                    wincap.user32.PostMessageW(m, 0x0100, 0x1B, 0)  # WM_KEYDOWN Esc: 이 앱의 메뉴에만
                    wincap.user32.PostMessageW(m, 0x0101, 0x1B, 0)
                return
            time.sleep(0.1)
        print("TIMEOUT menu", flush=True)

    threading.Thread(target=worker, daemon=True).start()
    app._on_task_right_click(Ev(x, y), task, task.due_date)  # 메뉴가 닫힐 때까지 여기서 멈춤
    record_main_rects("10_ctx_menu")
    RECTS["date.drag_src"] = task.due_date


@step(600)
def s10d_drag():
    """일정 칩을 끌어 이틀 뒤 칸 위에 둔 상태 (끄는 중 표시 + 놓을 칸 강조)."""
    task = tasks_where(lambda t: t.title == DRAG_TITLE)[0]
    src = task.due_date
    dst = (date.fromisoformat(src) + timedelta(days=2)).strftime("%Y-%m-%d")
    sx, sy = center(chip_of(task))
    dx, dy = center(cell_widget(dst))
    app._chip_press(Ev(sx, sy), task, src)
    for k in range(1, 11):
        app._chip_motion(Ev(sx + (dx - sx) * k // 10, sy + (dy - sy) * k // 10))
        app.update()
    record_main_rects("10_drag")
    ghost = app._drag["ghost"]
    ghost.update()
    wincap.capture_with(hwnd_of(app), [hwnd_of(ghost)], os.path.join(OUT, "10_drag.png"))
    print("captured 10_drag (with ghost)", flush=True)
    RECTS["date.drag_dst"] = dst
    app._demo_drop = (dx, dy)


@step(600)
def s10d_drop():
    app._chip_release(Ev(*app._demo_drop))
    app.update()
    moved = tasks_where(lambda t: t.title == DRAG_TITLE)[0]
    print("dragged:", RECTS["date.drag_src"], "->", moved.due_date, flush=True)
    shot("10_drag_after")
    app.switch_view_mode("AGENDA")


@step(800)
def s11_agenda():
    shot("11_agenda")
    app.search_var.set("회신")


@step(800)
def s12_search():
    shot("12_search")
    app.search_var.set("")
    app.label_filter_var.set(cfg["labels"]["PORTAL"]["name"])
    app.status_filter_var.set("미완료 전체")
    app.refresh_all()


@step(800)
def s12_filter():
    shot("12_filter")
    app._reset_filters()
    app.switch_view_mode("MONTH")
    before = toplevels()
    app.open_stats_dialog()
    app.update()
    dlg = newest_toplevel(before)
    dlg.update()
    w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    dlg.geometry(f"+{(1920 - w) // 2}+{max(0, (1080 - h) // 2)}")
    dlg.update()


@step(800)
def s12_stats():
    shot("12_stats", toplevels()[-1])
    close_all_popups()
    due = tasks_where(lambda t: t.due_date == T.strftime("%Y-%m-%d") and not t.is_completed)
    app._show_reminder_popup_safe(due[:2])
    app.update()
    p = app._active_reminder_popup
    grow(p, 620, 400, x=1920 - int(620 * SCALE) - 30, y=1080 - int(400 * SCALE) - 70)


@step(600)
def s13_shot():
    shot("13_reminder", app._active_reminder_popup)
    close_all_popups()
    fake_file_dialog([os.path.join(FILES, "inspect", f) for f in os.listdir(os.path.join(FILES, "inspect"))])
    before = toplevels()
    app.open_import_check_dialog()
    app.update()
    grow(newest_toplevel(before), 760, 560)


@step(600)
def s14_shot():
    shot("14_inspect", toplevels()[-1])
    close_all_popups()
    app._select_date(T.strftime("%Y-%m-%d"))


@step(600)
def s15_final():
    shot("15_main_final")


def finish():
    import json

    RECTS["date.today"] = T.strftime("%Y-%m-%d")
    # 결과 파일 rects.json: 장면별 위젯 위치·날짜 (build_composition.py가 강조·확대 위치로 씀)
    json.dump(RECTS, open(os.path.join(OUT, "rects.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    kill_msgs()
    app.scheduler.stop()
    app.destroy()
    print("done", flush=True)


app.after(2500, run_next)
app.after(330000, lambda: (print("GLOBAL TIMEOUT", flush=True), kill_msgs(), os._exit(1)))
app.mainloop()
