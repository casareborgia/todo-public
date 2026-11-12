"""일정/할일 자유 편집 모달 및 텍스트 붙여넣기 스마트 등록 다이얼로그 (구상 2-4, 2-5)."""

from datetime import date, timedelta
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, Optional

from collectors.clipboard_collector import ClipboardCollector
from core.database import DatabaseManager
from core.models import RecurrenceType, TaskItem, TaskSource, TaskStatus


from core.recurrence import label_for_recurrence

def normalize_date_input(text: str) -> Optional[str]:
    """입력한 날짜를 YYYY-MM-DD로. '2026-11-5', '2026.11.5', '2026/11/05'도 받음. 틀리면 None.

    0을 빼고 쓴 날짜를 그대로 저장하면 문자열 비교·검색이 어긋나 캘린더·목록에서 사라졌음."""
    parts = [p for p in re.split(r"\s*[-./]\s*", (text or "").strip().rstrip(".")) if p]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2])).strftime("%Y-%m-%d")
    except ValueError:
        return None


def normalize_time_input(text: str) -> Optional[str]:
    """입력한 시각을 HH:MM으로 ('9:00' → '09:00'). 빈 값은 18:00, 틀리면 None."""
    text = (text or "").strip()
    if not text:
        return "18:00"
    m = re.fullmatch(r"(\d{1,2})\s*:\s*(\d{2})", text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def apply_edit(fresh: TaskItem, before: TaskItem, values: Dict) -> TaskItem:
    """편집 창의 값을 DB에서 다시 읽은 업무(fresh)에 덮어씀.

    창을 연 뒤 알림·다시 알림·수집기 병합으로 바뀐 다른 칸은 그대로 두고,
    마감일·시각을 바꿨으면 알림 상태(보낸 알림·다시 알림)를 초기화해 새 기한에 다시 알리게 함."""
    if (values["due_date"], values["due_time"]) != (before.due_date, before.due_time):
        fresh.due_estimated = False  # 사용자가 직접 정한 기한은 확정값
        fresh.notified_at = None
        fresh.snooze_until = None
    for key, value in values.items():
        setattr(fresh, key, value)
    fresh.is_new_scan = False
    return fresh


class TaskEditDialog(tk.Toplevel):
    """할 일(TaskItem) 추가 및 자유 편집 모달 창."""

    def __init__(
        self,
        parent: tk.Widget,
        db: DatabaseManager,
        labels_config: Dict[str, dict],
        task: Optional[TaskItem] = None,
        default_date: Optional[str] = None,
        on_saved: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.db = db
        self.labels_config = labels_config
        self.task = task
        self.on_saved = on_saved

        is_edit = task is not None and task.id is not None
        self.title("업무 일정 편집" if is_edit else "새 업무 일정 등록")
        self.geometry("580x660")
        self.configure(bg="#F8FAFC")
        self.transient(parent)
        # 항상 위 창(출근 브리핑)에서 열면 이 창도 항상 위로: 아니면 브리핑 뒤에 가려진 채 다른 클릭만 막힘
        try:
            if bool(int(parent.winfo_toplevel().attributes("-topmost"))):
                self.attributes("-topmost", True)
        except (tk.TclError, ValueError, AttributeError):
            pass
        self.grab_set()

        self._build_ui(is_edit, default_date)
        self._center_window()
        # Esc: 닫기(저장 안 함), Ctrl+Enter: 저장 — 상세 내용 칸에서 Enter는 줄바꿈이므로 저장은 Ctrl+Enter
        self.bind("<Escape>", lambda _e: self.destroy())
        for seq in ("<Control-Return>", "<Control-KP_Enter>"):
            self.bind(seq, lambda _e: (self._on_save(), "break")[1])

    def _center_window(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self, is_edit: bool, default_date: Optional[str]) -> None:
        main_frame = tk.Frame(self, bg="#F8FAFC", padx=20, pady=16)
        main_frame.pack(fill=tk.BOTH, expand=True)

        header_lbl = tk.Label(
            main_frame,
            text="✏️ 업무 상세 정보 및 기한 설정" if is_edit else "➕ 새 업무 일정 추가",
            font=("Malgun Gothic", 13, "bold"),
            bg="#F8FAFC",
            fg="#1E293B",
        )
        header_lbl.pack(anchor="w", pady=(0, 12))

        form = tk.Frame(main_frame, bg="#F8FAFC")
        form.pack(fill=tk.BOTH, expand=True)
        form.columnconfigure(1, weight=1)

        # 1. 제목
        tk.Label(form, text="업무 제목 *", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=0, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        self.title_var = tk.StringVar(value=self.task.title if self.task else "")
        title_entry = ttk.Entry(form, textvariable=self.title_var, font=("Malgun Gothic", 10))
        title_entry.grid(row=0, column=1, sticky="ew", pady=6)
        title_entry.focus_set()

        # 2-1. 기간 업무의 시작일 (빈칸이면 마감 기한만 있는 업무)
        tk.Label(form, text="시작일 (기간일 때)", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=1, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        self.start_date_var = tk.StringVar(value=self.task.start_date if self.task else "")
        ttk.Entry(form, textvariable=self.start_date_var, width=13, font=("Malgun Gothic", 10)).grid(
            row=1, column=1, sticky="w", pady=6
        )

        # 2. 마감 기한 (날짜 + 시각 + 빠른 선택 버튼)
        tk.Label(form, text="마감 기한 *", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=2, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        due_frame = tk.Frame(form, bg="#F8FAFC")
        due_frame.grid(row=2, column=1, sticky="w", pady=6)

        initial_date = (
            self.task.due_date
            if (self.task and self.task.due_date)
            else (default_date or date.today().strftime("%Y-%m-%d"))
        )
        initial_time = self.task.due_time if (self.task and self.task.due_time) else "18:00"

        self.due_date_var = tk.StringVar(value=initial_date)
        self.due_time_var = tk.StringVar(value=initial_time)

        ttk.Entry(due_frame, textvariable=self.due_date_var, width=13, font=("Malgun Gothic", 10)).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        tk.Label(due_frame, text="시각:", bg="#F8FAFC", font=("Malgun Gothic", 9)).pack(
            side=tk.LEFT, padx=(4, 4)
        )
        time_combo = ttk.Combobox(
            due_frame,
            textvariable=self.due_time_var,
            values=["09:00", "10:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00", "20:00"],
            width=7,
            font=("Malgun Gothic", 9),
        )
        time_combo.pack(side=tk.LEFT)

        # 빠른 기한 버튼줄
        quick_date_frame = tk.Frame(form, bg="#F8FAFC")
        quick_date_frame.grid(row=3, column=1, sticky="w", pady=(0, 8))
        for btn_text, delta_days in [("오늘", 0), ("내일", 1), ("+3일", 3), ("이번주 금요일", -1), ("+1주일", 7)]:
            btn = tk.Button(
                quick_date_frame,
                text=btn_text,
                font=("Malgun Gothic", 8),
                bg="#E2E8F0",
                fg="#1E293B",
                relief=tk.FLAT,
                padx=8,
                pady=2,
                command=lambda d=delta_days: self._set_quick_date(d),
            )
            btn.pack(side=tk.LEFT, padx=(0, 5))

        # 3. 색상 라벨 / 카테고리
        tk.Label(form, text="라벨(분류)", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=4, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        self.label_keys = list(self.labels_config.keys())
        self.label_display_map = {
            k: f"{v.get('name', k)} ({k})" for k, v in self.labels_config.items()
        }
        self.rev_label_map = {v: k for k, v in self.label_display_map.items()}
        current_label_key = self.task.label if (self.task and self.task.label in self.label_display_map) else "MANUAL"
        self.label_var = tk.StringVar(value=self.label_display_map.get(current_label_key, ""))

        label_combo = ttk.Combobox(
            form,
            textvariable=self.label_var,
            values=list(self.label_display_map.values()),
            state="readonly",
            font=("Malgun Gothic", 9),
        )
        label_combo.grid(row=4, column=1, sticky="ew", pady=6)

        # 4. 진행 상태 & 중요도
        tk.Label(form, text="진행 상태", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=5, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        status_priority_frame = tk.Frame(form, bg="#F8FAFC")
        status_priority_frame.grid(row=5, column=1, sticky="w", pady=6)

        self.status_map = TaskStatus.ALL_LABELS
        self.rev_status_map = {v: k for k, v in self.status_map.items()}
        cur_status = self.task.status if self.task else TaskStatus.PENDING
        self.status_var = tk.StringVar(value=self.status_map.get(cur_status, "미완료(대기)"))

        ttk.Combobox(
            status_priority_frame,
            textvariable=self.status_var,
            values=list(self.status_map.values()),
            state="readonly",
            width=14,
            font=("Malgun Gothic", 9),
        ).pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(status_priority_frame, text="중요도:", bg="#F8FAFC", font=("Malgun Gothic", 9, "bold")).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self.priority_map = {1: "1 - 긴급/최우선", 2: "2 - 보통", 3: "3 - 낮음"}
        self.rev_priority_map = {v: k for k, v in self.priority_map.items()}
        cur_prio = self.task.priority if self.task else 2
        self.priority_var = tk.StringVar(value=self.priority_map.get(cur_prio, "2 - 보통"))
        ttk.Combobox(
            status_priority_frame,
            textvariable=self.priority_var,
            values=list(self.priority_map.values()),
            state="readonly",
            width=14,
            font=("Malgun Gothic", 9),
        ).pack(side=tk.LEFT)

        # 5. 반복 일정 설정 (구상 2-5 승인 기능)
        tk.Label(form, text="반복 설정", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=6, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        self.recur_map = RecurrenceType.ALL_LABELS
        self.rev_recur_map = {v: k for k, v in self.recur_map.items()}
        cur_recur = self.task.recurrence if self.task else RecurrenceType.NONE
        self.recur_var = tk.StringVar(value=self.recur_map.get(cur_recur, "반복 없음"))
        self.recur_var.trace_add("write", lambda *_a: self._on_recurrence_changed())
        ttk.Combobox(
            form,
            textvariable=self.recur_var,
            values=list(self.recur_map.values()),
            state="readonly",
            font=("Malgun Gothic", 9),
        ).grid(row=6, column=1, sticky="w", pady=6)

        # 6. 출처 / 문서번호 메타정보
        tk.Label(form, text="문서번호/발신", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=7, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        self.source_ref_var = tk.StringVar(value=self.task.source_ref if self.task else "")
        ttk.Entry(form, textvariable=self.source_ref_var, font=("Malgun Gothic", 9)).grid(
            row=7, column=1, sticky="ew", pady=6
        )

        # 7. 상세 내용 / 원본 메시지
        tk.Label(form, text="상세 내용", font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#334155").grid(
            row=8, column=0, sticky="nw", pady=6, padx=(0, 12)
        )
        self.desc_text = tk.Text(
            form,
            height=9,
            font=("Malgun Gothic", 9),
            wrap=tk.WORD,
            relief=tk.SOLID,
            bd=1,
            padx=6,
            pady=6,
        )
        self.desc_text.grid(row=8, column=1, sticky="nsew", pady=6)
        form.rowconfigure(8, weight=1)
        if self.task and self.task.description:
            self.desc_text.insert("1.0", self.task.description)

        # 하단 버튼 영역
        btn_bar = tk.Frame(main_frame, bg="#F8FAFC", pady=10)
        btn_bar.pack(fill=tk.X)

        if is_edit:
            tk.Button(
                btn_bar,
                text="🗑️ 삭제",
                font=("Malgun Gothic", 9, "bold"),
                bg="#FEE2E2",
                fg="#DC2626",
                relief=tk.FLAT,
                padx=12,
                pady=6,
                command=self._on_delete,
            ).pack(side=tk.LEFT)

        tk.Button(
            btn_bar,
            text="취소",
            font=("Malgun Gothic", 9),
            bg="#E2E8F0",
            fg="#334155",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            command=self.destroy,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        tk.Button(
            btn_bar,
            text="💾 저장하기",
            font=("Malgun Gothic", 10, "bold"),
            bg="#2563EB",
            fg="white",
            relief=tk.FLAT,
            padx=18,
            pady=6,
            command=self._on_save,
        ).pack(side=tk.RIGHT)

    def _set_quick_date(self, delta_days: int) -> None:
        today = date.today()
        if delta_days == -1:
            # 이번주 금요일
            diff = (4 - today.weekday()) % 7
            target = today + timedelta(days=diff)
        else:
            target = today + timedelta(days=delta_days)
        self.due_date_var.set(target.strftime("%Y-%m-%d"))

    def _on_save(self) -> None:
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("입력 확인", "업무 제목을 입력해 주세요.", parent=self)
            return

        due_date = normalize_date_input(self.due_date_var.get())
        if due_date is None:
            messagebox.showwarning("입력 확인", "마감 기한을 YYYY-MM-DD 형식으로 입력해 주세요. (예: 2026-11-20)", parent=self)
            return
        due_time = normalize_time_input(self.due_time_var.get())
        if due_time is None:
            messagebox.showwarning("입력 확인", "마감 시각을 HH:MM 형식으로 입력해 주세요. (예: 15:00)", parent=self)
            return
        start_date = ""
        if self.start_date_var.get().strip():
            start_date = normalize_date_input(self.start_date_var.get())
            if start_date is None or start_date > due_date:
                messagebox.showwarning(
                    "입력 확인", "시작일은 YYYY-MM-DD 형식이고 마감일보다 늦지 않아야 합니다.", parent=self
                )
                return
        label_key = self.rev_label_map.get(self.label_var.get(), "MANUAL")
        status_key = self.rev_status_map.get(self.status_var.get(), TaskStatus.PENDING)
        priority_val = self.rev_priority_map.get(self.priority_var.get(), 2)
        recur_key = self.rev_recur_map.get(self.recur_var.get(), RecurrenceType.NONE)
        source_ref = self.source_ref_var.get().strip()
        description = self.desc_text.get("1.0", tk.END).strip()

        if self.task and self.task.id is not None:
            fresh = self.db.get_task(self.task.id)
            if fresh is None:
                messagebox.showwarning("저장 안 됨", "그 사이 업무가 삭제되어 저장할 수 없습니다.", parent=self)
                if self.on_saved:
                    self.on_saved()
                self.destroy()
                return
            old_status = fresh.status
            self.task = apply_edit(fresh, self.task, {
                "title": title,
                "start_date": start_date,  # 마감일과 같으면 DB가 ''로 저장
                "due_date": due_date,
                "due_time": due_time,
                "label": label_key,
                "priority": priority_val,
                "recurrence": recur_key,
                "source_ref": source_ref,
                "description": description,
            })
            self.db.update_task(self.task)
            if old_status != status_key:
                self.db.set_task_status(self.task.id, status_key)
        else:
            source_map = {
                "PORTAL": TaskSource.PORTAL,
                "MESSENGER": TaskSource.MESSENGER,
            }
            new_item = TaskItem(
                title=title,
                description=description,
                source=source_map.get(label_key, TaskSource.MANUAL),
                source_ref=source_ref,
                start_date=start_date,
                due_date=due_date,
                due_time=due_time,
                status=status_key,
                label=label_key,
                priority=priority_val,
                recurrence=recur_key,
                is_new_scan=False,
            )
            self.db.add_task(new_item, skip_if_duplicate=False)

        if self.on_saved:
            self.on_saved()
        self.destroy()

    def _on_recurrence_changed(self) -> None:
        """반복을 고르면 라벨이 기본값(직접/수동등록)일 때 '정기/반복업무'로 바꿈 (다시 바꾸면 그대로 둠)."""
        recur_key = self.rev_recur_map.get(self.recur_var.get(), RecurrenceType.NONE)
        label_key = self.rev_label_map.get(self.label_var.get(), "MANUAL")
        new_label = label_for_recurrence(label_key, recur_key)
        if new_label != label_key and new_label in self.label_display_map:
            self.label_var.set(self.label_display_map[new_label])

    def _on_delete(self) -> None:
        if not self.task or self.task.id is None:
            return
        if messagebox.askyesno("삭제 확인", f"'{self.task.title}' 일정을 삭제하시겠습니까?", parent=self):
            self.db.delete_task(self.task.id)
            if self.on_saved:
                self.on_saved()
            self.destroy()


class QuickPasteScanDialog(tk.Toplevel):
    """사내포털 문서나 메신저 대화 내용을 붙여넣어 즉시 자동 파싱·등록하는 모달 창."""

    def __init__(
        self,
        parent: tk.Widget,
        clipboard_collector: ClipboardCollector,
        on_registered: Optional[Callable[[int], None]] = None,
    ):
        super().__init__(parent)
        self.clipboard_collector = clipboard_collector
        self.on_registered = on_registered

        self.title("사내포털 문서 / 메신저 텍스트 붙여넣기 자동 등록")
        self.geometry("640x480")
        self.configure(bg="#F8FAFC")
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._center_window()

    def _center_window(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_ui(self) -> None:
        frame = tk.Frame(self, bg="#F8FAFC", padx=18, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="📋 사내포털 배부 문서 또는 메신저 메시지 텍스트 자동 스캔",
            font=("Malgun Gothic", 12, "bold"),
            bg="#F8FAFC",
            fg="#1E293B",
        ).pack(anchor="w")

        tk.Label(
            frame,
            text="사내포털 화면이나 메신저에서 복사한 내용을 아래에 붙여넣으세요. 여러 건도 한 번에 기한/제목을 추출합니다.",
            font=("Malgun Gothic", 9),
            bg="#F8FAFC",
            fg="#475569",
        ).pack(anchor="w", pady=(4, 10))

        self.text_area = tk.Text(
            frame,
            font=("Malgun Gothic", 9),
            wrap=tk.WORD,
            relief=tk.SOLID,
            bd=1,
            padx=8,
            pady=8,
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)

        # 클립보드에 텍스트가 있으면 자동으로 채워넣기
        try:
            clip = self.clipboard_get()
            if clip and clip.strip():
                self.text_area.insert("1.0", clip.strip())
        except Exception:
            pass

        btn_bar = tk.Frame(frame, bg="#F8FAFC", pady=10)
        btn_bar.pack(fill=tk.X)

        tk.Button(
            btn_bar,
            text="닫기",
            font=("Malgun Gothic", 9),
            bg="#E2E8F0",
            fg="#334155",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            command=self.destroy,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        tk.Button(
            btn_bar,
            text="⚡ 기한 자동 추출 및 캘린더 등록",
            font=("Malgun Gothic", 10, "bold"),
            bg="#16A34A",
            fg="white",
            relief=tk.FLAT,
            padx=16,
            pady=6,
            command=self._on_extract_and_save,
        ).pack(side=tk.RIGHT)

    def _on_extract_and_save(self) -> None:
        raw = self.text_area.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("입력 필요", "스캔할 텍스트를 입력하거나 붙여넣어 주세요.", parent=self)
            return

        created = self.clipboard_collector.parse_and_register_text(raw, source_ref="수동 텍스트 스캔")
        if created:
            messagebox.showinfo(
                "자동 등록 완료",
                f"총 {len(created)}건의 업무가 캘린더에 자동 등록되었습니다.\n"
                + "\n".join(f"• [{t.due_date}] {t.title}" for t in created[:5]),
                parent=self,
            )
            if self.on_registered:
                self.on_registered(len(created))
            self.destroy()
        else:
            messagebox.showinfo(
                "스캔 결과 안내",
                "추출된 신규 업무가 없거나 이미 동일하게 등록된 업무입니다.",
                parent=self,
            )


class ImportCheckDialog(tk.Toplevel):
    """파일 점검 결과 창: 등록하지 않고 인식 결과만 보여주며 공유용 요약을 복사할 수 있음."""

    def __init__(self, parent: tk.Widget, full_report: str, share_report: str):
        super().__init__(parent)
        self.share_report = share_report
        self.title("파일 점검 결과 (등록하지 않음)")
        self.geometry("760x560")
        self.configure(bg="#F8FAFC")
        self.transient(parent)

        frame = tk.Frame(self, bg="#F8FAFC", padx=16, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            frame,
            text="파일을 어떻게 읽었는지 보여줍니다. 글자 깨짐, 기한, 문서번호가 맞는지 확인하세요.",
            font=("Malgun Gothic", 9),
            bg="#F8FAFC",
            fg="#475569",
        ).pack(anchor="w", pady=(0, 8))

        text = tk.Text(frame, font=("Malgun Gothic", 9), wrap=tk.WORD, relief=tk.SOLID, bd=1, padx=8, pady=8)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        btn_bar = tk.Frame(frame, bg="#F8FAFC", pady=10)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(
            "1.0",
            full_report + "\n\n===== 공유용 요약 (파일 이름·제목·본문 없음) =====\n\n" + share_report,
        )
        text.configure(state=tk.DISABLED)

        tk.Button(
            btn_bar,
            text="닫기",
            font=("Malgun Gothic", 9),
            bg="#E2E8F0",
            fg="#334155",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            command=self.destroy,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(
            btn_bar,
            text="📋 공유용 요약 복사",
            font=("Malgun Gothic", 10, "bold"),
            bg="#4F46E5",
            fg="white",
            relief=tk.FLAT,
            padx=16,
            pady=6,
            command=self._copy_share,
        ).pack(side=tk.RIGHT)

    def _copy_share(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.share_report)
        messagebox.showinfo("복사 완료", "공유용 요약을 클립보드에 복사했습니다.", parent=self)
