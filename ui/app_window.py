"""메인 데스크톱 윈도우 (월간 캘린더 뷰 & 일정 목록 Agenda 뷰, 검색/필터, 상세 패널)."""

import calendar
from datetime import date, datetime, timedelta
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont
from typing import Callable, Dict, List, Optional

from collectors.clipboard_collector import ClipboardCollector
from collectors.ezq_collector import EzqMessageCollector, ezq_key_of
from collectors.workmgmt_collector import WORKMGMT_LIST_SOURCE_REF, WorkMgmtDocCollector
from collectors.folder_watcher import FolderWatcherCollector, WatchFolder
from core.config_store import save_config_values
from core.database import DatabaseManager
from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus
from core.scheduler import AppScheduler
from core.version import APP_VERSION
from parsers.import_check import check_files
from parsers.quick_entry import parse_quick_entry
from parsers.rule_parser import RuleBasedExtractor
from ui.briefing_popup import DeadlineReminderPopup, StartupBriefingPopup
from core.recurrence import drag_due_date, next_task_message, preview_tasks, span_dates
from ui.ezq_log_dialog import EzqLogDialog, EzqReviewDialog, review_handler
from ui.folder_dialogs import FolderSettingsDialog, ImportResultDialog
from ui.calendar_fit import chips_that_fit, fit_title
from ui.shortcuts import HINT_TEXT, delete_confirm_message, shortcut_help_text, tasks_in_selection
from ui.startup_helper import is_startup_enabled, set_startup_enabled
from ui.stats_dialog import WorkStatsDialog
from ui.task_dialog import ImportCheckDialog, QuickPasteScanDialog, TaskEditDialog

WEEKDAY_KO = "월화수목금토일"


def short_date(date_str: str) -> str:
    """'2026-11-03' → '11.3(화)'. 형식이 틀리면 그대로."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return date_str or ""
    return f"{d.month}.{d.day}({WEEKDAY_KO[d.weekday()]})"


# 폴더 확인 한 번에 쓸 최대 시간(초). 넘으면 남은 파일은 다음 확인(15초 뒤)에 이어서 처리
FOLDER_SCAN_BUDGET_SEC = 2.0
# 일정 목록 '전체 보기'에 보여 줄 완료 업무: 마감이 이 날수 안인 것만 (오래 쓰면 수천 줄이 되어 느려짐)
AGENDA_COMPLETED_DAYS = 90


class ClickPairDetector:
    """같은 대상을 빠르게 두 번 눌렀는지 판단.

    캘린더 일정 칸은 첫 클릭에서 화면을 다시 그리며 새 위젯으로 바뀌므로,
    Tk의 <Double-1>(두 클릭이 같은 위젯이어야 함)은 뜨지 않는다. 그래서 대상 키와 이벤트 시각으로 직접 판단한다.
    """

    def __init__(self, interval_ms: int = 500):
        self.interval_ms = interval_ms
        self._last: Optional[tuple] = None

    def click(self, key, time_ms: int) -> bool:
        last, self._last = self._last, (key, time_ms)
        if last and last[0] == key and 0 <= time_ms - last[1] <= self.interval_ms:
            self._last = None  # 세 번째 클릭이 또 '두 번 누름'이 되지 않게
            return True
        return False


def month_to_show(year: int, month: int, date_str: str) -> tuple:
    """date_str이 (year, month) 달력에 보이면 그대로, 아니면 그 날짜의 달."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return year, month
    first = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)[0][0]  # _render_month_calendar와 같은 일요일 시작
    if first <= d <= first + timedelta(days=41):  # 달력은 늘 6주(42칸)
        return year, month
    return d.year, d.month


