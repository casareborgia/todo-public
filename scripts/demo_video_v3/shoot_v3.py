"""시연영상 v3 장면 캡처: 실제 앱(v1.4.0 + 11.6 보완)을 가상 데이터로 띄워 창만 PNG로 찍습니다.

v2 장면(scripts/demo_video_v2/shoot_v2.py)에 11.6 보완 장면을 더함:
문서 본문 판정(등록·확인 필요·제외, 제외된 쪽지·문서), 기간 업무(쪽지의 ~ 기간, 시작일 입력, 달력 여러 칸, 반복 기간의 다음 기간).

v1 장면(scripts/demo_video/shoot_all.py)에 v1.3.0~v1.4.0 장면을 더함:
쪽지 확인 필요·제외된 쪽지, 업무관리시스템 배부 등록·문서 열기·같은 일 합치기, 반복 미리보기,
여러 건 삭제, F1 단축키, 창 크기별 캘린더 칸 맞춤.
- 쪽지·배부 목록·문서 창은 수집기에 가짜 읽기 함수를 넣어(테스트와 같은 방식) 앱이 실제 경로로 등록하게 함.
- 앱 코드는 고치지 않습니다. 앱 창·팝업만 PrintWindow로 찍으므로 바탕화면이나 다른 창은 담기지 않습니다.

사용: python scripts/demo_video/make_demo_data.py C:\\tc\\video4
      python shoot_v3.py [작업 폴더, 기본 C:\\tc\\video4]
- 결과: <작업 폴더>\\shots\\*.png, rects.json
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
BASE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else r"C:\tc\video4")
APP = os.path.join(BASE, "demo_app")
FILES = os.path.join(BASE, "demo_files")
OUT = os.path.join(BASE, "shots")
SCALE = float(os.environ.get("DEMO_SCALE", "1.25"))  # 1280x820 창을 1600x1025로 (글자를 키워 1080p에서 선명하게)

sys.path.insert(0, APP)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "demo_video"))  # wincap
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
from collectors.ezq_window import read_message_windows  # noqa: E402
from collectors.workmgmt_collector import WorkMgmtDocCollector  # noqa: E402
from collectors.workmgmt_window import DocWindowResult, ListWindowResult  # noqa: E402
from parsers.workmgmt_list import ListRow  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus  # noqa: E402
from parsers.portal_parser import PortalAndMessengerParser  # noqa: E402
from parsers.rule_parser import RuleBasedExtractor  # noqa: E402

os.chdir(APP)  # main.py가 작업 폴더를 바꿨을 수 있으므로 다시 시연용 사본으로
assert os.path.abspath(os.getcwd()) == os.path.abspath(APP)

cfg = entry.load_config(os.path.join(APP, "config.json"))
db = DatabaseManager(os.path.join(APP, "todo_data.db"))
parser = PortalAndMessengerParser(RuleBasedExtractor(cfg))
folder = FolderWatcherCollector(db=db, parser=parser, watch_folders=[], supported_extensions=cfg.get("supported_extensions"))
INJ_MSGS = {}   # 창 번호 → 쪽지 본문 (시연용: 실제 쪽지 창 대신 수집기에 넘김)
LIST_ROWS = []  # 결재시행 목록 재현 행
DOC_WINS = {}   # 창 번호 → (창 제목, 본문)


def ezq_reader(cls):
    return list(read_message_windows(cls)) + list(INJ_MSGS.items())


def list_reader(_cls):
    return [ListWindowResult(9001, "결재시행 (시연용 재현)", list(LIST_ROWS))] if LIST_ROWS else []


def doc_reader(_cls, skip=None):
    out = []
    for hwnd, (title, text) in DOC_WINS.items():
        if skip and skip(hwnd, title):
            continue
        out.append(DocWindowResult(hwnd, title, "TEXT", text))
    return out


ezq = EzqMessageCollector(db=db, parser=parser, window_class=cfg["ezq_window_class"], reader=ezq_reader)
wm = WorkMgmtDocCollector(db=db, parser=parser, reader=doc_reader, list_reader=list_reader)
app = app_window.TodoCalendarApp(cfg, db, folder, ClipboardCollector(db=db, parser=parser), ezq_collector=ezq,
                                 workmgmt_collector=wm, config_path=os.path.join(APP, "config.json"))
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
NEXT_MON = T + timedelta(days=7 - T.weekday())  # 쪽지 기한 (v1.3.0 판정은 "오전까지"를 기한으로 못 읽어 "까지"만 씀)
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
                          os.path.join(HERE, "..", "demo_video", "msgwin.ps1"), "-Sender", sender, "-Sent", sent, "-BodyFile", path])
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
             f"안녕하세요, 총무팀 김철수입니다.<br>{NEXT_MON.month}/{NEXT_MON.day}(월)까지 교육 결과 보고서 작성 부탁드립니다.")
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


# ---------------------------------------------------------------- v2 장면 (v1.3.0 ~ v1.4.0)
WD = "월화수목금토일"
D3 = T + timedelta(days=3)
while D3.weekday() >= 5:
    D3 += timedelta(days=1)
MERGE_MSG = (f"감사실 내부통제 자가진단 공문 왔습니다. {D3.month}/{D3.day}({WD[D3.weekday()]})까지 처실별로 "
             "제출해야 하니 작성 부탁드립니다.\n\n보낸사람 : 이영희 (가상)")
D_EDU = T + timedelta(days=8)
EDU_BODY = f"""가상 기관 머리말
수신자 부서별처실
제 목
2027년 정보보호 교육 계획 제출
1. 관련: 정보보호팀-80(가상)
2. 2027년 부서별 정보보호 교육 계획을 붙임 양식에 따라 작성하여 제출하여 주시기 바랍니다.
3. 회신기한/방법 : '{D_EDU:%y}.{D_EDU.month}.{D_EDU.day}({WD[D_EDU.weekday()]})/처실별 공문제출
붙임 1. 교육 계획 양식 1부. 끝.
시행 정보보호팀-88 ({T:%Y.%m.%d}.)
"""


D_REQ = T + timedelta(days=6)
DOC_REQ = f"""가상 기관 머리말
수신자 부서별처실
제 목
2027년 부서별 안전점검 결과 제출
1. 관련: 안전팀-40(가상)
2. 부서별 안전점검을 실시하고 그 결과를 붙임 양식에 작성하여 '{D_REQ:%y}.{D_REQ.month}.{D_REQ.day}({WD[D_REQ.weekday()]})까지 회신하여 주시기 바랍니다.
붙임 1. 점검 결과 양식 1부. 끝.
시행 안전팀-52 ({T:%Y.%m.%d}.)
"""
DOC_REVIEW = f"""가상 기관 머리말
수신자 부서별처실
제 목
전자결재 서비스 확대 시행 안내
1. 전자결재 서비스 확대 시행을 안내해드립니다.
2. 전환 일정을 유의하시기 바랍니다.
3. 별도 양식이 필요하면 사전 협의서를 제출해주시기 바람
시행 정보화팀-31 ({T:%Y.%m.%d}.)
"""
DOC_NOTICE = f"""가상 기관 머리말
수신자 부서별처실
제 목
사무실 에너지 절약 지침 송부
1. 동절기 사무실 에너지 절약 지침을 알려드리오니 업무에 참고하여 주시기 바랍니다.
붙임 1. 에너지 절약 지침 1부. 끝.
시행 총무팀-77 ({T:%Y.%m.%d}.)
"""


def lrow(title, no, role="업무담당자", kind="수신", dept="가상감사실", hm="09:12"):
    return ListRow(title, no, kind, role, dept, f"{T:%Y-%m-%d} {hm}")


def find_title(part):
    got = [t for t in db.query_tasks(needs_review=None) if part in t.title]
    return got[0] if got else None


@step(800)
def v2_msg_first():
    """동료 쪽지로 먼저 받은 일 (나중에 배부 문서와 합쳐짐)."""
    app._select_date(T.strftime("%Y-%m-%d"))
    INJ_MSGS[7001] = MERGE_MSG
    return lambda nxt: wait_for(lambda: find_title("자가진단"), nxt)


@step(800)
def v2_list_open():
    """결재시행 목록을 연 상태: 배부 2건(업무담당자), 1건(업무관리자), 배부 아닌 행 1건."""
    print("msg task:", find_title("자가진단").title, flush=True)
    LIST_ROWS[:] = [
        lrow("2026년 내부통제 자가진단 결과 제출 시행 알림", "감사실-456"),
        lrow("2027년 정보보호 교육 계획 제출", "정보보호팀-88", dept="가상정보보호팀", hm="09:30"),
        lrow("부서별 사무공간 재배치 의견 조회", "총무팀-301", role="업무관리자", dept="가상총무팀", hm="10:02"),
        lrow("11월 부서 회의 결과 보고", "기획팀-77", role="기안", kind="생산", dept="기획팀", hm="08:40"),
    ]
    log.append(("list_open", time.time()))
    return lambda nxt: wait_for(lambda: find_title("정보보호 교육") and find_title("사무공간"), nxt)


@step(1500)
def v2_list_registered():
    print("list registered after %.1fs" % (time.time() - log[-1][1]), flush=True)
    edu = find_title("정보보호 교육")
    RECTS["date.edu_est"] = edu.due_date
    select(edu)
    shot("v2_list_registered")


@step(600)
def v2_merged():
    t = find_title("자가진단")
    print("merged task:", t.title, "|", t.source_ref, flush=True)
    select(t)
    RECTS["date.merged"] = t.due_date
    shot("v2_merged")
    DOC_WINS[8001] = ("정보보호팀-88호 2027년 정보보호 교육 계획 제출 - 페이지 1 (시연용 재현)", EDU_BODY)
    return lambda nxt: wait_for(lambda: (find_title("정보보호 교육") and not find_title("정보보호 교육").due_estimated), nxt)


@step(800)
def v2_doc_opened():
    edu = find_title("정보보호 교육")
    print("doc confirmed due:", edu.due_date, "estimated:", edu.due_estimated, flush=True)
    RECTS["date.edu_doc"] = edu.due_date
    select(edu)
    shot("v2_doc_opened")
    # 쪽지 과탐 방지: 애매한 요청 1건(확인 필요), 업무 아닌 쪽지 3건(제외)
    INJ_MSGS[7002] = "우선 낙찰자랑 낙찰율 등 관련 내용 좀 공유해주세요~\n\n보낸사람 : 김철수 (가상)"
    INJ_MSGS[7003] = "[ 펌뱅킹지급 알림 ]\n\n보낸사람 : 회계팀\n\n출장비가 지급되었습니다."
    INJ_MSGS[7004] = "다음 주 회의 참석자 명단이 어떻게 되나요?\n\n보낸사람 : 홍길동 (가상)"
    INJ_MSGS[7005] = "오늘 점심 같이 하실 분 계신가요? ㅎㅎ\n\n보낸사람 : 이영희 (가상)"
    # 11.6 보완: 배부받지 않고 연 문서 3건 → 본문 판정 (등록 / 확인 필요 / 제외)
    DOC_WINS[8002] = ("안전팀-52호 2027년 부서별 안전점검 결과 제출 - 페이지 1 (시연용 재현)", DOC_REQ)
    DOC_WINS[8003] = ("정보화팀-31호 전자결재 서비스 확대 시행 안내 - 페이지 1 (시연용 재현)", DOC_REVIEW)
    DOC_WINS[8004] = ("총무팀-77호 사무실 에너지 절약 지침 송부 - 페이지 1 (시연용 재현)", DOC_NOTICE)
    return lambda nxt: wait_for(lambda: len(db.list_ezq_messages(verdict="제외")) >= 3
                                and len(db.list_workmgmt_judgments()) >= 3, lambda: app.after(3500, nxt))


@step(800)
def v3_doc_registered():
    print("doc judgments:", [j.get("verdict") for j in db.list_workmgmt_judgments()], flush=True)
    t = find_title("안전점검")
    print("doc task:", t.title, t.due_date, flush=True)
    RECTS["date.doc_req"] = t.due_date
    select(t)
    shot("v3_doc_registered")


@step(800)
def v2_review():
    app.refresh_all()
    print("review count:", db.count_review_tasks(), "excluded:", len(db.list_ezq_messages(verdict="제외")), flush=True)
    shot("v2_main_review_btn")
    before = toplevels()
    app.open_ezq_review_dialog()
    app.update()
    grow(newest_toplevel(before), 860, 520)


@step(600)
def v2_review_shot():
    shot("v2_review_queue", toplevels()[-1])
    close_all_popups()
    before = toplevels()
    app.open_ezq_log_dialog()
    app.update()
    dlg = newest_toplevel(before)
    grow(dlg, 900, 560)
    # 제외된 통보 공문(문서) 행을 골라 판정 근거가 보이게
    docs = [i for i in dlg.tree.get_children() if i.startswith("문서:")]
    if docs:
        dlg.tree.selection_set(docs[0])
        dlg.tree.see(docs[0])
        dlg.update()


@step(600)
def v2_dismissed_shot():
    shot("v2_dismissed_msgs", toplevels()[-1])
    close_all_popups()
    INJ_MSGS.clear()
    weekly = [t for t in db.query_tasks() if t.title == "주간업무실적 제출" and not t.is_completed]
    if weekly:
        select(weekly[0])
        RECTS["date.weekly"] = weekly[0].due_date


@step(600)
def v2_recur():
    shot("v2_recur_preview")
    app._select_date(T.strftime("%Y-%m-%d"))
    app.update()
    kids = app.day_tree.get_children()
    app.day_tree.selection_set(kids[:2])
    app.day_tree.focus_set()
    app.update()
    record_main_rects("v2_multi_delete")
    app_hwnd = hwnd_of(app)
    path = os.path.join(OUT, "v2_multi_delete.png")

    def worker():
        for _ in range(100):
            hwnd = wincap.find("삭제 확인")
            if hwnd:
                time.sleep(0.8)
                wincap.capture_with(app_hwnd, [hwnd], path)
                print("captured v2_multi_delete (with dialog)", flush=True)
                wincap.user32.PostMessageW(hwnd, 0x0111, 6, 0)  # WM_COMMAND IDYES
                return
            time.sleep(0.1)
        print("TIMEOUT delete dialog", flush=True)

    threading.Thread(target=worker, daemon=True).start()
    print("deleting:", [app._right_task_map[k].title for k in kids[:2]], flush=True)
    app.delete_selected_task()  # 확인 창이 닫힐 때까지 여기서 멈춤


@step(600)
def v2_after_delete():
    shot("v2_after_delete")
    catch_native("단축키 안내", "v2_shortcuts")
    app._show_shortcuts()


# ---------------------------------------------------------------- v3 장면: 기간 업무
P_S = NEXT_MON + timedelta(days=7)          # 쪽지로 받은 교육 기간 (다다음 주 월~수)
P_E = P_S + timedelta(days=2)
C_S = NEXT_MON                              # 직접 넣은 기간 업무 (다음 주 월~수)
C_E = C_S + timedelta(days=2)
R_S = T - timedelta(days=1)                 # 매주 반복 기간 (어제~오늘) → 완료하면 다음 주 기간이 열림
PERIOD_MSG = (f"신규 정보보안 교육이 {P_S.month}/{P_S.day}~{P_E.month}/{P_E.day} 진행됩니다. "
              "기간 중 하루 참석 부탁드립니다." + chr(10) * 2 + "보낸사람 : 홍길동 (가상)")


def ymd(x):
    return x.strftime("%Y-%m-%d")


@step(800)
def v3_period_setup():
    close_all_popups()
    course = TaskItem(title="신규 직원 직무교육 운영", description="교육장 준비, 출석부, 교재 배부",
                      source=TaskSource.MANUAL, source_ref="직접 등록", start_date=ymd(C_S), due_date=ymd(C_E),
                      due_time="18:00", label="MANUAL", priority=2)
    db.add_task(course, skip_if_duplicate=False)
    weekly = TaskItem(title="주간 시설 안전점검", description="매주 시설 점검표 작성",
                      source=TaskSource.MANUAL, source_ref="정기 업무", start_date=ymd(R_S), due_date=ymd(T),
                      due_time="17:00", label="ROUTINE", priority=2, recurrence=RecurrenceType.WEEKLY)
    wid, _ = db.add_task(weekly, skip_if_duplicate=False)
    nid = db.set_task_status(wid, TaskStatus.COMPLETED)
    print("weekly period done:", wid, "next:", nid, flush=True)
    INJ_MSGS.clear()
    INJ_MSGS[7010] = PERIOD_MSG
    app.refresh_all()
    return lambda nxt: wait_for(lambda: tasks_where(lambda t: t.start_date == ymd(P_S)), nxt)


@step(800)
def v3_period_msg():
    t = tasks_where(lambda t: t.start_date == ymd(P_S))[0]
    print("period msg task:", t.title, t.start_date, t.due_date, flush=True)
    RECTS["date.period_msg"] = t.due_date
    select(t)
    shot("v3_period_msg")
    course = tasks_where(lambda t: t.title == "신규 직원 직무교육 운영")[0]
    before = toplevels()
    app.open_edit_task_dialog(course)
    app.update()
    grow(newest_toplevel(before), 580, 680)


@step(600)
def v3_period_dialog():
    shot("v3_period_dialog", toplevels()[-1])
    close_all_popups()
    course = tasks_where(lambda t: t.title == "신규 직원 직무교육 운영")[0]
    RECTS["date.period_course"] = course.due_date
    select(course)


@step(600)
def v3_period_month():
    shot("v3_period_month")
    nxt = [t for t in tasks_where(lambda t: t.title == "주간 시설 안전점검") if not t.is_completed]
    print("next weekly period:", [(t.start_date, t.due_date) for t in nxt], flush=True)
    RECTS["date.period_next"] = nxt[0].due_date
    select(nxt[0])


@step(600)
def v3_period_next():
    shot("v3_period_next")


@step(800)
def v2_fit():
    sizes = [("min", int(1060 * SCALE), int(680 * SCALE)), ("def", W, H), ("max", 1920, 1040)]

    def run(i=0):
        if i >= len(sizes):
            app.geometry(f"{W}x{H}+0+0")
            return
        name, w, h = sizes[i]
        app.geometry(f"{w}x{h}+0+0")
        app.update()
        app.after(1500, lambda: (app.update(), shot(f"v2_fit_{name}"), run(i + 1)))

    return lambda nxt: (run(), app.after(6500, nxt))


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
app.after(600000, lambda: (print("GLOBAL TIMEOUT", flush=True), kill_msgs(), os._exit(1)))
app.mainloop()
