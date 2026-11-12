"""출근 후 프로그램 실행 시 브리핑 팝업 및 기한 도래 자동 알림/Snooze 모달 (구상 2-3, 2-4, 2-5)."""

from datetime import date
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from core.database import DatabaseManager
from core.models import TaskItem, TaskSource, TaskStatus


class StartupBriefingPopup(tk.Toplevel):
    """출근 후 프로그램 실행 시 최상단(Topmost)에 표시되는 오늘의 업무 브리핑 팝업."""

    def __init__(
        self,
        parent: tk.Widget,
        db: DatabaseManager,
        labels_config: Dict[str, dict],
        newly_scanned_count: int = 0,
        on_task_edit: Optional[Callable[[TaskItem], None]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.db = db
        self.labels_config = labels_config
        self.newly_scanned_count = newly_scanned_count
        self.on_task_edit = on_task_edit
        self.on_refresh = on_refresh
        self._task_lookup: Dict[str, TaskItem] = {}

        self.title("🔔 오늘의 출근 업무 브리핑 & 기한 점검")
        self.geometry("760x560")
        self.configure(bg="#F8FAFC")
        self.attributes("-topmost", True)
        self.transient(parent)

        self._build_ui()
        self._populate_data()
        self._center_window()
        self.lift()
        self.focus_force()

    def _center_window(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_ui(self) -> None:
        # 상단 배너
        banner = tk.Frame(self, bg="#1E3A8A", padx=20, pady=14)
        banner.pack(fill=tk.X)

        today_str = date.today().strftime("%Y년 %m월 %d일")
        tk.Label(
            banner,
            text=f"📢 [{today_str}] 출근 업무 현황 및 마감 기한 브리핑",
            font=("Malgun Gothic", 13, "bold"),
            bg="#1E3A8A",
            fg="white",
        ).pack(anchor="w")

        self.summary_sub_lbl = tk.Label(
            banner,
            text="미완료 이월 업무 및 오늘/내일 마감 기한을 확인하세요.",
            font=("Malgun Gothic", 9),
            bg="#1E3A8A",
            fg="#BFDBFE",
        )
        self.summary_sub_lbl.pack(anchor="w", pady=(4, 0))

        # 요약 카드 영역
        cards_frame = tk.Frame(self, bg="#F8FAFC", padx=18, pady=12)
        cards_frame.pack(fill=tk.X)
        for i in range(4):
            cards_frame.columnconfigure(i, weight=1)

        self.card_overdue = self._create_stat_card(cards_frame, 0, "🚨 기한경과 (자동이월)", "0건", "#FEF2F2", "#DC2626")
        self.card_today = self._create_stat_card(cards_frame, 1, "📌 오늘 마감 (D-Day)", "0건", "#EFF6FF", "#2563EB")
        self.card_tomorrow = self._create_stat_card(cards_frame, 2, "⏳ 내일/금주 마감", "0건", "#FFFBEB", "#D97706")
        self.card_new = self._create_stat_card(cards_frame, 3, "📥 신규 자동스캔 문서", "0건", "#F0FDF4", "#16A34A")

        # 목록 영역 (Treeview)
        list_frame = tk.Frame(self, bg="#F8FAFC", padx=18, pady=4)
        list_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            list_frame,
            text="▼ 우선 확인 대상 업무 목록 (더블클릭 시 상세 편집 / 기한 변경)",
            font=("Malgun Gothic", 9, "bold"),
            bg="#F8FAFC",
            fg="#334155",
        ).pack(anchor="w", pady=(0, 6))

        columns = ("category", "dday", "due", "title", "source")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=12)
        self.tree.heading("category", text="구분")
        self.tree.heading("dday", text="기한상태")
        self.tree.heading("due", text="마감일시")
        self.tree.heading("title", text="업무 제목 / 배부 문서명")
        self.tree.heading("source", text="출처 / 문서번호")

        self.tree.column("category", width=120, anchor="center")
        self.tree.column("dday", width=110, anchor="center")
        self.tree.column("due", width=115, anchor="center")
        self.tree.column("title", width=250, anchor="w")
        self.tree.column("source", width=120, anchor="w")

        self.tree.tag_configure("overdue", background="#FEE2E2", foreground="#991B1B")
        self.tree.tag_configure("today", background="#DBEAFE", foreground="#1E40AF")
        self.tree.tag_configure("new_scan", background="#DCFCE7", foreground="#166534")
        self.tree.tag_configure("upcoming", background="#FFFFFF", foreground="#334155")

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<Double-1>", self._on_double_click)

        # 하단 액션 바
        action_bar = tk.Frame(self, bg="#F8FAFC", padx=18, pady=12)
        action_bar.pack(fill=tk.X)

        tk.Button(
            action_bar,
            text="✅ 선택 항목 완료 처리",
            font=("Malgun Gothic", 9, "bold"),
            bg="#16A34A",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            command=self._mark_selected_complete,
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            action_bar,
            text="✏️ 선택 항목 편집/기한변경",
            font=("Malgun Gothic", 9),
            bg="#E2E8F0",
            fg="#1E293B",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            command=self._edit_selected,
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            action_bar,
            text="⏰ 선택 항목 2시간 뒤 다시 알림",
            font=("Malgun Gothic", 9),
            bg="#FEF3C7",
            fg="#92400E",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            command=self._snooze_selected_2h,
        ).pack(side=tk.LEFT)

        tk.Button(
            action_bar,
            text="확인 및 캘린더 열기",
            font=("Malgun Gothic", 10, "bold"),
            bg="#2563EB",
            fg="white",
            relief=tk.FLAT,
            padx=18,
            pady=6,
            command=self._on_close_ack,
        ).pack(side=tk.RIGHT)

    def _create_stat_card(
        self, parent: tk.Widget, col: int, title: str, count_text: str, bg_color: str, fg_color: str
    ) -> tk.Label:
        card = tk.Frame(parent, bg=bg_color, highlightbackground=fg_color, highlightthickness=1, padx=10, pady=8)
        card.grid(row=0, column=col, sticky="ew", padx=4)
        tk.Label(card, text=title, font=("Malgun Gothic", 8, "bold"), bg=bg_color, fg=fg_color).pack(anchor="w")
        val_lbl = tk.Label(card, text=count_text, font=("Malgun Gothic", 14, "bold"), bg=bg_color, fg=fg_color)
        val_lbl.pack(anchor="e", pady=(2, 0))
        return val_lbl

    def _populate_data(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._task_lookup.clear()

        summary = self.db.get_briefing_summary()
        overdue = summary["overdue"]
        today_list = summary["today"]
        tomorrow_list = summary["tomorrow"]
        upcoming_week = summary["upcoming_week"]
        new_scans = summary["new_scan"]

        self.card_overdue.config(text=f"{len(overdue)}건")
        self.card_today.config(text=f"{len(today_list)}건")
        self.card_tomorrow.config(text=f"{len(tomorrow_list) + len(upcoming_week)}건")
        self.card_new.config(text=f"{len(new_scans)}건")

        if overdue:
            self.summary_sub_lbl.config(
                text=f"⚠️ 기한이 지난 미완료 업무가 {len(overdue)}건 있습니다! 누락 방지를 위해 최상단에 자동 이월되었습니다.",
                fg="#FCA5A5",
            )
        else:
            self.summary_sub_lbl.config(
                text=f"오늘 마감 {len(today_list)}건, 신규 자동 스캔 {len(new_scans)}건의 업무가 있습니다.",
                fg="#BFDBFE",
            )

        inserted_ids = set()

        def insert_group(items: List[TaskItem], cat_label: str, tag: str):
            for t in items:
                if t.id in inserted_ids:
                    continue
                inserted_ids.add(t.id)
                iid = f"task_{t.id}"
                self._task_lookup[iid] = t
                src_text = t.source_ref or TaskSource.ALL_LABELS.get(t.source, t.source)
                new_badge = "[신규스캔] " if t.is_new_scan else ""
                self.tree.insert(
                    "",
                    tk.END,
                    iid=iid,
                    values=(
                        cat_label,
                        t.d_day_badge(),
                        f"{t.due_date} {t.due_time}",
                        f"{new_badge}{t.title}",
                        src_text,
                    ),
                    tags=(tag,),
                )

        insert_group(overdue, "🚨 기한경과(이월)", "overdue")
        insert_group(today_list, "📌 오늘마감", "today")
        insert_group(new_scans, "📥 신규자동스캔", "new_scan")
        insert_group(tomorrow_list, "⏳ 내일마감", "upcoming")
        insert_group(upcoming_week, "📅 금주예정", "upcoming")

    def _get_selected_task(self) -> Optional[TaskItem]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._task_lookup.get(sel[0])

    def _mark_selected_complete(self) -> None:
        task = self._get_selected_task()
        if not task or task.id is None:
            return
        self.db.set_task_status(task.id, TaskStatus.COMPLETED)
        self._populate_data()
        if self.on_refresh:
            self.on_refresh()

    def _edit_selected(self) -> None:
        task = self._get_selected_task()
        if not task:
            return
        if self.on_task_edit:
            # 편집 창은 브리핑 위에 띄우고, 저장한 뒤에 브리핑 목록을 다시 읽음 (편집 창은 바로 돌아오므로 여기서 갱신하면 옛 값)
            self.on_task_edit(task, parent=self, after_save=self._refresh_if_open)

    def _refresh_if_open(self) -> None:
        if self.winfo_exists():
            self._populate_data()

    def _on_double_click(self, _event) -> None:
        self._edit_selected()

    def _snooze_selected_2h(self) -> None:
        task = self._get_selected_task()
        if not task or task.id is None:
            return
        self.db.snooze_task(task.id, 120)
        self._populate_data()
        if self.on_refresh:
            self.on_refresh()

    def _on_close_ack(self) -> None:
        self.db.mark_scan_reviewed()
        if self.on_refresh:
            self.on_refresh()
        self.destroy()


class DeadlineReminderPopup(tk.Toplevel):
    """기한 도래 시 표시되는 알림 & 다시 알림(Snooze) 팝업 (구상 2-4, 2-5)."""

    def __init__(
        self,
        parent: tk.Widget,
        db: DatabaseManager,
        due_tasks: List[TaskItem],
        on_refresh: Optional[Callable[[], None]] = None,
        on_edit_task: Optional[Callable[[TaskItem], None]] = None,
    ):
        super().__init__(parent)
        self.db = db
        self.due_tasks = due_tasks
        self.on_refresh = on_refresh
        self.on_edit_task = on_edit_task
        self._task_lookup: Dict[str, TaskItem] = {}

        self.title("⏰ 업무 기한 도래 알림")
        self.geometry("620x400")
        self.configure(bg="#FFFBEB")
        self.attributes("-topmost", True)
        self.transient(parent)

        # 알림 표시 시각 기록
        for t in due_tasks:
            if t.id is not None:
                self.db.mark_notified(t.id)

        self._build_ui()
        self._position_bottom_right()
        self.lift()
        self.focus_force()

    def _position_bottom_right(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, sw - w - 30)
        y = max(0, sh - h - 70)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg="#B45309", padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text=f"⏰ 마감 기한 도래 / 미완료 업무 알림 ({len(self.due_tasks)}건)",
            font=("Malgun Gothic", 11, "bold"),
            bg="#B45309",
            fg="white",
        ).pack(anchor="w")

        body = tk.Frame(self, bg="#FFFBEB", padx=14, pady=10)
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("dday", "due", "title")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=8)
        self.tree.heading("dday", text="상태")
        self.tree.heading("due", text="마감기한")
        self.tree.heading("title", text="업무 제목")

        self.tree.column("dday", width=110, anchor="center")
        self.tree.column("due", width=120, anchor="center")
        self.tree.column("title", width=340, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)

        for t in self.due_tasks:
            iid = f"rem_{t.id}"
            self._task_lookup[iid] = t
            self.tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(t.d_day_badge(), f"{t.due_date} {t.due_time}", t.title),
            )

        if self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])

        # Snooze 및 완료 버튼 바
        btn_frame = tk.Frame(self, bg="#FFFBEB", padx=14, pady=10)
        btn_frame.pack(fill=tk.X)

        tk.Button(
            btn_frame,
            text="✅ 완료 처리",
            font=("Malgun Gothic", 9, "bold"),
            bg="#16A34A",
            fg="white",
            relief=tk.FLAT,
            padx=10,
            pady=5,
            command=self._complete_selected,
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            btn_frame,
            text="30분 뒤 알림",
            font=("Malgun Gothic", 9),
            bg="#FDE68A",
            fg="#78350F",
            relief=tk.FLAT,
            padx=8,
            pady=5,
            command=lambda: self._snooze_selected(30),
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            btn_frame,
            text="2시간 뒤 알림",
            font=("Malgun Gothic", 9),
            bg="#FDE68A",
            fg="#78350F",
            relief=tk.FLAT,
            padx=8,
            pady=5,
            command=lambda: self._snooze_selected(120),
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            btn_frame,
            text="🌅 내일 아침 알림",
            font=("Malgun Gothic", 9),
            bg="#FDE68A",
            fg="#78350F",
            relief=tk.FLAT,
            padx=8,
            pady=5,
            command=self._snooze_tomorrow,
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            btn_frame,
            text="닫기",
            font=("Malgun Gothic", 9),
            bg="#E2E8F0",
            fg="#334155",
            relief=tk.FLAT,
            padx=12,
            pady=5,
            command=self.destroy,
        ).pack(side=tk.RIGHT)

    def _get_selected(self) -> Optional[TaskItem]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._task_lookup.get(sel[0])

    def _complete_selected(self) -> None:
        t = self._get_selected()
        if not t or t.id is None:
            return
        self.db.set_task_status(t.id, TaskStatus.COMPLETED)
        self.tree.delete(f"rem_{t.id}")
        if self.on_refresh:
            self.on_refresh()
        if not self.tree.get_children():
            self.destroy()

    def _snooze_selected(self, minutes: int) -> None:
        t = self._get_selected()
        if not t or t.id is None:
            return
        self.db.snooze_task(t.id, minutes)
        self.tree.delete(f"rem_{t.id}")
        if self.on_refresh:
            self.on_refresh()
        if not self.tree.get_children():
            self.destroy()

    def _snooze_tomorrow(self) -> None:
        t = self._get_selected()
        if not t or t.id is None:
            return
        self.db.snooze_until_tomorrow_morning(t.id)
        self.tree.delete(f"rem_{t.id}")
        if self.on_refresh:
            self.on_refresh()
        if not self.tree.get_children():
            self.destroy()