def postpone_targets(task: TaskItem, today: Optional[date] = None) -> List[tuple]:
    """우클릭 '미루기' 후보 [(메뉴 이름, 새 마감일)].

    하루 미루기는 기한이 이미 지났으면 오늘 기준, 아니면 원래 기한 기준으로 하루 뒤.
    """
    today = today or date.today()
    try:
        due = datetime.strptime(task.due_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        due = today
    base = max(due, today)
    one_day = date.fromordinal(base.toordinal() + 1)
    next_monday = date.fromordinal(today.toordinal() + 7 - today.weekday())
    out = [(f"⏩ 하루 미루기 (→ {short_date(one_day.isoformat())})", one_day.isoformat())]
    if next_monday != one_day:
        out.append((f"⏩ 다음 주 월요일로 (→ {short_date(next_monday.isoformat())})", next_monday.isoformat()))
    return out



def bg_new_tasks_message(created: List[TaskItem]) -> str:
    """자동 감지로 새로 등록한 업무 알림 문구. 배부 목록에서 받은 문서는 열어야 판정된다고 덧붙임."""
    review = sum(1 for t in created if t.needs_review)
    parts = []
    if len(created) - review:
        parts.append(f"{len(created) - review}건 캘린더 등록")
    if review:
        parts.append(f"{review}건 확인 필요")
    text = f"신규 문서/메시지 자동 감지: {', '.join(parts)}"
    distributed = sum(1 for t in created if WORKMGMT_LIST_SOURCE_REF in (t.source_ref or ""))
    if distributed:
        text += f" (배부받은 문서 {distributed}건: 문서를 열면 업무인지 판정하고 기한을 정합니다)"
    return text

def list_doc_judged_message(confirmed: List[TaskItem], removed: List[TaskItem]) -> str:
    """배부 문서를 열어 본문으로 판정한 결과 알림 문구."""
    parts = []
    registered = sum(1 for t in confirmed if not t.needs_review)
    if registered:
        parts.append(f"{registered}건 캘린더 등록")
    if len(confirmed) - registered:
        parts.append(f"{len(confirmed) - registered}건 확인 필요 유지")
    if removed:
        parts.append(f"{len(removed)}건 제외 (안내·통보 문서, [🧾 제외된 쪽지·문서]에서 되살릴 수 있음)")
    return f"배부 문서 본문 판정: {', '.join(parts)}"


class TodoCalendarApp(tk.Tk):
    def __init__(
        self,
        config: dict,
        db: DatabaseManager,
        folder_collector: FolderWatcherCollector,
        clipboard_collector: ClipboardCollector,
        is_startup_mode: bool = False,
        config_path: str = "config.json",
        ezq_collector: Optional[EzqMessageCollector] = None,
        workmgmt_collector: Optional[WorkMgmtDocCollector] = None,
    ):
        super().__init__()
        self.config_data = config
        self.config_path = config_path
        self.db = db
        self.folder_collector = folder_collector
        self.clipboard_collector = clipboard_collector
        self.ezq_collector = ezq_collector
        self.workmgmt_collector = workmgmt_collector
        self.is_startup_mode = is_startup_mode

        self.labels_config: Dict[str, dict] = self.config_data.get("labels", {})
        self.current_year = date.today().year
        self.current_month = date.today().month
        self.selected_date_str = date.today().strftime("%Y-%m-%d")
        self.view_mode = "MONTH"  # "MONTH" or "AGENDA"
        self.selected_task: Optional[TaskItem] = None
        self._right_task_map: Dict[str, TaskItem] = {}
        self._agenda_task_map: Dict[str, TaskItem] = {}
        self._active_tree: Optional[ttk.Treeview] = None  # 마지막으로 커서가 있던 업무 목록 (삭제 대상)
        self._active_reminder_popup: Optional[DeadlineReminderPopup] = None
        # 캘린더 칸(위젯 경로) → 날짜. 끌어다 놓을 때 마우스 아래 칸의 날짜를 찾는 데 씀
        self._cell_dates: Dict[str, str] = {}
        # 달력 칸 부품 재사용 (다시 그릴 때 지우고 새로 만들지 않음)
        self._cell_parts: List[Optional[dict]] = []
        self._chip_data: Dict[str, tuple] = {}     # 칩 → (업무, 날짜)
        self._widget_opts: Dict[str, dict] = {}    # 부품 → 마지막으로 보낸 설정
        self._fit_cache: Dict[tuple, str] = {}     # (글자, 너비) → 잘라 낸 제목
        self._width_cache: Dict[tuple, int] = {}
        self._drag: Optional[dict] = None
        self._chip_clicks = ClickPairDetector()
        self._drop_cell: Optional[tuple] = None
        self._refresh_pending = False
        # 캘린더 칸 크기에 맞춰 업무 개수를 정함 (글자 높이 측정값, 마지막으로 그린 달력 크기, 다시 그리기 예약)
        self._chip_metrics: Optional[dict] = None
        self._cal_size: Optional[tuple] = None
        self._cal_fit_job: Optional[str] = None
        self._ctx_menu: Optional[tk.Menu] = None
        self._quick_flash_job: Optional[str] = None
        self._quick_extractor: Optional[RuleBasedExtractor] = None
        self._period_check_day: Optional[date] = None  # 반복 기간 만료 확인을 마지막으로 한 날 (하루 한 번)

        app_title = self.config_data.get("app_name", "사내 업무 캘린더 & 투두 매니저")
        # 버전은 프로그램 안의 값 (config.json은 예전 버전에서 가져온 파일일 수 있어 쓰지 않음)
        self.title(f"{app_title} v{APP_VERSION}")
        self.geometry("1280x820")
        self.minsize(1060, 680)
        self.configure(bg="#F1F5F9")

        self._setup_styles()
        self._build_layout()
        self._bind_shortcuts()

        # 초기 폴더 자동 스캔 실행
        # (큰 폴더를 처음 가져오는 경우 창이 늦게 뜨지 않게 2초까지만, 나머지는 백그라운드 확인에서 이어서)
        initial_new = self.folder_collector.collect_and_register(time_budget=FOLDER_SCAN_BUDGET_SEC)
        if self.ezq_collector:
            initial_new += self.ezq_collector.collect_and_register()
        if self.workmgmt_collector:
            initial_new += self.workmgmt_collector.collect_and_register()
        # 끝날이 지난 반복 기간 업무의 다음 기간을 먼저 만들어 첫 화면에 보이게 함
        self._activate_expired_periods()
        self.refresh_all()

        # 백그라운드 스케줄러 시작 (주기적 폴더 감시 & 기한 도래 알림)
        self.scheduler = AppScheduler(
            db=self.db,
            scan_callback=self._bg_scan_tick,
            reminder_callback=self._bg_reminder_tick,
            scan_interval_sec=int(self.config_data.get("scan_interval_seconds", 15)),
            reminder_interval_sec=int(self.config_data.get("reminder_check_seconds", 30)),
            fast_callback=self._bg_fast_tick if (self.ezq_collector or self.workmgmt_collector) else None,
            fast_interval_sec=int(self.config_data.get("ezq_check_seconds", 3)),
        )
        # mainloop가 돈 뒤에 시작: 그 전에 백그라운드 스레드가 self.after를 부르면 Tk 오류로 첫 알림·갱신이 빠짐
        self.after(0, self.scheduler.start)

        # 출근 후 프로그램 실행 시 브리핑 팝업 자동 호출 (구상 2-3)
        if self.config_data.get("show_startup_briefing", True) or self.is_startup_mode:
            # 확인 필요 업무는 브리핑 목록에 없으므로 '신규' 건수에서도 뺌
            new_count = sum(1 for t in initial_new if not t.needs_review)
            self.after(350, lambda: self.open_briefing_popup(new_count))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", font=("Malgun Gothic", 9), rowheight=26)
        style.configure("Treeview.Heading", font=("Malgun Gothic", 9, "bold"))

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-n>", lambda _e: self.open_add_task_dialog())
        self.bind("<Control-Shift-V>", lambda _e: self.scan_clipboard_now())
        self.bind("<Control-Shift-v>", lambda _e: self.scan_clipboard_now())
        self.bind("<F5>", lambda _e: self.scan_folders_now())
        # 단축키 목록은 ui/shortcuts.py SHORTCUTS (F1 안내와 같이 고칠 것)
        window_keys = {
            ("<Control-l>", "<Control-L>"): lambda: self._focus_quick_entry(),
            ("<Control-Key-1>",): lambda: self._focus_tree(self.day_tree),
            ("<Control-Key-2>",): self._focus_agenda_list,
            ("<Alt-Left>",): self._prev_month,
            ("<Alt-Right>",): self._next_month,
            ("<Control-t>", "<Control-T>"): self._go_today,
            ("<F1>",): self._show_shortcuts,
        }
        for seqs, action in window_keys.items():
            for seq in seqs:
                self.bind(seq, lambda _e, a=action: (a(), "break")[1])
        for tree, task_map in ((self.day_tree, self._right_task_map), (self.agenda_tree, self._agenda_task_map)):
            self._bind_tree_keys(tree, task_map)

    def _bind_tree_keys(self, tree: ttk.Treeview, task_map: Dict[str, TaskItem]) -> None:
        """업무 목록 안에서만 쓰는 키. 창 전체에 걸면 입력 칸에서 Delete를 눌러도 업무가 지워지므로 목록에만 검."""

        def run(action):
            def handler(_e):
                self._active_tree = tree
                action()
                return "break"

            return handler

        def focused_task() -> Optional[TaskItem]:
            t = task_map.get(tree.focus())
            if t is None or tree.focus() not in tree.selection():
                chosen = tasks_in_selection(tree.selection(), task_map)
                t = chosen[0] if chosen else None
            return t

        def edit():
            t = focused_task()
            if t:
                self.open_edit_task_dialog(t)

        def toggle():
            t = focused_task()
            if t:
                self._toggle_task_complete(t)
                self._focus_tree(tree)

        def select_all():
            rows = tree.get_children()
            if rows:
                tree.selection_set(rows)

        def clear():
            tree.selection_remove(tree.selection())
            self._show_task_detail(None)

        tree.bind("<FocusIn>", lambda _e: setattr(self, "_active_tree", tree), add="+")
        for seq in ("<Delete>",):
            tree.bind(seq, run(self.delete_selected_task))
        for seq in ("<Return>", "<KP_Enter>"):
            tree.bind(seq, run(edit))
        tree.bind("<space>", run(toggle))
        for seq in ("<Control-a>", "<Control-A>"):
            tree.bind(seq, run(select_all))
        tree.bind("<Escape>", run(clear))

    def _focus_tree(self, tree: ttk.Treeview) -> None:
        """목록으로 키보드 커서를 옮김. 고른 줄이 없으면 첫 줄을 고름."""
        tree.focus_set()
        self._active_tree = tree
        sel = tree.selection()
        rows = tree.get_children()
        if not sel and rows:
            tree.selection_set(rows[0])
            sel = (rows[0],)
        if sel and tree.focus() not in sel:
            tree.focus(sel[0])
        if sel:
            tree.see(tree.focus() or sel[0])

    def _focus_agenda_list(self) -> None:
        if self.view_mode != "AGENDA":
            self.switch_view_mode("AGENDA")
        self._focus_tree(self.agenda_tree)

    def _show_shortcuts(self) -> None:
        messagebox.showinfo("단축키 안내", shortcut_help_text(), parent=self)

    def _build_layout(self) -> None:
        # 1. 최상단 헤더 및 액션 툴바
        top_bar = tk.Frame(self, bg="#0F172A", padx=16, pady=10)
        top_bar.pack(fill=tk.X)

        title_box = tk.Frame(top_bar, bg="#0F172A")
        title_box.pack(side=tk.LEFT)
        self.title_lbl = tk.Label(
            title_box,
            text="🗓️ 사내 개인용 투두리스트 & 캘린더",
            font=("Malgun Gothic", 14, "bold"),
            bg="#0F172A",
            fg="white",
        )
        self.title_lbl.pack(anchor="w")
        folder_row = tk.Frame(title_box, bg="#0F172A")
        folder_row.pack(anchor="w")
        self.status_sub_lbl = tk.Label(
            folder_row,
            text=self._watch_folder_summary(),
            font=("Malgun Gothic", 8),
            bg="#0F172A",
            fg="#94A3B8",
        )
        self.status_sub_lbl.pack(side=tk.LEFT)
        tk.Button(
            folder_row,
            text="⚙ 감시 폴더 설정",
            font=("Malgun Gothic", 8),
            bg="#1E293B",
            fg="#E2E8F0",
            activebackground="#334155",
            activeforeground="white",
            relief=tk.FLAT,
            padx=6,
            pady=0,
            cursor="hand2",
            command=self.open_folder_settings_dialog,
        ).pack(side=tk.LEFT, padx=(8, 0))
        small_btn = dict(
            font=("Malgun Gothic", 8), bg="#1E293B", fg="#E2E8F0", activebackground="#334155",
            activeforeground="white", relief=tk.FLAT, padx=6, pady=0, cursor="hand2",
        )
        if self.ezq_collector:
            # 정리할 '확인 필요' 쪽지가 있을 때만 보임 (건수는 refresh_all에서 갱신)
            self.review_btn = tk.Button(
                folder_row, text="", command=self.open_ezq_review_dialog, **{**small_btn, "bg": "#7C3AED"}
            )
            self._review_btn_anchor = tk.Button(
                folder_row, text="🧾 제외된 쪽지·문서", command=self.open_ezq_log_dialog, **small_btn
            )
            self._review_btn_anchor.pack(side=tk.LEFT, padx=(6, 0))
        # '업무 아님' 직후 잠깐 보이는 되돌리기 버튼
        self.undo_btn = tk.Button(folder_row, text="↩ 되돌리기", command=self.undo_last_dismiss, **{**small_btn, "bg": "#B45309"})
        self._undo_job = None
        self._last_dismissed = None  # (지운 업무, 쪽지 키 또는 배부 기록 키, 처리한 수집기)

        # 우측 액션 버튼들
        self.top_bar = top_bar
        self.title_box = title_box
        btn_box = tk.Frame(top_bar, bg="#0F172A")
        btn_box.pack(side=tk.RIGHT)
        self.top_btn_box = btn_box

        # 체크박스는 위아래로 쌓아 최소 창 너비에서 버튼 자리를 더 차지하지 않게 함
        chk_col = tk.Frame(btn_box, bg="#0F172A")
        chk_col.pack(side=tk.RIGHT, padx=(10, 0))
        chk_style = dict(
            font=("Malgun Gothic", 9),
            bg="#0F172A",
            fg="#E2E8F0",
            selectcolor="#1E293B",
            activebackground="#0F172A",
            activeforeground="white",
        )
        self.startup_var = tk.BooleanVar(value=is_startup_enabled())
        self._top_chks: List[tk.Checkbutton] = []
        chk = tk.Checkbutton(
            chk_col, text="출근 시 자동실행", variable=self.startup_var, command=self._toggle_startup, **chk_style
        )
        chk.pack(anchor="w")
        self._top_chks.append(chk)
        if self.ezq_collector:
            self.ezq_var = tk.BooleanVar(value=self.ezq_collector.enabled)
            chk = tk.Checkbutton(
                chk_col, text="EzQ 쪽지 수집", variable=self.ezq_var, command=self._toggle_ezq, **chk_style
            )
            chk.pack(anchor="w")
            self._top_chks.append(chk)
        if self.workmgmt_collector:
            self.workmgmt_var = tk.BooleanVar(value=self.workmgmt_collector.enabled)
            chk = tk.Checkbutton(
                chk_col, text="업무관리 문서 수집", variable=self.workmgmt_var, command=self._toggle_workmgmt, **chk_style
            )
            chk.pack(anchor="w")
            self._top_chks.append(chk)

        # (버튼, 전체 이름, 창이 좁을 때 쓰는 짧은 이름) — _relayout에서 창 너비에 맞춰 바꿔 씀
        self._top_btns: List[tuple] = []
        self._make_top_btn(
            btn_box, "🔔 출근 브리핑", "#D97706", lambda: self.open_briefing_popup(0), short="🔔 브리핑"
        ).pack(side=tk.RIGHT, padx=4)
        self._make_top_btn(
            btn_box, "➕ 새 업무 추가", "#2563EB", self.open_add_task_dialog, short="➕ 새 업무"
        ).pack(side=tk.RIGHT, padx=4)
        self._make_top_btn(
            btn_box, "📝 붙여넣기 스캔", "#0D9488", self.open_paste_scan_dialog, short="📝 붙여넣기"
        ).pack(side=tk.RIGHT, padx=4)
        self._make_top_btn(
            btn_box, "📋 클립보드 스캔", "#16A34A", self.scan_clipboard_now, short="📋 클립보드"
        ).pack(side=tk.RIGHT, padx=4)
        self._make_top_btn(
            btn_box, "📄 파일 불러오기", "#4F46E5", self.import_file_dialog, short="📄 불러오기"
        ).pack(side=tk.RIGHT, padx=4)
        self._make_top_btn(
            btn_box, "📂 폴더 스캔(F5)", "#334155", self.scan_folders_now, short="📂 폴더 스캔"
        ).pack(side=tk.RIGHT, padx=4)

        # 2. 검색 / 필터 / 뷰 전환 바 (구상 2-5 승인 기능)
        filter_bar = tk.Frame(self, bg="#FFFFFF", padx=16, pady=8, highlightbackground="#CBD5E1", highlightthickness=1)
        filter_bar.pack(fill=tk.X)
        self.filter_bar = filter_bar

        tk.Label(filter_bar, text="🔍 검색:", font=("Malgun Gothic", 9, "bold"), bg="#FFFFFF", fg="#334155").pack(
            side=tk.LEFT
        )
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_all())
        # 검색칸이 남는 너비를 채워 필터 줄 가운데에 큰 빈칸이 생기지 않게 함
        self.search_entry = ttk.Entry(filter_bar, textvariable=self.search_var, width=14, font=("Malgun Gothic", 9))
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 12))

        tk.Label(filter_bar, text="상태:", font=("Malgun Gothic", 9, "bold"), bg="#FFFFFF", fg="#334155").pack(
            side=tk.LEFT
        )
        self.status_filter_map = {
            "전체 보기": "ALL",
            "미완료 전체": "INCOMPLETE",
            "🚨 기한경과 (이월)": "OVERDUE",
            "📥 신규 자동스캔": "NEW_SCAN",
            "미완료(대기)": TaskStatus.PENDING,
            "진행중": TaskStatus.IN_PROGRESS,
            "완료됨": TaskStatus.COMPLETED,
        }
        self.status_filter_var = tk.StringVar(value="전체 보기")
        self.status_cb = status_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.status_filter_var,
            values=list(self.status_filter_map.keys()),
            state="readonly",
            width=16,
            font=("Malgun Gothic", 9),
        )
        status_cb.pack(side=tk.LEFT, padx=(6, 12))
        status_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_all())

        self.label_filter_lbl = tk.Label(
            filter_bar, text="라벨(출처):", font=("Malgun Gothic", 9, "bold"), bg="#FFFFFF", fg="#334155"
        )
        self.label_filter_lbl.pack(side=tk.LEFT)
        self.label_filter_map = {"전체 라벨": "ALL"}
        for k, v in self.labels_config.items():
            self.label_filter_map[v.get("name", k)] = k
        self.label_filter_var = tk.StringVar(value="전체 라벨")
        self.label_cb = label_cb = ttk.Combobox(
            filter_bar,
            textvariable=self.label_filter_var,
            values=list(self.label_filter_map.keys()),
            state="readonly",
            width=15,
            font=("Malgun Gothic", 9),
        )
        label_cb.pack(side=tk.LEFT, padx=(6, 12))
        label_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_all())

        self.reset_filter_btn = tk.Button(
            filter_bar,
            text="필터 초기화",
            font=("Malgun Gothic", 8),
            bg="#E2E8F0",
            fg="#334155",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            command=self._reset_filters,
        )
        self.reset_filter_btn.pack(side=tk.LEFT)

        tk.Button(
            filter_bar,
            text="🔎 점검",
            font=("Malgun Gothic", 8),
            bg="#E0E7FF",
            fg="#3730A3",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            command=self.open_import_check_dialog,
        ).pack(side=tk.LEFT, padx=(6, 0))

        tk.Button(
            filter_bar,
            text="📊 실적",
            font=("Malgun Gothic", 8),
            bg="#DCFCE7",
            fg="#166534",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            command=self.open_stats_dialog,
        ).pack(side=tk.LEFT, padx=(6, 0))

        # 우측 뷰 전환 버튼 (월간 캘린더 뷰 vs 일정 목록 Agenda 뷰)
        view_switch_frame = tk.Frame(filter_bar, bg="#FFFFFF")
        view_switch_frame.pack(side=tk.RIGHT, padx=(12, 0))

        self.btn_month_view = tk.Button(
            view_switch_frame,
            text="📅 월간 캘린더 뷰",
            font=("Malgun Gothic", 9, "bold"),
            bg="#2563EB",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=lambda: self.switch_view_mode("MONTH"),
        )
        self.btn_month_view.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_agenda_view = tk.Button(
            view_switch_frame,
            text="📋 일정 목록(Agenda) 뷰",
            font=("Malgun Gothic", 9, "bold"),
            bg="#E2E8F0",
            fg="#334155",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            command=lambda: self.switch_view_mode("AGENDA"),
        )
        self.btn_agenda_view.pack(side=tk.LEFT)

        # 2-1. 빠른 입력 줄: 한 줄로 쓰면 날짜·시각을 읽어 바로 등록
        quick_bar = tk.Frame(self, bg="#F8FAFC", padx=16, pady=6, highlightbackground="#CBD5E1", highlightthickness=1)
        quick_bar.pack(fill=tk.X)
        self.quick_bar = quick_bar
        tk.Label(
            quick_bar, text="⚡ 빠른 입력", font=("Malgun Gothic", 9, "bold"), bg="#F8FAFC", fg="#0F172A"
        ).pack(side=tk.LEFT)
        self.quick_var = tk.StringVar()
        self.quick_entry = ttk.Entry(quick_bar, textvariable=self.quick_var, width=34, font=("Malgun Gothic", 10))
        self.quick_entry.pack(side=tk.LEFT, padx=(8, 6))
        self.quick_entry.bind("<Return>", lambda _e: self.add_quick_entry())
        self.quick_entry.bind("<KP_Enter>", lambda _e: self.add_quick_entry())
        self.quick_add_btn = tk.Button(
            quick_bar,
            text="등록",
            font=("Malgun Gothic", 8, "bold"),
            bg="#2563EB",
            fg="white",
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
            command=self.add_quick_entry,
        )
        self.quick_add_btn.pack(side=tk.LEFT)
        self.quick_hint_lbl = tk.Label(
            quick_bar,
            text=HINT_TEXT,
            font=("Malgun Gothic", 8),
            bg="#F8FAFC",
            fg="#64748B",
        )
        self.quick_hint_lbl.pack(side=tk.RIGHT)
        self.quick_preview_lbl = tk.Label(
            quick_bar, text="", font=("Malgun Gothic", 9), bg="#F8FAFC", fg="#94A3B8", anchor="w"
        )
        self.quick_preview_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 8))
        self.quick_var.trace_add("write", lambda *_: self._on_quick_typed())
        self._update_quick_preview()

        # 3. 메인 분할 영역 (좌측: 캘린더/목록 뷰, 우측: 선택 일자 할일 & 상세 편집 패널)
        self.main_pane = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, bg="#CBD5E1", sashwidth=5, bd=0
        )
        self.main_pane.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        self.left_container = tk.Frame(self.main_pane, bg="#FFFFFF", highlightbackground="#CBD5E1", highlightthickness=1)
        self.right_container = tk.Frame(self.main_pane, bg="#FFFFFF", highlightbackground="#CBD5E1", highlightthickness=1)

        # 창을 늘리거나 줄이면 캘린더와 오른쪽 패널이 함께 커지고 작아지도록 stretch="always"
        self.main_pane.add(self.left_container, minsize=620, width=780, stretch="always")
        self.main_pane.add(self.right_container, minsize=380, width=460, stretch="always")

        self._build_left_views()
        self._build_right_panel()

        # 창 크기나 분할선이 바뀌면 메뉴 글자·버튼 크기를 그 너비에 맞춤
        self._relayout_job: Optional[str] = None
        self._last_layout_widths: Optional[tuple] = None
        for w in (self, self.left_container, self.right_container):
            w.bind("<Configure>", self._schedule_relayout, add="+")

    def _make_top_btn(self, parent: tk.Widget, text: str, bg: str, cmd, short: str = "") -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            font=("Malgun Gothic", 9, "bold"),
            bg=bg,
            fg="white",
            activebackground=bg,
            activeforeground="white",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=cmd,
        )
        self._top_btns.append((btn, text, short or text))
        return btn

    def _build_left_views(self) -> None:
        # A. 월간 캘린더 뷰 프레임
        self.month_view_frame = tk.Frame(self.left_container, bg="#FFFFFF", padx=12, pady=10)
        self.month_view_frame.pack(fill=tk.BOTH, expand=True)

        nav_bar = tk.Frame(self.month_view_frame, bg="#FFFFFF")
        nav_bar.pack(fill=tk.X, pady=(0, 8))
        self.nav_bar = nav_bar

        self.prev_month_btn = tk.Button(
            nav_bar,
            text="◀ 이전달",
            font=("Malgun Gothic", 9, "bold"),
            bg="#F1F5F9",
            fg="#1E293B",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self._prev_month,
        )
        self.prev_month_btn.pack(side=tk.LEFT)

        self.today_btn = tk.Button(
            nav_bar,
            text="오늘로 이동",
            font=("Malgun Gothic", 9, "bold"),
            bg="#DBEAFE",
            fg="#1D4ED8",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self._go_today,
        )
        self.today_btn.pack(side=tk.LEFT, padx=6)

        self.next_month_btn = tk.Button(
            nav_bar,
            text="다음달 ▶",
            font=("Malgun Gothic", 9, "bold"),
            bg="#F1F5F9",
            fg="#1E293B",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self._next_month,
        )
        self.next_month_btn.pack(side=tk.LEFT)

        self.month_title_lbl = tk.Label(
            nav_bar,
            text="",
            font=("Malgun Gothic", 14, "bold"),
            bg="#FFFFFF",
            fg="#0F172A",
        )
        self.month_title_lbl.pack(side=tk.LEFT, padx=20)

        # 라벨 범례 표시 (탐색 줄에 다 들어가지 않으면 _relayout에서 그 아래 한 줄로 내림)
        legend_frame = tk.Frame(self.month_view_frame, bg="#FFFFFF")
        legend_frame.pack(in_=nav_bar, side=tk.RIGHT)
        self.legend_frame = legend_frame
        for k, v in self.labels_config.items():
            chip = tk.Label(
                legend_frame,
                text=f"● {v.get('name', k)}",
                font=("Malgun Gothic", 8, "bold"),
                bg="#FFFFFF",
                fg=v.get("color", "#334155"),
            )
            chip.pack(side=tk.LEFT, padx=4)

        # 요일 헤더 + 6주 달력 그리드
        self.cal_grid_frame = tk.Frame(self.month_view_frame, bg="#E2E8F0")
        self.cal_grid_frame.pack(fill=tk.BOTH, expand=True)

        weekdays = [("일", "#DC2626"), ("월", "#1E293B"), ("화", "#1E293B"), ("수", "#1E293B"), ("목", "#1E293B"), ("금", "#1E293B"), ("토", "#2563EB")]
        for col, (w_name, w_color) in enumerate(weekdays):
            self.cal_grid_frame.columnconfigure(col, weight=1, uniform="cal_col")
            hdr = tk.Label(
                self.cal_grid_frame,
                text=w_name,
                font=("Malgun Gothic", 9, "bold"),
                bg="#F8FAFC",
                fg=w_color,
                pady=5,
            )
            hdr.grid(row=0, column=col, sticky="nsew", padx=1, pady=1)

        self.day_cells: List[tk.Frame] = []
        for r in range(1, 7):
            self.cal_grid_frame.rowconfigure(r, weight=1, uniform="cal_row")
            for c in range(7):
                cell = tk.Frame(self.cal_grid_frame, bg="#FFFFFF", padx=4, pady=self._CELL_PADY)
                cell.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                self.day_cells.append(cell)
                # 칸은 계속 다시 쓰므로 클릭 연결은 여기서 한 번만 한다. 달력을 그릴 때마다 bind하면
                # 예전 연결이 Tcl 쪽에 쌓여 오래 켜 둘수록 메모리가 는다. 날짜는 _cell_dates에서 찾음
                cell.bind("<Button-1>", lambda _e, w=cell: self._cell_click(w, self._select_date))
                cell.bind("<Double-1>", lambda _e, w=cell: self._cell_click(w, self.open_add_task_dialog))
                cell.bind("<Button-3>", lambda e, w=cell: self._cell_click(w, lambda ds: self._on_date_right_click(e, ds)))
        # 창 크기가 바뀌면 칸에 들어가는 업무 개수가 달라지므로 달력만 다시 그림
        self.cal_grid_frame.bind("<Configure>", self._on_cal_resize, add="+")

        # B. 일정 목록(Agenda) 뷰 프레임
        self.agenda_view_frame = tk.Frame(self.left_container, bg="#FFFFFF", padx=12, pady=10)
        agenda_top = tk.Frame(self.agenda_view_frame, bg="#FFFFFF")
        agenda_top.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            agenda_top,
            text="📋 전체 업무 일정 목록 (기한경과 이월 → 오늘 마감 → 예정 업무 순)",
            font=("Malgun Gothic", 12, "bold"),
            bg="#FFFFFF",
            fg="#0F172A",
        ).pack(side=tk.LEFT)

        agenda_cols = ("dday", "due", "label", "title", "source", "recur", "status")
        self.agenda_tree = ttk.Treeview(
            self.agenda_view_frame, columns=agenda_cols, show="headings"
        )
        self.agenda_tree.heading("dday", text="D-Day")
        self.agenda_tree.heading("due", text="마감기한")
        self.agenda_tree.heading("label", text="분류 라벨")
        self.agenda_tree.heading("title", text="업무 제목")
        self.agenda_tree.heading("source", text="출처/문서번호")
        self.agenda_tree.heading("recur", text="반복")
        self.agenda_tree.heading("status", text="상태")

        # 창을 줄이면 제목·출처 열만 좁아지도록 나머지 열은 너비 고정
        self.agenda_tree.column("dday", width=95, anchor="center", stretch=False)
        self.agenda_tree.column("due", width=110, anchor="center", stretch=False)
        self.agenda_tree.column("label", width=95, anchor="center", stretch=False)
        self.agenda_tree.column("title", width=230, minwidth=80, anchor="w")
        self.agenda_tree.column("source", width=120, minwidth=60, anchor="w")
        self.agenda_tree.column("recur", width=70, anchor="center", stretch=False)
        self.agenda_tree.column("status", width=75, anchor="center", stretch=False)

        self.agenda_tree.tag_configure("overdue", background="#FEE2E2", foreground="#991B1B")
        self.agenda_tree.tag_configure("today", background="#DBEAFE", foreground="#1E40AF")
        self.agenda_tree.tag_configure("completed", background="#F1F5F9", foreground="#94A3B8")
        self.agenda_tree.tag_configure("normal", background="#FFFFFF", foreground="#1E293B")

        ag_scroll = ttk.Scrollbar(self.agenda_view_frame, orient=tk.VERTICAL, command=self.agenda_tree.yview)
        self.agenda_tree.configure(yscrollcommand=ag_scroll.set)
        self.agenda_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ag_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.agenda_tree.bind("<<TreeviewSelect>>", self._on_agenda_select)
        self.agenda_tree.bind("<Double-1>", lambda _e: self.open_edit_selected_task())
        self.agenda_tree.bind(
            "<Button-3>", lambda e: self._on_tree_right_click(e, self.agenda_tree, self._agenda_task_map)
        )

    def _build_right_panel(self) -> None:
        pad = tk.Frame(self.right_container, bg="#FFFFFF", padx=14, pady=12)
        pad.pack(fill=tk.BOTH, expand=True)
        self.right_pad = pad

        # 선택 날짜 헤더
        header_row = tk.Frame(pad, bg="#FFFFFF")
        header_row.pack(fill=tk.X, pady=(0, 8))

        # 날짜 제목과 보충 설명을 두 줄로 나눠 좁은 패널에서도 잘리지 않게 함
        date_box = tk.Frame(header_row, bg="#FFFFFF")
        date_box.pack(side=tk.LEFT)
        self.right_date_lbl = tk.Label(
            date_box,
            text="",
            font=("Malgun Gothic", 12, "bold"),
            bg="#FFFFFF",
            fg="#0F172A",
        )
        self.right_date_lbl.pack(anchor="w")
        self.right_date_sub_lbl = tk.Label(
            date_box,
            text="",
            font=("Malgun Gothic", 8),
            bg="#FFFFFF",
            fg="#64748B",
        )

        tk.Button(
            header_row,
            text="+ 이 날짜에 추가",
            font=("Malgun Gothic", 8, "bold"),
            bg="#DBEAFE",
            fg="#1D4ED8",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            command=lambda: self.open_add_task_dialog(self.selected_date_str),
        ).pack(side=tk.RIGHT, anchor="n")

        # 해당 날짜(및 오늘인 경우 기한경과 자동 이월 포함) 할 일 Treeview
        list_container = tk.Frame(pad, bg="#FFFFFF")
        list_container.pack(fill=tk.BOTH, expand=True)

        cols = ("dday", "label", "title", "status")
        # 높이는 최소치만 정하고 남는 공간은 아래 상세 칸과 나눠 가짐 (낮은 창에서 상세 칸이 잘리지 않게)
        self.day_tree = ttk.Treeview(list_container, columns=cols, show="headings", height=6)
        self.day_tree.heading("dday", text="기한/시간")
        self.day_tree.heading("label", text="분류")
        self.day_tree.heading("title", text="업무 제목")
        self.day_tree.heading("status", text="상태")

        # 제목 열만 패널 너비를 따라 늘고 줄며, 나머지 열 너비는 _relayout에서 목록 너비에 맞춰 정함
        self.day_tree.column("dday", width=110, anchor="center", stretch=False)
        self.day_tree.column("label", width=88, anchor="center", stretch=False)
        self.day_tree.column("title", width=180, minwidth=60, anchor="w")
        self.day_tree.column("status", width=82, anchor="center", stretch=False)

        self.day_tree.tag_configure("overdue", background="#FEE2E2", foreground="#991B1B")
        self.day_tree.tag_configure("completed", background="#F1F5F9", foreground="#94A3B8")
        self.day_tree.tag_configure("new_scan", background="#DCFCE7", foreground="#166534")
        self.day_tree.tag_configure("normal", background="#FFFFFF", foreground="#1E293B")

        d_scroll = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.day_tree.yview)
        self.day_tree.configure(yscrollcommand=d_scroll.set)
        self.day_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        d_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.day_tree.bind("<<TreeviewSelect>>", self._on_day_tree_select)
        self.day_tree.bind("<Double-1>", lambda _e: self.open_edit_selected_task())
        self.day_tree.bind("<Button-3>", lambda e: self._on_tree_right_click(e, self.day_tree, self._right_task_map))

        # 액션 버튼 줄
        act_row = tk.Frame(pad, bg="#FFFFFF", pady=8)
        act_row.pack(fill=tk.X)
        self.act_row = act_row

        self.complete_btn = tk.Button(
            act_row,
            text="✅ 완료 / 미완료 전환",
            font=("Malgun Gothic", 9, "bold"),
            bg="#16A34A",
            fg="white",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            command=self.toggle_selected_task_complete,
        )
        # 완료·편집 버튼이 줄 너비를 나눠 채워 삭제 버튼 앞에 빈칸이 생기지 않게 함
        self.complete_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.edit_btn = tk.Button(
            act_row,
            text="✏️ 상세 편집 / 기한 변경",
            font=("Malgun Gothic", 9, "bold"),
            bg="#2563EB",
            fg="white",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            command=self.open_edit_selected_task,
        )
        self.edit_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.delete_btn = tk.Button(
            act_row,
            text="🗑️ 삭제",
            font=("Malgun Gothic", 9),
            bg="#FEE2E2",
            fg="#DC2626",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            command=self.delete_selected_task,
        )
        self.delete_btn.pack(side=tk.RIGHT)

        # 하단: 선택된 업무의 원본 문서/메시지 및 메타데이터 상세 보기 영역
        detail_box = tk.LabelFrame(
            pad,
            text=" 📄 선택 업무 상세 내용 & 배부 문서/메시지 원문 ",
            font=("Malgun Gothic", 9, "bold"),
            bg="#F8FAFC",
            fg="#334155",
            padx=10,
            pady=8,
        )
        detail_box.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.detail_box = detail_box

        self.detail_meta_lbl = tk.Label(
            detail_box,
            text="목록에서 업무를 선택하면 상세 내용과 원문이 표시됩니다.",
            font=("Malgun Gothic", 9),
            bg="#F8FAFC",
            fg="#475569",
            justify=tk.LEFT,
            anchor="w",
            wraplength=390,
        )
        self.detail_meta_lbl.pack(fill=tk.X, pady=(0, 6))

        self.detail_text = tk.Text(
            detail_box,
            height=8,
            font=("Malgun Gothic", 9),
            bg="#FFFFFF",
            fg="#1E293B",
            wrap=tk.WORD,
            relief=tk.SOLID,
            bd=1,
            padx=6,
            pady=6,
            state=tk.DISABLED,
        )
        self.detail_text.pack(fill=tk.BOTH, expand=True)

    # 창 너비에 따라 단계적으로 줄이는 메뉴 모양. 0단계가 기본, 뒤로 갈수록 작게.
    # (제목 글꼴, 버튼 글꼴, 버튼 좌우 여백, 짧은 이름 사용)
    _TOP_LEVELS = [(14, 9, 10, False), (14, 9, 6, False), (13, 9, 7, True), (12, 8, 5, True)]
    # (글꼴, 상태 콤보 너비, 라벨 콤보 너비, 뷰 버튼 이름, 라벨 이름, 초기화 이름, 뷰 버튼 좌우 여백)
    _FILTER_LEVELS = [
        (9, 16, 15, ("📅 월간 캘린더 뷰", "📋 일정 목록(Agenda) 뷰"), "라벨(출처):", "필터 초기화", 12),
        (9, 16, 15, ("📅 월간 캘린더", "📋 일정 목록"), "라벨(출처):", "초기화", 8),
        (8, 13, 12, ("📅 월간", "📋 목록"), "라벨:", "초기화", 6),
    ]
    # (범례를 탐색 줄 안에 둘지, 이전달/오늘/다음달 버튼 이름, 월 제목 글꼴)
    _NAV_LEVELS = [
        (True, ("◀ 이전달", "오늘로 이동", "다음달 ▶"), 14),
        (False, ("◀ 이전달", "오늘로 이동", "다음달 ▶"), 14),
        (False, ("◀", "오늘", "▶"), 13),
    ]
    # (완료·편집·삭제 버튼 이름, 글꼴, 좌우 여백, 날짜 제목 글꼴)
    _ACTION_LEVELS = [
        (("✅ 완료 / 미완료 전환", "✏️ 상세 편집 / 기한 변경", "🗑️ 삭제"), 9, 10, 12),
        (("✅ 완료 전환", "✏️ 편집 / 기한 변경", "🗑️ 삭제"), 9, 8, 12),
        (("✅ 완료", "✏️ 편집", "🗑️ 삭제"), 8, 6, 11),
    ]
    # (목록 너비 하한, 기한·분류·상태 열 너비) — 남는 너비는 제목 열이 가짐
    _DAY_COL_LEVELS = [(420, (140, 88, 82)), (360, (115, 85, 80)), (0, (100, 78, 76))]

    def _schedule_relayout(self, _event=None) -> None:
        if self._relayout_job is None:
            self._relayout_job = self.after(60, self._relayout)

    def _relayout(self) -> None:
        self._relayout_job = None
        widths = (self.winfo_width(), self.left_container.winfo_width(), self.right_container.winfo_width())
        if widths == self._last_layout_widths or widths[0] <= 1:
            return
        self._last_layout_widths = widths

        self._fit_levels(
            self._TOP_LEVELS,
            self._apply_top_level,
            self.top_bar,
            lambda: self.title_box.winfo_reqwidth() + self.top_btn_box.winfo_reqwidth() + 16,
        )
        self._fit_levels(self._FILTER_LEVELS, self._apply_filter_level, self.filter_bar, self.filter_bar.winfo_reqwidth)
        self._fit_levels(self._NAV_LEVELS, self._apply_nav_level, self.month_view_frame, self.nav_bar.winfo_reqwidth)
        self._fit_levels(self._ACTION_LEVELS, self._apply_action_level, self.right_pad, self.act_row.winfo_reqwidth)
        self.detail_meta_lbl.config(wraplength=max(200, self.detail_box.winfo_width() - 28))
        tree_w = self.day_tree.winfo_width()
        col_widths = next(widths for min_w, widths in self._DAY_COL_LEVELS if tree_w >= min_w)
        for col, width in zip(("dday", "label", "status"), col_widths):
            self.day_tree.column(col, width=width)

    def _fit_levels(self, levels: list, apply, container: tk.Widget, needed) -> None:
        """가장 큰 단계부터 적용해 보고 container 너비 안에 들어가는 첫 단계를 남김."""
        avail = container.winfo_width() - 2 * int(container.cget("padx"))
        for lv in levels:
            apply(lv)
            self.update_idletasks()
            if needed() <= avail:
                return

    def _apply_top_level(self, lv: tuple) -> None:
        title_font, btn_font, padx, use_short = lv
        self.title_lbl.config(font=("Malgun Gothic", title_font, "bold"))
        for btn, full, short in self._top_btns:
            btn.config(text=short if use_short else full, font=("Malgun Gothic", btn_font, "bold"), padx=padx)
        for chk in self._top_chks:
            chk.config(font=("Malgun Gothic", btn_font))

    def _apply_filter_level(self, lv: tuple) -> None:
        font, status_w, label_w, view_texts, label_text, reset_text, view_padx = lv
        for w in self.filter_bar.winfo_children():
            if isinstance(w, tk.Label):
                w.config(font=("Malgun Gothic", font, "bold"))
        self.label_filter_lbl.config(text=label_text)
        self.search_entry.config(font=("Malgun Gothic", font))
        self.status_cb.config(width=status_w, font=("Malgun Gothic", font))
        self.label_cb.config(width=label_w, font=("Malgun Gothic", font))
        self.reset_filter_btn.config(text=reset_text)
        for btn, text in zip((self.btn_month_view, self.btn_agenda_view), view_texts):
            btn.config(text=text, font=("Malgun Gothic", font, "bold"), padx=view_padx)

    def _apply_nav_level(self, lv: tuple) -> None:
        legend_inline, texts, title_font = lv
        for btn, text in zip((self.prev_month_btn, self.today_btn, self.next_month_btn), texts):
            btn.config(text=text)
        self.month_title_lbl.config(font=("Malgun Gothic", title_font, "bold"))
        if legend_inline:
            self.legend_frame.pack(in_=self.nav_bar, side=tk.RIGHT, anchor="center", pady=0)
        else:
            self.legend_frame.pack(
                in_=self.month_view_frame, side=tk.TOP, anchor="e", pady=(0, 6), before=self.cal_grid_frame
            )

    def _apply_action_level(self, lv: tuple) -> None:
        texts, font, padx, date_font = lv
        for btn, text in zip((self.complete_btn, self.edit_btn, self.delete_btn), texts):
            weight = "normal" if btn is self.delete_btn else "bold"
            btn.config(text=text, font=("Malgun Gothic", font, weight), padx=padx)
        self.right_date_lbl.config(font=("Malgun Gothic", date_font, "bold"))

    def switch_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        if mode == "MONTH":
            self.agenda_view_frame.pack_forget()
            self.month_view_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_month_view.config(bg="#2563EB", fg="white")
            self.btn_agenda_view.config(bg="#E2E8F0", fg="#334155")
        else:
            self.month_view_frame.pack_forget()
            self.agenda_view_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_agenda_view.config(bg="#2563EB", fg="white")
            self.btn_month_view.config(bg="#E2E8F0", fg="#334155")
        # 숨겨져 있던 동안 바뀐 창 너비에 맞춰 탐색 줄을 다시 맞춤
        self._last_layout_widths = None
        self._schedule_relayout()
        self.refresh_all()

    def _reset_filters(self) -> None:
        self.search_var.set("")
        self.status_filter_var.set("전체 보기")
        self.label_filter_var.set("전체 라벨")
        self.refresh_all()

    def _get_filter_args(self) -> dict:
        kw = self.search_var.get().strip()
        st = self.status_filter_map.get(self.status_filter_var.get(), "ALL")
        lb = self.label_filter_map.get(self.label_filter_var.get(), "ALL")
        return {"keyword": kw, "status_filter": st, "label_filter": lb}

    def refresh_all(self) -> None:
        if self._drag and self._drag.get("active"):
            # 끄는 중에 다시 그리면 끌던 일정 칩이 사라지므로, 놓은 뒤에 한 번 갱신
            self._refresh_pending = True
            return
        self._refresh_pending = False
        if self.view_mode == "MONTH":
            self._render_month_calendar()
        else:
            self._render_agenda_list()
        self._render_right_panel()
        self._update_review_button()

    def _update_review_button(self) -> None:
        if not self.ezq_collector:
            return
        count = self.db.count_review_tasks()
        shown = bool(self.review_btn.winfo_manager())
        if count:
            self.review_btn.config(text=f"❔ 확인 필요 {count}건")
            if not shown:
                self.review_btn.pack(side=tk.LEFT, padx=(6, 0), after=self._review_btn_anchor)
        elif shown:
            self.review_btn.pack_forget()
        if bool(count) != shown:
            self._last_layout_widths = None
            self._schedule_relayout()

    def _cell_click(self, cell: tk.Frame, action) -> None:
        """달력 칸에 지금 표시된 날짜로 동작을 실행 (칸의 날짜는 달을 옮길 때마다 바뀜)."""
        d_str = self._cell_dates.get(str(cell))
        if d_str:
            action(d_str)

    def _render_month_calendar(self) -> None:
        self.month_title_lbl.config(text=f"{self.current_year}년 {self.current_month:02d}월")
        cal = calendar.Calendar(firstweekday=6)  # 일요일 시작 달력
        month_days = cal.monthdatescalendar(self.current_year, self.current_month)
        while len(month_days) < 6:
            last_week = month_days[-1]
            next_week = [
                date.fromordinal(last_week[-1].toordinal() + i) for i in range(1, 8)
            ]
            month_days.append(next_week)

        flat_dates = [d for week in month_days[:6] for d in week]
        first_day, last_day = flat_dates[0], flat_dates[-1]

        f_args = self._get_filter_args()
        # 보이는 6주에 걸치는 업무 + 기한경과·반복 미완료 업무만 읽음 (몇 년 치 업무를 매번 다 읽지 않게)
        all_tasks = self.db.query_tasks(**f_args, visible_range=(first_day.isoformat(), last_day.isoformat()))

        tasks_by_date: Dict[str, List[TaskItem]] = {}
        overdue_tasks: List[TaskItem] = []
        today_str = date.today().strftime("%Y-%m-%d")
        # 기간 업무는 보이는 모든 날 칸에 (달이 바뀌는 기간도 겹치는 칸에 모두)
        for t in all_tasks:
            if not t.due_date:
                continue
            for d in span_dates(t, first_day, last_day):
                tasks_by_date.setdefault(d.strftime("%Y-%m-%d"), []).append(t)
            if t.is_overdue():
                overdue_tasks.append(t)

        self._cell_dates = {}
        # 반복 업무의 앞으로의 날짜 (저장하지 않는 미리보기, 진짜 업무 다음에 흐리게).
        # 끝이 달력 뒤라도 시작이 달력 안이면 보이게, 가장 긴 기간만큼 더 넓게 구함
        longest = max((len(span_dates(t, date.min, date.max)) - 1 for t in all_tasks if t.is_period), default=0)
        preview_by_due = preview_tasks(all_tasks, first_day, last_day + timedelta(days=longest), date.today())
        previews: Dict[str, List[TaskItem]] = {}
        for items in preview_by_due.values():
            for p in items:
                for d in span_dates(p, first_day, last_day):
                    previews.setdefault(d.strftime("%Y-%m-%d"), []).append(p)

        m = self._chip_metrics_now()
        overdue_count = len(overdue_tasks)
        for idx, cell in enumerate(self.day_cells):
            # 칸 안의 부품(날짜 숫자, 업무 칩, '+ 외 N건')은 지우지 않고 다시 씀.
            # 매번 지우고 새로 만들면 1년치 데이터에서 새로고침 한 번에 0.6초가 걸림
            parts = self._cell_parts_for(idx, cell)

            cell_date = flat_dates[idx]
            d_str = cell_date.strftime("%Y-%m-%d")
            is_current_month = cell_date.month == self.current_month
            is_today = d_str == today_str
            is_selected = d_str == self.selected_date_str

            bg_color = "#FFFFFF" if is_current_month else "#F8FAFC"
            if is_today:
                bg_color = "#EFF6FF"
            if is_selected:
                bg_color = "#DBEAFE"

            self._set_opts(
                cell,
                bg=bg_color,
                highlightbackground="#2563EB" if is_selected else "#E2E8F0",
                highlightthickness=2 if is_selected else 1,
            )
            self._cell_dates[str(cell)] = d_str

            # 상단 날짜 숫자 및 배지 줄
            self._set_opts(parts["top"], bg=bg_color)

            col_idx = idx % 7
            if not is_current_month:
                num_fg = "#94A3B8"
            elif col_idx == 0:
                num_fg = "#DC2626"
            elif col_idx == 6:
                num_fg = "#2563EB"
            else:
                num_fg = "#1E293B"

            self._set_opts(
                parts["day"],
                text=f"{cell_date.day} (오늘)" if is_today else f"{cell_date.day}",
                font=("Malgun Gothic", 9, "bold" if (is_today or is_selected) else "normal"),
                bg=bg_color,
                fg=num_fg,
            )

            # 오늘 칸에는 기한 경과(자동 이월) 건수 배지 표시
            if is_today and overdue_count:
                if parts["ov"] is None:
                    parts["ov"] = tk.Label(
                        parts["top"], font=("Malgun Gothic", 7, "bold"), bg="#DC2626", fg="white", padx=4, pady=0, bd=0
                    )
                    self._bind_date_clicks(parts["ov"], cell)
                self._set_opts(parts["ov"], text=f"🚨이월 {overdue_count}")
                if not parts["ov_on"]:
                    parts["ov"].pack(side=tk.RIGHT)
                    parts["ov_on"] = True
            elif parts["ov_on"]:
                parts["ov"].pack_forget()
                parts["ov_on"] = False

            day_items = list(tasks_by_date.get(d_str, [])) + previews.get(d_str, [])
            # 칸 높이에 들어가는 만큼 보이고 나머지는 '+ 외 N건' (글자 크기는 그대로)
            # 테두리는 선택하면 1px → 2px. 선택 여부와 상관없이 2px로 계산해야, 칸에 딱 맞게 들어가 있던
            # 맨 아래 업무가 그 칸을 누르는 순간 '+ 외 N건'으로 숨지 않음
            hl = 2
            avail_h = cell.winfo_height() - 2 * (hl + self._CELL_PADY) - m["header"] if cell.winfo_height() > 1 else 0
            max_chips, hidden = chips_that_fit(len(day_items), avail_h, m["slot"], m["more"])
            text_w = cell.winfo_width() - 2 * (hl + 4) - 2 * 3 if cell.winfo_width() > 1 else 0
            shown = day_items[:max_chips]

            # '+ 외 N건'은 항상 업무 칩 아래에 와야 하므로, 칩을 더 꺼내기 전에 잠시 뺌
            if parts["more_on"] and (len(shown) > parts["shown"] or not hidden):
                parts["more"].pack_forget()
                parts["more_on"] = False

            chips = parts["chips"]
            for i, t in enumerate(shown):
                if i == len(chips):
                    chips.append(self._new_chip(cell))
                chip = chips[i]
                # 기간 업무의 이어지는 칸 (시작 칸과 각 주 첫 칸은 제목 그대로)
                cont = t.is_period and d_str != t.start_date and col_idx != 0
                text, chip_bg, chip_fg = self._chip_look(t, cont, text_w, m)
                self._set_opts(chip, text=text, bg=chip_bg, fg=chip_fg)
                self._chip_data[str(chip)] = (t, d_str)
                if i >= parts["shown"]:
                    chip.pack(fill=tk.X, pady=(0, self._CHIP_GAP))
            for i in range(len(shown), parts["shown"]):
                chips[i].pack_forget()
                self._chip_data.pop(str(chips[i]), None)
            parts["shown"] = len(shown)

            if hidden:
                more_text = f"+ 외 {hidden}건 더보기"
                if 0 < text_w < self._text_width(m["more_font"], more_text):
                    more_text = f"+{hidden}건"
                if parts["more"] is None:
                    parts["more"] = tk.Label(
                        cell, font=m["more_font"], fg="#475569", anchor="e", pady=0, bd=0, highlightthickness=0
                    )
                    self._bind_date_clicks(parts["more"], cell)
                self._set_opts(parts["more"], text=more_text, bg=bg_color)
                if not parts["more_on"]:
                    parts["more"].pack(fill=tk.X)
                    parts["more_on"] = True

    def _set_opts(self, widget: tk.Widget, **opts) -> None:
        """바뀐 설정만 Tk에 보냄 (다시 그릴 때 대부분의 칸은 그대로라서 Tk 호출이 크게 줆)."""
        key = str(widget)
        old = self._widget_opts.get(key)
        if old is None:
            widget.config(**opts)
            self._widget_opts[key] = opts
            return
        changed = {k: v for k, v in opts.items() if old.get(k) != v}
        if changed:
            widget.config(**changed)
            old.update(changed)

    def _bind_date_clicks(self, widget: tk.Widget, cell: tk.Frame) -> None:
        """칸 안의 부품을 눌러도 그 칸에 지금 표시된 날짜가 선택되게 (한 번만 연결)."""
        widget.bind("<Button-1>", lambda _e, w=cell: self._cell_click(w, self._select_date))
        widget.bind("<Button-3>", lambda e, w=cell: self._cell_click(w, lambda ds: self._on_date_right_click(e, ds)))

    def _cell_parts_for(self, idx: int, cell: tk.Frame) -> dict:
        while len(self._cell_parts) <= idx:
            self._cell_parts.append(None)
        parts = self._cell_parts[idx]
        if parts is None:
            top = tk.Frame(cell)
            top.pack(fill=tk.X)
            day = tk.Label(top, pady=0, bd=0)
            day.pack(side=tk.LEFT)
            self._bind_date_clicks(top, cell)
            self._bind_date_clicks(day, cell)
            parts = {"top": top, "day": day, "ov": None, "ov_on": False, "chips": [], "shown": 0,
                     "more": None, "more_on": False}
            self._cell_parts[idx] = parts
        return parts

    def _new_chip(self, cell: tk.Frame) -> tk.Label:
        """업무 칩 하나. 어떤 업무를 보여 주는지는 _chip_data에 두고, 누를 때 찾아 씀."""
        # 테두리·위아래 여백 없이 글자 높이만 차지하게 해 한 칸에 더 많이 들어가게 함
        chip = tk.Label(
            cell, font=self._chip_metrics_now()["chip_font"], anchor="w", padx=3, pady=0, bd=0,
            highlightthickness=0, cursor="hand2",
        )
        # 누르고 끌면 다른 날짜로 옮기기, 그냥 떼면 선택 (선택 화면 갱신은 뗄 때 해야 끌기가 끊기지 않음)
        # 두 번 누르기는 _chip_press에서 판단 (<Double-1>에 기대지 않음)
        chip.bind("<ButtonPress-1>", lambda e, w=chip: self._chip_event(w, e, menu=False))
        chip.bind("<B1-Motion>", self._chip_motion)
        chip.bind("<ButtonRelease-1>", self._chip_release)
        chip.bind("<Button-3>", lambda e, w=chip: self._chip_event(w, e, menu=True))
        return chip

    def _chip_event(self, chip: tk.Label, event, menu: bool) -> None:
        data = self._chip_data.get(str(chip))
        if not data:
            return
        task, d_str = data
        if task.is_preview:
            # 반복 업무의 다음 날짜 미리보기: 끌어 옮기기·업무 메뉴 없음. 두 번 누르면 반복 업무를 엶
            if menu:
                self._on_date_right_click(event, d_str)
            else:
                self._preview_chip_click(event, d_str, task.id)
        elif menu:
            self._on_task_right_click(event, task, d_str)
        else:
            self._chip_press(event, task, d_str)

    def _chip_look(self, t: TaskItem, cont: bool, text_w: int, m: dict):
        """업무 칩의 (글자, 바탕색, 글자색). cont: 기간 업무의 이어지는 칸."""
        lbl_cfg = self.labels_config.get(t.label, {})
        if t.is_preview:
            # 미리보기는 라벨 바탕색에 흐린 글자
            text = f"▸ {t.title}" if cont else f"↻ {t.title}"
            short = text if len(text) <= 15 else text[:14] + "…"
            chip_bg, chip_fg = lbl_cfg.get("bg_color", "#F1F5F9"), "#94A3B8"
        else:
            overdue = t.is_overdue()
            chip_bg = "#E2E8F0" if t.is_completed else lbl_cfg.get("bg_color", "#E0F2FE")
            chip_fg = "#64748B" if t.is_completed else lbl_cfg.get("color", "#0369A1")
            if overdue:
                chip_bg, chip_fg = "#FEE2E2", "#B91C1C"
            prefix = "✓ " if t.is_completed else ("🚨 " if overdue else "• ")
            if cont:
                prefix = "▸ "
            text = f"{prefix}{t.title}"
            short = prefix + (t.title if len(t.title) <= 14 else t.title[:13] + "…")
        if text_w <= 0:
            return short, chip_bg, chip_fg
        key = (text, text_w)
        fitted = self._fit_cache.get(key)
        if fitted is None:
            if len(self._fit_cache) > 4000:
                self._fit_cache.clear()
            fitted = self._fit_cache[key] = fit_title(text, text_w, m["chip_font"].measure)
        return fitted, chip_bg, chip_fg

    def _text_width(self, font, text: str) -> int:
        key = (str(font), text)
        width = self._width_cache.get(key)
        if width is None:
            if len(self._width_cache) > 4000:
                self._width_cache.clear()
            width = self._width_cache[key] = font.measure(text)
        return width

    def _preview_chip_click(self, event, date_str: str, task_id: Optional[int]) -> None:
        if self._chip_clicks.click(("preview", task_id), event.time):
            self._open_task_by_id(task_id)
        else:
            self._select_date(date_str)

    def _open_task_by_id(self, task_id: Optional[int]) -> None:
        task = self.db.get_task(task_id) if task_id is not None else None
        if task is not None:
            self.open_edit_task_dialog(task)

    _CHIP_GAP = 1  # 업무 칸 사이 간격(px)
    _CELL_PADY = 2  # 날짜 칸 위아래 여백(px)

    def _chip_metrics_now(self) -> dict:
        """업무 1개 높이, '+ 외 N건' 줄 높이, 날짜 숫자 줄 높이 (화면 배율에 따라 달라서 실제로 재 둠)."""
        if self._chip_metrics is None:
            chip_font = tkfont.Font(root=self, family="Malgun Gothic", size=8)
            more_font = tkfont.Font(root=self, family="Malgun Gothic", size=7, weight="bold")
            header_font = tkfont.Font(root=self, family="Malgun Gothic", size=9, weight="bold")
            probe = tk.Label(self, text="31", font=header_font, pady=0, bd=0)  # 날짜 숫자 줄과 같은 설정
            header = probe.winfo_reqheight()
            probe.destroy()
            self._chip_metrics = {
                "chip_font": chip_font,
                "more_font": more_font,
                "slot": chip_font.metrics("linespace") + self._CHIP_GAP,
                "more": more_font.metrics("linespace"),
                "header": header,
            }
        return self._chip_metrics

    def _on_cal_resize(self, event) -> None:
        size = (event.width, event.height)
        if size == self._cal_size or event.width <= 1:
            return
        self._cal_size = size
        if self._cal_fit_job:
            self.after_cancel(self._cal_fit_job)
        self._cal_fit_job = self.after(120, self._refit_calendar)

    def _refit_calendar(self) -> None:
        self._cal_fit_job = None
        if self.view_mode != "MONTH":
            return
        if self._drag and self._drag.get("active"):
            self._refresh_pending = True  # 끄는 중에는 다시 그리지 않고, 놓은 뒤 갱신
            return
        self._render_month_calendar()

    def _render_agenda_list(self) -> None:
        for item in self.agenda_tree.get_children():
            self.agenda_tree.delete(item)
        self._agenda_task_map.clear()

        f_args = self._get_filter_args()
        # '전체 보기'에서 검색어가 없으면 마감이 오래 지난 완료 업무는 뺌 (검색하거나 '완료'를 고르면 모두 나옴)
        if f_args["status_filter"] == "ALL" and not f_args["keyword"]:
            f_args["hide_completed_before"] = (date.today() - timedelta(days=AGENDA_COMPLETED_DAYS)).isoformat()
        tasks = self.db.query_tasks(**f_args)
        today_str = date.today().strftime("%Y-%m-%d")

        for t in tasks:
            iid = f"ag_{t.id}"
            self._agenda_task_map[iid] = t
            lbl_name = self.labels_config.get(t.label, {}).get("name", t.label)
            src_str = t.source_ref or TaskSource.ALL_LABELS.get(t.source, t.source)
            recur_str = RecurrenceType.ALL_LABELS.get(t.recurrence, "없음")
            st_str = TaskStatus.ALL_LABELS.get(t.status, t.status)

            if t.is_completed:
                tag = "completed"
            elif t.is_overdue():
                tag = "overdue"
            elif t.due_date == today_str:
                tag = "today"
            else:
                tag = "normal"

            self.agenda_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    t.d_day_badge(),
                    f"{t.period_label() if t.is_period else t.due_date} {t.due_time}",
                    lbl_name,
                    t.title,
                    src_str,
                    recur_str,
                    st_str,
                ),
                tags=(tag,),
            )

        # 다시 그린 뒤에도 고른 업무와 키보드 커서를 유지 (Space로 완료 전환 후 이어서 조작).
        # 일정 목록을 쓰던 중일 때만 — 오른쪽 목록에서 고른 이월 업무로 날짜가 바뀌지 않게
        if (
            self._active_tree is self.agenda_tree
            and self.selected_task
            and f"ag_{self.selected_task.id}" in self._agenda_task_map
        ):
            iid = f"ag_{self.selected_task.id}"
            self.agenda_tree.selection_set(iid)
            self.agenda_tree.focus(iid)
            self.agenda_tree.see(iid)

    def _render_right_panel(self) -> None:
        today_str = date.today().strftime("%Y-%m-%d")
        is_today = self.selected_date_str == today_str
        self.right_date_lbl.config(text=f"📌 {self.selected_date_str} 업무 목록")
        if is_today:
            self.right_date_sub_lbl.config(text="오늘 마감 + 기한경과 자동이월 포함")
            self.right_date_sub_lbl.pack(anchor="w")
        else:
            self.right_date_sub_lbl.pack_forget()

        for item in self.day_tree.get_children():
            self.day_tree.delete(item)
        self._right_task_map.clear()

        f_args = self._get_filter_args()
        tasks = self.db.query_tasks(
            target_date=self.selected_date_str,
            include_rollover_on_today=True,
            include_period_span=True,
            **f_args,
        )

        for t in tasks:
            iid = f"rt_{t.id}"
            self._right_task_map[iid] = t
            lbl_name = self.labels_config.get(t.label, {}).get("name", t.label)
            st_str = TaskStatus.ALL_LABELS.get(t.status, t.status)
            time_or_dday = f"🚨이월({t.due_date[5:]})" if t.is_overdue() else f"{t.due_time} ({t.d_day_badge()})"
            if t.is_period and not t.is_overdue():
                time_or_dday = f"{t.period_label()} ({t.d_day_badge()})"

            if t.is_completed:
                tag = "completed"
            elif t.is_overdue():
                tag = "overdue"
            elif t.is_new_scan:
                tag = "new_scan"
            else:
                tag = "normal"

            self.day_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(time_or_dday, lbl_name, t.title, st_str),
                tags=(tag,),
            )

        if self.selected_task and f"rt_{self.selected_task.id}" in self._right_task_map:
            target_iid = f"rt_{self.selected_task.id}"
            self.day_tree.selection_set(target_iid)
            self.day_tree.focus(target_iid)
            self.day_tree.see(target_iid)
            self._show_task_detail(self._right_task_map[target_iid])
        elif tasks:
            first_iid = f"rt_{tasks[0].id}"
            self.day_tree.selection_set(first_iid)
            self.day_tree.focus(first_iid)
            self._show_task_detail(tasks[0])
        else:
            self._show_task_detail(None)

    def _show_task_detail(self, task: Optional[TaskItem]) -> None:
        self.selected_task = task
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)

        if not task:
            self.detail_meta_lbl.config(text="선택된 업무가 없습니다.")
            self.detail_text.config(state=tk.DISABLED)
            return

        lbl_name = self.labels_config.get(task.label, {}).get("name", task.label)
        recur_name = RecurrenceType.ALL_LABELS.get(task.recurrence, "반복 없음")
        st_name = TaskStatus.ALL_LABELS.get(task.status, task.status)
        due_line = (
            f"• 기간: {task.start_date} ~ {task.due_date} {task.due_time} ({task.d_day_badge()})"
            if task.is_period
            else f"• 마감기한: {task.due_date} {task.due_time} ({task.d_day_badge()})"
        )
        meta_text = (
            f"• 제목: {task.title}\n"
            f"{due_line}  |  상태: {st_name}\n"
            f"• 분류라벨: {lbl_name}  |  반복: {recur_name}\n"
            f"• 출처/문서번호: {task.source_ref or TaskSource.ALL_LABELS.get(task.source, task.source)}"
        )
        judged = self._ezq_judgement_text(task)
        if judged:
            meta_text += f"\n• 판정: {judged}"
        self.detail_meta_lbl.config(text=meta_text)
        self.detail_text.insert("1.0", task.description or "(상세 내용 없음)")
        self.detail_text.config(state=tk.DISABLED)

    def _select_date(self, date_str: str) -> None:
        self.selected_date_str = date_str
        self.selected_task = None
        self.refresh_all()

    def _select_date_and_task(self, date_str: str, task: TaskItem) -> None:
        self.selected_date_str = date_str
        self.selected_task = task
        self.refresh_all()

    def _show_multi_detail(self, tasks: List[TaskItem], focus_task: TaskItem) -> None:
        """여러 업무를 골랐을 때 상세 칸. 편집·완료 버튼은 커서가 놓인 1건에 적용됨."""
        self.selected_task = focus_task
        self.detail_meta_lbl.config(
            text=(
                f"• {len(tasks)}건 선택됨 — 🗑️ 삭제 버튼이나 Delete 키로 한 번에 지울 수 있습니다.\n"
                f"• 편집·완료 전환은 커서가 놓인 '{focus_task.title}' 1건에 적용됩니다."
            )
        )
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", "\n".join(
            f"• {t.period_label() if t.is_period else t.due_date} {t.due_time}  {t.title}" for t in tasks
        ))
        self.detail_text.config(state=tk.DISABLED)

    def _tree_selection(self, tree: ttk.Treeview, task_map: Dict[str, TaskItem]) -> tuple:
        """(고른 업무 목록, 커서가 놓인 업무). 커서 줄이 선택에서 빠졌으면 고른 것 중 첫 번째."""
        tasks = tasks_in_selection(tree.selection(), task_map)
        focus_task = task_map.get(tree.focus())
        if focus_task not in tasks:
            focus_task = tasks[0] if tasks else None
        return tasks, focus_task

    def _on_day_tree_select(self, _event) -> None:
        tasks, task = self._tree_selection(self.day_tree, self._right_task_map)
        if len(tasks) > 1:
            self._show_multi_detail(tasks, task)
        elif task:
            self._show_task_detail(task)

    def _on_agenda_select(self, _event) -> None:
        tasks, task = self._tree_selection(self.agenda_tree, self._agenda_task_map)
        if len(tasks) > 1:
            self._show_multi_detail(tasks, task)
            return
        if task:
            if task.due_date:
                self.selected_date_str = task.due_date
            self.selected_task = task
            self._render_right_panel()
            self._show_task_detail(task)

    def _prev_month(self) -> None:
        if self.current_month == 1:
            self.current_year -= 1
            self.current_month = 12
        else:
            self.current_month -= 1
        self.refresh_all()

    def _next_month(self) -> None:
        if self.current_month == 12:
            self.current_year += 1
            self.current_month = 1
        else:
            self.current_month += 1
        self.refresh_all()

    def _go_today(self) -> None:
        today = date.today()
        self.current_year = today.year
        self.current_month = today.month
        self.selected_date_str = today.strftime("%Y-%m-%d")
        self.refresh_all()

    def open_add_task_dialog(self, target_date: Optional[str] = None) -> None:
        TaskEditDialog(
            parent=self,
            db=self.db,
            labels_config=self.labels_config,
            task=None,
            default_date=target_date or self.selected_date_str,
            on_saved=self.refresh_all,
        )

    def open_edit_task_dialog(
        self, task: TaskItem, parent: Optional[tk.Misc] = None, after_save: Optional[Callable[[], None]] = None
    ) -> None:
        """parent: 편집 창을 띄울 창 (출근 브리핑처럼 항상 위 창에서 열면 그 위에 뜨게).
        after_save: 저장·삭제 뒤 메인 화면과 함께 갱신할 것 (브리핑 목록 등)."""
        def saved() -> None:
            self.refresh_all()
            if after_save:
                after_save()

        TaskEditDialog(
            parent=parent or self,
            db=self.db,
            labels_config=self.labels_config,
            task=task,
            on_saved=saved,
        )

    def open_edit_selected_task(self) -> None:
        if not self.selected_task:
            messagebox.showinfo("선택 안내", "편집할 업무를 먼저 선택해 주세요.", parent=self)
            return
        self.open_edit_task_dialog(self.selected_task)

    def toggle_selected_task_complete(self) -> None:
        if not self.selected_task or self.selected_task.id is None:
            messagebox.showinfo("선택 안내", "완료 상태를 변경할 업무를 선택해 주세요.", parent=self)
            return
        new_st = (
            TaskStatus.PENDING
            if self.selected_task.status == TaskStatus.COMPLETED
            else TaskStatus.COMPLETED
        )
        next_recur_id = self.db.set_task_status(self.selected_task.id, new_st)
        next_created = self.db.next_task_was_created()
        self.selected_task = self.db.get_task(self.selected_task.id)
        self.refresh_all()
        if next_recur_id is not None:
            next_task = self.db.get_task(next_recur_id)
            if next_task:
                messagebox.showinfo(
                    "반복 일정 자동 생성" if next_created else "반복 일정",
                    next_task_message(next_task, next_created),
                    parent=self,
                )

    def _ezq_judgement_text(self, task: TaskItem) -> str:
        """EzQ 쪽지 업무의 판정 결과 한 줄 (예: '등록 9점 — 부탁 표현 +3, …'). 쪽지 업무가 아니면 ''."""
        key = ezq_key_of(task)
        rec = self.db.get_ezq_message(key) if key else None
        if not rec or not rec.get("verdict"):
            return ""
        text = f"{rec['verdict']} {rec['score']}점 — {', '.join(rec['reasons'])}"
        if rec.get("user_label"):
            text += f"  (사용자 표시: {rec['user_label']})"
        return text + "  · 업무가 아니면 우클릭 → 업무 아님"

    # ── EzQ 쪽지: 업무 아님 / 되돌리기 / 제외된 쪽지 ─────────────────

    def dismiss_task(self, task: TaskItem) -> None:
        """확인 창 없이 지우고 10초 동안 되돌리기 버튼을 보여줌. 다시 켜도 같은 쪽지는 등록되지 않음."""
        handler = review_handler(task, self.ezq_collector, self.workmgmt_collector)
        key = handler.dismiss_task(task) if handler else None
        if key is None:
            return
        self._last_dismissed = (task, key, handler)
        if self.selected_task and self.selected_task.id == task.id:
            self.selected_task = None
        self.refresh_all()
        self._set_status_text(f"🚫 '{task.title}' 업무 아님으로 처리했습니다.", fg="#FBBF24")
        self.undo_btn.pack(side=tk.LEFT, padx=(6, 0))
        if self._undo_job:
            self.after_cancel(self._undo_job)
        self._undo_job = self.after(10000, self._hide_undo)

    def _hide_undo(self) -> None:
        self._undo_job = None
        self._last_dismissed = None
        self.undo_btn.pack_forget()
        self._last_layout_widths = None
        self._schedule_relayout()

    def undo_last_dismiss(self) -> None:
        if not self._last_dismissed:
            return
        task, key, handler = self._last_dismissed
        handler.undo_dismiss(task, key)
        if self._undo_job:
            self.after_cancel(self._undo_job)
        self._hide_undo()
        self.refresh_all()
        self._set_status_text(f"↩ '{task.title}' 업무를 되돌렸습니다.", fg="#4ADE80")

    def open_ezq_review_dialog(self) -> None:
        EzqReviewDialog(self, self.ezq_collector, on_changed=self.refresh_all, workmgmt_collector=self.workmgmt_collector)

    def open_ezq_log_dialog(self) -> None:
        EzqLogDialog(
            self, self.ezq_collector, on_registered=self._on_bg_new_tasks_found, workmgmt_collector=self.workmgmt_collector
        )

    def _current_tree(self) -> tuple:
        """삭제할 목록과 그 표. 목록 보기에서 일정 목록을 마지막으로 썼으면 그것, 아니면 오른쪽 날짜 목록."""
        if self._active_tree is self.agenda_tree and self.view_mode == "AGENDA":
            return self.agenda_tree, self._agenda_task_map
        return self.day_tree, self._right_task_map

    def _selected_tasks(self) -> List[TaskItem]:
        tree, task_map = self._current_tree()
        tasks = tasks_in_selection(tree.selection(), task_map)
        if not tasks and self.selected_task and self.selected_task.id is not None:
            tasks = [self.selected_task]
        return tasks

    def delete_selected_task(self) -> None:
        """목록에서 고른 업무를 모두 삭제 (Ctrl/Shift 클릭·Shift+방향키로 여러 건)."""
        self._delete_tasks(self._selected_tasks())

    def _delete_tasks(self, tasks: List[TaskItem]) -> None:
        tasks = [t for t in tasks if t.id is not None]
        if not tasks:
            return
        refocus = self.focus_get() in (self.day_tree, self.agenda_tree)
        tree, _ = self._current_tree()
        if not messagebox.askyesno("삭제 확인", delete_confirm_message(tasks), parent=self):
            if refocus:
                tree.focus_set()
            return
        for t in tasks:
            self.db.delete_task(t.id)
        self.selected_task = None
        self.refresh_all()
        if len(tasks) > 1:
            self._set_status_text(f"🗑️ 업무 {len(tasks)}건을 삭제했습니다.", fg="#FBBF24")
        if refocus:
            self._focus_tree(tree)

    # ── 우클릭 메뉴 ─────────────────────────────────────────────
    # 메뉴 항목은 (이름, 실행 함수) 목록, None은 구분선. 목록을 따로 만들어 화면 없이도 시험할 수 있게 함

    def _date_menu_items(self, date_str: str) -> List[Optional[tuple]]:
        return [
            (f"➕ {short_date(date_str)}에 새 업무 추가", lambda: self.open_add_task_dialog(date_str)),
            ("⚡ 빠른 입력으로 추가", lambda: self._focus_quick_entry(date_str)),
        ]

    def _task_menu_items(self, task: TaskItem) -> List[Optional[tuple]]:
        items: List[Optional[tuple]] = [
            ("✏️ 편집 (제목·기한·라벨·반복)", lambda: self.open_edit_task_dialog(task)),
            (
                "↩️ 미완료로 되돌리기" if task.is_completed else "✅ 완료 처리",
                lambda: self._toggle_task_complete(task),
            ),
        ]
        if not task.is_completed:
            items.append(None)
            for text, new_date in postpone_targets(task):
                items.append((text, lambda nd=new_date: self._reschedule_task(task, nd)))
        items.append(None)
        if (self.ezq_collector and ezq_key_of(task)) or (
            self.workmgmt_collector
            and (self.workmgmt_collector.is_list_task(task) or self.workmgmt_collector.is_doc_task(task))
        ):
            items.append(("🚫 업무 아님 (다시 등록 안 함)", lambda: self.dismiss_task(task)))
        items += [
            ("🗑️ 삭제", lambda: self._delete_task(task)),
            None,
            (
                f"➕ {short_date(task.due_date or self.selected_date_str)}에 새 업무 추가",
                lambda: self.open_add_task_dialog(task.due_date or self.selected_date_str),
            ),
        ]
        return items

    def _popup_menu(self, event, items: List[Optional[tuple]]) -> None:
        if self._ctx_menu is not None:
            self._ctx_menu.destroy()
        menu = tk.Menu(self, tearoff=0, font=("Malgun Gothic", 9), activebackground="#DBEAFE", activeforeground="#0F172A")
        for item in items:
            if item is None:
                menu.add_separator()
            else:
                menu.add_command(label=item[0], command=item[1])
        self._ctx_menu = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_date_right_click(self, event, date_str: str) -> None:
        self._select_date(date_str)
        self._popup_menu(event, self._date_menu_items(date_str))

    def _on_task_right_click(self, event, task: TaskItem, date_str: str) -> None:
        self._select_date_and_task(date_str, task)
        self._popup_menu(event, self._task_menu_items(task))

    def _on_tree_right_click(self, event, tree: ttk.Treeview, task_map: Dict[str, TaskItem]) -> None:
        row = tree.identify_row(event.y)
        task = task_map.get(row) if row else None
        if task:
            tree.selection_set(row)
            tree.focus(row)
            self._show_task_detail(task)
            self._popup_menu(event, self._task_menu_items(task))
        else:
            target = self.selected_date_str if tree is self.day_tree else date.today().isoformat()
            self._popup_menu(event, self._date_menu_items(target))

    def _toggle_task_complete(self, task: TaskItem) -> None:
        self.selected_task = task
        self.toggle_selected_task_complete()

    def _delete_task(self, task: TaskItem) -> None:
        self.selected_task = task
        self._delete_tasks([task])

    def _reschedule_task(self, task: TaskItem, new_date: str) -> None:
        """기한 날짜 옮기기 (우클릭 미루기, 끌어다 놓기 공용)."""
        if task.id is None or new_date == task.due_date:
            return
        old_date = task.due_date
        updated = self.db.reschedule_task(task.id, new_date)
        if not updated:
            return
        self.selected_task = updated
        self.selected_date_str = new_date
        # 옮긴 날짜가 지금 달력(6주) 밖이면 그 달로 넘김 ('다음 주 월요일로'가 다음 달로 넘어가면 안 보이던 문제)
        self.current_year, self.current_month = month_to_show(self.current_year, self.current_month, new_date)
        self.refresh_all()
        self._flash_quick(
            f"📅 기한 변경: {updated.title}  {short_date(old_date)} → {short_date(new_date)}", "#B45309"
        )

    # ── 일정 칩 끌어다 놓기 (월간 캘린더) ─────────────────────────

    def _chip_press(self, event, task: TaskItem, date_str: str) -> None:
        if self._chip_clicks.click(("task", task.id), event.time):
            self._drag = None
            self.open_edit_task_dialog(task)
            return
        self._drag = {"task": task, "from": date_str, "x": event.x_root, "y": event.y_root, "active": False}

    def _chip_motion(self, event) -> None:
        d = self._drag
        if not d:
            return
        if not d["active"]:
            if abs(event.x_root - d["x"]) + abs(event.y_root - d["y"]) < 6:
                return
            d["active"] = True
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            try:
                ghost.attributes("-topmost", True)
                ghost.attributes("-alpha", 0.9)
            except tk.TclError:
                pass
            tk.Label(
                ghost,
                text=f"📅 {d['task'].title[:20]}",
                font=("Malgun Gothic", 9, "bold"),
                bg="#FEF3C7",
                fg="#92400E",
                padx=8,
                pady=3,
                relief=tk.SOLID,
                bd=1,
            ).pack()
            d["ghost"] = ghost
            self.config(cursor="fleur")
        # 끌고 있는 표시는 마우스 오른쪽 아래에 둬서, 놓을 칸을 찾을 때 가리지 않게 함
        d["ghost"].geometry(f"+{event.x_root + 14}+{event.y_root + 10}")
        self._highlight_drop_cell(self._cell_at(event.x_root, event.y_root))

    def _chip_release(self, event) -> None:
        d, self._drag = self._drag, None
        if not d:
            return
        if not d["active"]:
            self._select_date_and_task(d["from"], d["task"])
            return
        target = self._cell_at(event.x_root, event.y_root)
        d["ghost"].destroy()
        self.config(cursor="")
        self._highlight_drop_cell(None)
        new_date = self._cell_dates.get(str(target)) if target is not None else None
        if new_date and new_date != d["from"]:
            # 기간 업무는 가운데 칸에서 끌어도 옮긴 날수만큼 시작·끝을 함께 옮김
            self._reschedule_task(d["task"], drag_due_date(d["task"], d["from"], new_date))
        elif self._refresh_pending:
            self.refresh_all()

    def _cell_at(self, x_root: int, y_root: int) -> Optional[tk.Widget]:
        """화면 좌표가 들어 있는 캘린더 날짜 칸. 칸 위치로 찾으므로 끌고 있는 표시나 다른 창에 가려도 됨."""
        for cell in self.day_cells:
            x, y = cell.winfo_rootx(), cell.winfo_rooty()
            if x <= x_root < x + cell.winfo_width() and y <= y_root < y + cell.winfo_height():
                return cell
        return None

    def _highlight_drop_cell(self, cell: Optional[tk.Widget]) -> None:
        if self._drop_cell and self._drop_cell[0] is not cell:
            old, color, thick = self._drop_cell
            try:
                old.config(highlightbackground=color, highlightthickness=thick)
            except tk.TclError:
                pass
            self._drop_cell = None
        if cell is not None and self._drop_cell is None:
            self._drop_cell = (cell, cell.cget("highlightbackground"), cell.cget("highlightthickness"))
            cell.config(highlightbackground="#F59E0B", highlightthickness=3)

    # ── 빠른 입력 ───────────────────────────────────────────────

    def _parse_quick(self) -> Optional[TaskItem]:
        if self._quick_extractor is None:
            ext = getattr(getattr(self.clipboard_collector, "parser", None), "extractor", None)
            self._quick_extractor = ext if isinstance(ext, RuleBasedExtractor) else RuleBasedExtractor(self.config_data)
        return parse_quick_entry(self._quick_extractor, self.quick_var.get(), self.selected_date_str)

    def _update_quick_preview(self) -> None:
        if self._quick_flash_job is not None:
            return  # 방금 한 일의 결과 문구를 잠시 보여 주는 중
        task = self._parse_quick()
        if task is None:
            text, fg = f"예) 내일 오후 3시까지 출장계획 제출 → Enter  (날짜를 안 쓰면 {short_date(self.selected_date_str)})", "#94A3B8"
        else:
            label = self.labels_config.get(task.label, {}).get("name", task.label)
            recur = "" if task.recurrence == RecurrenceType.NONE else f" · {RecurrenceType.ALL_LABELS[task.recurrence]}"
            text = f"→ {short_date(task.due_date)} {task.due_time} · {task.title} · {label}{recur}"
            fg = "#1D4ED8"
        self.quick_preview_lbl.config(text=text, fg=fg)

    def _on_quick_typed(self) -> None:
        # 결과 문구를 보여 주는 중이라도 입력을 시작하면 바로 미리보기로 바꿈
        if self._quick_flash_job is not None and self.quick_var.get():
            self.after_cancel(self._quick_flash_job)
            self._quick_flash_job = None
        self._update_quick_preview()

    def _flash_quick(self, text: str, fg: str, ms: int = 5000) -> None:
        if self._quick_flash_job is not None:
            self.after_cancel(self._quick_flash_job)
        self.quick_preview_lbl.config(text=text, fg=fg)

        def done():
            self._quick_flash_job = None
            self._update_quick_preview()

        self._quick_flash_job = self.after(ms, done)

    def _focus_quick_entry(self, date_str: Optional[str] = None) -> None:
        if date_str:
            self._select_date(date_str)
        self.quick_entry.focus_set()
        self._update_quick_preview()

    def add_quick_entry(self) -> Optional[TaskItem]:
        task = self._parse_quick()
        if task is None:
            self.quick_entry.focus_set()
            return None
        task_id, _ = self.db.add_task(task, skip_if_duplicate=False)
        saved = self.db.get_task(task_id) if task_id else None
        if not saved:
            return None
        self.quick_var.set("")
        due = datetime.strptime(saved.due_date, "%Y-%m-%d").date()
        self.current_year, self.current_month = due.year, due.month
        self.selected_date_str = saved.due_date
        self.selected_task = saved
        self.refresh_all()
        self._flash_quick(f"✔ 등록: {short_date(saved.due_date)} {saved.due_time} · {saved.title}", "#15803D")
        return saved

    def open_stats_dialog(self) -> None:
        WorkStatsDialog(self, self.db.get_work_stats(), date.today())

    def scan_folders_now(self) -> None:
        results = self.folder_collector.scan_all(force=False)
        self.refresh_all()
        if any(not r.excluded for r in results):
            ImportResultDialog(self, "감시 폴더 스캔 결과", results, show_excluded=False)
        else:
            messagebox.showinfo(
                "폴더 스캔 완료",
                f"감시 폴더 {len(self.folder_collector.folders)}곳을 확인했으나 새로 추가된 문서가 없습니다.",
                parent=self,
            )

    def _watch_folder_summary(self) -> str:
        # 긴 경로가 상단 버튼을 밀어내지 않도록 폴더 이름만 표시 (전체 경로는 설정 창에서 확인)
        paths = self.folder_collector.watch_folders
        first = os.path.basename(os.path.normpath(paths[0])) if paths else "-"
        if len(first) > 16:
            first = first[:15] + "…"
        return f"자동 감시 폴더: {first}" + (f" 외 {len(paths) - 1}곳" if len(paths) > 1 else "")

    def open_folder_settings_dialog(self) -> None:
        FolderSettingsDialog(self, self.folder_collector.folders, on_save=self._apply_folder_settings)

    def _apply_folder_settings(self, folders: List[WatchFolder]) -> None:
        try:
            save_config_values(self.config_path, watch_folders=[f.to_config() for f in folders])
        except (OSError, ValueError) as e:
            messagebox.showerror("감시 폴더 설정", f"설정 파일(config.json)을 저장하지 못했습니다.\n{e}", parent=self)
            return
        self.config_data["watch_folders"] = [f.to_config() for f in folders]
        self.folder_collector.set_folders(folders)
        self._set_status_text(self._watch_folder_summary())
        # 저장 직후 바로 확인해 결과를 보여줌 (이후에는 백그라운드에서 자동 확인)
        results = self.folder_collector.scan_all(force=False)
        self.refresh_all()
        ImportResultDialog(self, "감시 폴더 확인 결과", results, show_excluded=False)

    def scan_clipboard_now(self) -> None:
        created = self.clipboard_collector.collect_from_tk_clipboard(self, force=True)
        self.refresh_all()
        if created:
            messagebox.showinfo(
                "클립보드 즉시 스캔 완료",
                f"클립보드 텍스트에서 {len(created)}건의 업무와 기한을 추출해 등록했습니다!\n"
                + "\n".join(f"• [{t.due_date}] {t.title}" for t in created[:5]),
                parent=self,
            )
        else:
            messagebox.showwarning(
                "클립보드 스캔 안내",
                "클립보드에서 새로운 업무 지시/문서 내용을 찾지 못했거나 이미 등록된 항목입니다.",
                parent=self,
            )

    def open_paste_scan_dialog(self) -> None:
        QuickPasteScanDialog(
            parent=self,
            clipboard_collector=self.clipboard_collector,
            on_registered=lambda _cnt: self.refresh_all(),
        )

    def import_file_dialog(self) -> None:
        file_paths = filedialog.askopenfilenames(
            parent=self,
            title="불러올 파일 선택 (Ctrl·Shift 클릭 또는 Ctrl+A로 여러 개 선택)",
            filetypes=[
                ("지원 문서 파일", "*.txt *.html *.htm *.md *.log *.json *.pdf"),
                ("모든 파일", "*.*"),
            ],
        )
        if not file_paths:
            return
        results = [self.folder_collector.scan_file(fp, force=True) for fp in file_paths]
        self.refresh_all()
        ImportResultDialog(self, "파일 불러오기 결과", results, show_excluded=True)

    def open_import_check_dialog(self) -> None:
        file_paths = filedialog.askopenfilenames(
            parent=self,
            title="점검할 파일 선택 (등록하지 않고 인식 결과만 확인)",
            filetypes=[
                ("공문 PDF·메신저 파일", "*.pdf *.txt"),
                ("모든 파일", "*.*"),
            ],
        )
        if not file_paths:
            return
        full, share = check_files(list(file_paths), parser=self.folder_collector.parser, db=self.db)
        ImportCheckDialog(self, full, share)

    def open_briefing_popup(self, newly_scanned_count: int = 0) -> None:
        StartupBriefingPopup(
            parent=self,
            db=self.db,
            labels_config=self.labels_config,
            newly_scanned_count=newly_scanned_count,
            on_task_edit=self.open_edit_task_dialog,
            on_refresh=self.refresh_all,
        )

    def _toggle_startup(self) -> None:
        desired = self.startup_var.get()
        ok = set_startup_enabled(desired)
        if not ok:
            self.startup_var.set(not desired)
            messagebox.showwarning(
                "설정 안내",
                "시작프로그램 레지스트리 설정을 변경하지 못했습니다.",
                parent=self,
            )

    def _bg_scan_tick(self) -> int:
        created = self.folder_collector.collect_and_register(force=False, time_budget=FOLDER_SCAN_BUDGET_SEC)
        if created:
            self.after(0, self._on_bg_new_tasks_found, created)
        # 날짜가 바뀌면 (켜 둔 채 자정을 넘김) 끝난 반복 기간의 다음 기간을 만들고, 화면의 '오늘'을 새 날로 옮김
        # (예전엔 다음 기간이 생길 때만 다시 그려서, 어제를 오늘로 표시하고 새로 기한이 지난 업무도 빨갛게 안 바뀌었음)
        old_day = self._period_check_day
        if old_day != date.today():
            self._activate_expired_periods()
            if old_day is not None:
                self.after(0, self._on_new_day, old_day)
        return len(created)

    def _on_new_day(self, old_day: date) -> None:
        """자정을 넘긴 뒤 한 번: 어제(그때의 오늘)를 보고 있었으면 새 오늘로 옮기고 화면을 다시 그림."""
        today = date.today()
        if self.selected_date_str == old_day.isoformat():
            self.selected_date_str = today.isoformat()
            self.selected_task = None
            if (self.current_year, self.current_month) == (old_day.year, old_day.month):
                self.current_year, self.current_month = today.year, today.month
        self.refresh_all()

    def _activate_expired_periods(self) -> List[int]:
        """끝날이 지난 반복 기간 업무의 다음 기간 활성화 (하루 한 번). 실패해도 앱은 계속."""
        self._period_check_day = date.today()
        try:
            return self.db.activate_expired_periods()
        except Exception:  # noqa: BLE001  백그라운드 확인 실패로 앱이 멈추면 안 됨
            return []

    def _bg_fast_tick(self) -> int:
        """몇 초마다: EzQ 쪽지 창과 업무관리시스템 문서 창 확인."""
        created: List[TaskItem] = []
        if self.ezq_collector:
            created += self.ezq_collector.collect_and_register()
        merged: List[TaskItem] = []
        if self.workmgmt_collector:
            created += self.workmgmt_collector.collect_and_register()
            merged = list(self.workmgmt_collector.merged)
            judged = (list(self.workmgmt_collector.confirmed), list(self.workmgmt_collector.removed))
        else:
            judged = ([], [])
        if created:
            self.after(0, self._on_bg_new_tasks_found, created)
        elif judged[0] or judged[1]:
            self.after(0, self._on_bg_list_doc_judged, *judged)
        elif merged:
            self.after(0, self._on_bg_merged, merged)
        return len(created)

    def _on_bg_list_doc_judged(self, confirmed: List[TaskItem], removed: List[TaskItem]) -> None:
        """배부 문서를 열어 본문으로 판정한 결과를 화면에 반영."""
        self.refresh_all()
        now_hm = datetime.now().strftime("%H:%M:%S")
        self._set_status_text(f"[{now_hm}] {list_doc_judged_message(confirmed, removed)}", fg="#4ADE80")

    def _on_bg_merged(self, merged: List[TaskItem]) -> None:
        self.refresh_all()
        now_hm = datetime.now().strftime("%H:%M:%S")
        self._set_status_text(f"[{now_hm}] 업무관리시스템 문서: 같은 일을 받은 쪽지 업무 {len(merged)}건에 공문 출처 추가", fg="#4ADE80")

    def _toggle_workmgmt(self) -> None:
        enabled = self.workmgmt_var.get()
        self.workmgmt_collector.enabled = enabled
        try:
            save_config_values(self.config_path, workmgmt_auto_collect=enabled)
        except (OSError, ValueError):
            messagebox.showwarning("설정 안내", "config.json에 설정을 저장하지 못했습니다. 이번 실행에만 적용됩니다.", parent=self)

    def _toggle_ezq(self) -> None:
        enabled = self.ezq_var.get()
        self.ezq_collector.enabled = enabled
        try:
            save_config_values(self.config_path, ezq_auto_collect=enabled)
        except (OSError, ValueError):
            messagebox.showwarning("설정 안내", "config.json에 설정을 저장하지 못했습니다. 이번 실행에만 적용됩니다.", parent=self)

    def _on_bg_new_tasks_found(self, created: List[TaskItem]) -> None:
        self.refresh_all()
        now_hm = datetime.now().strftime("%H:%M:%S")
        self._set_status_text(f"[{now_hm}] {bg_new_tasks_message(created)}", fg="#4ADE80")

    def _set_status_text(self, text: str, fg: Optional[str] = None) -> None:
        """상단 왼쪽 상태 글자를 바꾸고, 글자 길이가 달라진 만큼 상단 버튼 크기를 다시 맞춤
        (창 크기가 그대로면 다시 맞추지 않아 버튼이 왼쪽에서 잘리던 문제)."""
        self.status_sub_lbl.config(text=text, **({"fg": fg} if fg else {}))
        self._last_layout_widths = None
        self._schedule_relayout()

    def _bg_reminder_tick(self, due_tasks: List[TaskItem]) -> None:
        self.after(0, self._show_reminder_popup_safe, due_tasks)

    def _show_reminder_popup_safe(self, due_tasks: List[TaskItem]) -> None:
        if self._active_reminder_popup and self._active_reminder_popup.winfo_exists():
            return
        self._active_reminder_popup = DeadlineReminderPopup(
            parent=self,
            db=self.db,
            due_tasks=due_tasks,
            on_refresh=self.refresh_all,
            on_edit_task=self.open_edit_task_dialog,
        )

    def _on_close(self) -> None:
        if hasattr(self, "scheduler"):
            self.scheduler.stop()
        self.destroy()
