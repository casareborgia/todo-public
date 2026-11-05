"""감시 폴더 설정 창과 파일 불러오기 결과 목록 창."""

from datetime import date, timedelta
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

from collectors.folder_watcher import FileScanResult, WatchFolder

FILTER_WORK_ONLY = "쪽지·공문만"
FILTER_ALL = "모든 지원 파일"

SINCE_TODAY = "오늘부터 저장된 파일"
SINCE_7_DAYS = "최근 7일"
SINCE_30_DAYS = "최근 30일"
SINCE_ALL = "전체 파일"
SINCE_CHOICES = {SINCE_TODAY: 0, SINCE_7_DAYS: 7, SINCE_30_DAYS: 30, SINCE_ALL: None}


def _since_label(since: str) -> str:
    return f"{since} 이후" if since else "전체"


def _same_folder(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class FolderSettingsDialog(tk.Toplevel):
    """감시 폴더 추가·삭제와 폴더별 옵션(받을 파일, 가져올 범위) 설정."""

    def __init__(
        self,
        parent: tk.Widget,
        folders: List[WatchFolder],
        on_save: Callable[[List[WatchFolder]], None],
    ):
        super().__init__(parent)
        self.folders = [WatchFolder(f.path, f.work_files_only, f.since) for f in folders]
        self.on_save = on_save
        self.title("감시 폴더 설정")
        self.geometry("760x470")
        self.configure(bg="#F8FAFC")
        self.transient(parent)
        self.grab_set()
        self._build_ui()
        self._refresh_tree()

    def _build_ui(self) -> None:
        frame = tk.Frame(self, bg="#F8FAFC", padx=16, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            frame,
            text="메신저 쪽지 저장 폴더나 공문 PDF 다운로드 폴더를 추가하면, 파일을 저장하는 즉시 일정에 자동 등록됩니다.",
            font=("Malgun Gothic", 9),
            bg="#F8FAFC",
            fg="#475569",
            wraplength=720,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 8))

        btn_bar = tk.Frame(frame, bg="#F8FAFC", pady=10)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X)

        option_box = tk.LabelFrame(
            frame, text=" 선택한 폴더 옵션 ", font=("Malgun Gothic", 9, "bold"), bg="#F8FAFC", padx=10, pady=8
        )
        option_box.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))

        self.tree = ttk.Treeview(frame, columns=("path", "filter", "since"), show="headings", height=7)
        self.tree.heading("path", text="폴더")
        self.tree.heading("filter", text="받을 파일")
        self.tree.heading("since", text="가져올 범위")
        self.tree.column("path", width=430)
        self.tree.column("filter", width=110, anchor="center")
        self.tree.column("since", width=140, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._load_options())

        tk.Label(option_box, text="받을 파일:", font=("Malgun Gothic", 9), bg="#F8FAFC").grid(row=0, column=0, sticky="w")
        self.filter_var = tk.StringVar(value=FILTER_WORK_ONLY)
        for col, (value, hint) in enumerate(
            ((FILTER_WORK_ONLY, " (메신저 쪽지 txt·공문 PDF, 다운로드 폴더에 권장)"), (FILTER_ALL, ""))
        ):
            tk.Radiobutton(
                option_box,
                text=value + hint,
                value=value,
                variable=self.filter_var,
                command=self._store_options,
                font=("Malgun Gothic", 9),
                bg="#F8FAFC",
            ).grid(row=0, column=1 + col, sticky="w", padx=(6, 0))

        tk.Label(option_box, text="가져올 범위:", font=("Malgun Gothic", 9), bg="#F8FAFC").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self.since_var = tk.StringVar(value=SINCE_TODAY)
        since_cb = ttk.Combobox(
            option_box, textvariable=self.since_var, values=list(SINCE_CHOICES), state="readonly", width=22
        )
        since_cb.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        since_cb.bind("<<ComboboxSelected>>", lambda _e: self._store_options())
        tk.Label(
            option_box,
            text="폴더에 예전 파일이 많으면 '오늘부터'로 두세요. 지난 쪽지가 한꺼번에 등록되지 않습니다.",
            font=("Malgun Gothic", 8),
            bg="#F8FAFC",
            fg="#64748B",
        ).grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(6, 0))

        for text, bg, fg, cmd, side in (
            ("취소", "#E2E8F0", "#334155", self.destroy, tk.RIGHT),
            ("💾 저장하고 바로 확인", "#2563EB", "white", self._save, tk.RIGHT),
            ("＋ 폴더 추가", "#16A34A", "white", self._add_folder, tk.LEFT),
            ("－ 선택 폴더 빼기", "#E2E8F0", "#334155", self._remove_folder, tk.LEFT),
        ):
            tk.Button(
                btn_bar,
                text=text,
                font=("Malgun Gothic", 9, "bold"),
                bg=bg,
                fg=fg,
                relief=tk.FLAT,
                padx=12,
                pady=6,
                command=cmd,
            ).pack(side=side, padx=(0, 8) if side == tk.LEFT else (8, 0))

    def _refresh_tree(self, select: Optional[int] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(self.folders):
            self.tree.insert(
                "", tk.END, iid=str(i),
                values=(f.path, FILTER_WORK_ONLY if f.work_files_only else FILTER_ALL, _since_label(f.since)),
            )
        if select is not None and self.folders:
            self.tree.selection_set(str(min(select, len(self.folders) - 1)))
            self._load_options()

    def _selected_index(self) -> Optional[int]:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _load_options(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        f = self.folders[idx]
        self.filter_var.set(FILTER_WORK_ONLY if f.work_files_only else FILTER_ALL)
        self.since_var.set(_since_label(f.since) if f.since else SINCE_ALL)

    def _store_options(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        f = self.folders[idx]
        f.work_files_only = self.filter_var.get() == FILTER_WORK_ONLY
        days = SINCE_CHOICES.get(self.since_var.get(), "keep")
        if days != "keep":  # 콤보에 'YYYY-MM-DD 이후'처럼 기존 값이 표시된 경우는 그대로 둠
            f.since = "" if days is None else (date.today() - timedelta(days=days)).isoformat()
        self._refresh_tree(select=idx)

    def _add_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="감시할 폴더 선택 (메신저 쪽지 저장 폴더, PDF 다운로드 폴더 등)")
        if not path:
            return
        path = os.path.normpath(path)
        if any(_same_folder(path, f.path) for f in self.folders):
            messagebox.showinfo("감시 폴더", "이미 추가된 폴더입니다.", parent=self)
            return
        # 새 폴더 기본값: 업무 파일만, 오늘부터 저장된 파일만 (예전 파일이 한꺼번에 쏟아지지 않게)
        self.folders.append(WatchFolder(path, work_files_only=True, since=date.today().isoformat()))
        self._refresh_tree(select=len(self.folders) - 1)

    def _remove_folder(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        if len(self.folders) == 1:
            messagebox.showwarning("감시 폴더", "감시 폴더는 하나 이상 있어야 합니다.", parent=self)
            return
        del self.folders[idx]
        self._refresh_tree(select=idx)

    def _save(self) -> None:
        self.on_save(self.folders)
        self.destroy()


class ImportResultDialog(tk.Toplevel):
    """여러 파일을 불러온 뒤 파일마다 무엇이 되었는지(등록·갱신·업무 아님·제외) 보여주는 창."""

    def __init__(self, parent: tk.Widget, title: str, results: List[FileScanResult], show_excluded: bool = True):
        super().__init__(parent)
        self.title(title)
        self.geometry("860x480")
        self.configure(bg="#F8FAFC")
        self.transient(parent)

        shown = [r for r in results if show_excluded or not r.excluded]
        hidden = len(results) - len(shown)
        created = sum(len(r.created) for r in results)
        frame = tk.Frame(self, bg="#F8FAFC", padx=16, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)
        summary = f"파일 {len(results)}개 확인 · 새 업무 {created}건 등록"
        if hidden:
            summary += f" · 쪽지·공문이 아니거나 이미 확인한 파일 {hidden}개는 목록에서 생략"
        tk.Label(frame, text=summary, font=("Malgun Gothic", 10, "bold"), bg="#F8FAFC", fg="#1E293B").pack(
            anchor="w", pady=(0, 8)
        )

        btn_bar = tk.Frame(frame, bg="#F8FAFC", pady=10)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X)
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
        ).pack(side=tk.RIGHT)

        tree = ttk.Treeview(frame, columns=("file", "outcome", "task"), show="headings")
        tree.heading("file", text="파일")
        tree.heading("outcome", text="결과")
        tree.heading("task", text="업무 (기한)")
        tree.column("file", width=260)
        tree.column("outcome", width=160)
        tree.column("task", width=400)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)

        for r in shown:
            name = os.path.basename(r.path)
            if not r.items:
                tree.insert("", tk.END, values=(name, r.outcome, ""))
            for i, t in enumerate(r.items):
                due = f"{t.due_date} {t.due_time}" + (" 추정" if t.due_estimated else "")
                tree.insert("", tk.END, values=(name if i == 0 else "", r.outcome if i == 0 else "", f"{t.title} ({due})"))
        if not shown:
            tree.insert("", tk.END, values=("", "새로 확인할 파일이 없습니다", ""))
