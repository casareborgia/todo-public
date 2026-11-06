"""쪽지·문서 판정 기록 창(제외된 쪽지·업무관리시스템 문서 → 업무로 등록)과 확인 필요 정리 창(업무 맞음 / 업무 아님).

목표가 '업무 누락 0건'이라, 판정이 제외한 쪽지·문서를 사용자가 훑어보고 놓친 업무를 건질 수 있게 함.
'업무로 등록'·'업무 아님' 표시는 판정 기록(ezq_messages·업무관리시스템 문서 기록의 user_label)에 남아
점수표를 맞출 정답 표본이 됨.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List

from collectors.ezq_collector import EzqMessageCollector, ezq_key_of

FONT = "Malgun Gothic"
BG = "#F8FAFC"

ORIGIN_EZQ = "쪽지"
ORIGIN_DOC = "문서"

# (보기 이름, list_ezq_messages·list_workmgmt_judgments 인자)
VIEWS = [
    ("제외된 쪽지·문서", {"verdict": "제외", "user_label": ""}),
    ("'업무 아님'으로 표시한 쪽지·문서", {"user_label": "업무아님"}),
    ("전체 판정 기록", {}),
]


def first_line(body: str, width: int = 60) -> str:
    """목록에 보일 쪽지 요지: 비어 있지 않은 첫 줄."""
    line = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
    return line if len(line) <= width else line[: width - 1] + "…"


def merged_records(ezq_rows: List[dict], doc_rows: List[dict]) -> List[dict]:
    """쪽지·문서 판정 기록을 받은 시각 내림차순으로 합침. 키가 같을 수 있어 표 iid는 출처를 붙인 row_id."""
    tagged = [
        {**rec, "origin": origin, "row_id": f"{origin}:{rec['msg_key']}"}
        for origin, rows in ((ORIGIN_EZQ, ezq_rows), (ORIGIN_DOC, doc_rows))
        for rec in rows
    ]
    return sorted(tagged, key=lambda r: r.get("first_seen", ""), reverse=True)


def row_values(rec: dict) -> tuple:
    verdict = rec.get("verdict") or "-"
    if rec.get("user_label"):
        verdict += f" → {rec['user_label']}"
    return (
        rec.get("first_seen", "")[5:16],
        rec.get("origin") or ORIGIN_EZQ,
        verdict,
        rec.get("score", 0),
        first_line(rec.get("body", "")),
    )


def can_register(rec: dict, task_exists: bool) -> bool:
    """'업무로 등록' 가능 여부: 지금 그 쪽지에서 나온 업무가 없으면 판정·표시와 상관없이 가능.

    판정이 제외한 쪽지, '업무 아님'으로 지운 쪽지, 실수로 일반 삭제한 등록 업무 모두 되살릴 수 있게 함.
    """
    return not task_exists


class EzqLogDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        collector: EzqMessageCollector,
        on_registered: Callable[[list], None],
        workmgmt_collector=None,
    ):
        super().__init__(parent)
        self.title("쪽지·문서 판정 기록")
        self.geometry("880x620")
        self.configure(bg=BG)
        self.transient(parent)
        self.collector = collector
        self.on_registered = on_registered
        self.workmgmt_collector = workmgmt_collector  # 없으면 쪽지 행만 보여줌
        self._rows: Dict[str, dict] = {}
        self._build()
        self.reload()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _build(self) -> None:
        frame = tk.Frame(self, bg=BG, padx=16, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        head = tk.Frame(frame, bg=BG)
        head.pack(fill=tk.X)
        tk.Label(head, text="🧾 쪽지·문서 판정 기록", font=(FONT, 13, "bold"), bg=BG, fg="#0F172A").pack(side=tk.LEFT)
        self.view_var = tk.StringVar(value=VIEWS[0][0])
        view = ttk.Combobox(
            head, textvariable=self.view_var, values=[v[0] for v in VIEWS], state="readonly", width=24, font=(FONT, 9)
        )
        view.pack(side=tk.RIGHT)
        view.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        tk.Label(
            frame,
            text="업무가 아니라고 판단해 등록하지 않은 쪽지와 업무관리시스템 문서입니다. 놓친 업무가 있으면 골라서 '업무로 등록'을 누르세요."
            " ('전체 판정 기록'에서 실수로 지운 업무도 되살릴 수 있습니다.)",
            font=(FONT, 8),
            bg=BG,
            fg="#64748B",
        ).pack(anchor="w", pady=(2, 8))

        btn_bar = tk.Frame(frame, bg=BG, pady=8)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Button(
            btn_bar, text="닫기", font=(FONT, 9), bg="#E2E8F0", fg="#334155", relief=tk.FLAT, padx=14, pady=6,
            command=self.destroy,
        ).pack(side=tk.RIGHT)
        self.register_btn = tk.Button(
            btn_bar, text="📥 업무로 등록", font=(FONT, 9, "bold"), bg="#2563EB", fg="white", relief=tk.FLAT,
            padx=14, pady=6, cursor="hand2", command=self.register_selected, state=tk.DISABLED,
        )
        self.register_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.result_lbl = tk.Label(btn_bar, text="", font=(FONT, 9), bg=BG, fg="#166534")
        self.result_lbl.pack(side=tk.LEFT)

        pane = tk.PanedWindow(frame, orient=tk.VERTICAL, bg=BG, sashwidth=6)
        pane.pack(fill=tk.BOTH, expand=True)
        tree_box = tk.Frame(pane, bg=BG)
        self.tree = ttk.Treeview(tree_box, columns=("seen", "origin", "verdict", "score", "text"), show="headings", height=8)
        for col, text, width, anchor in (
            ("seen", "받은 시각", 90, tk.CENTER),
            ("origin", "출처", 50, tk.CENTER),
            ("verdict", "판정", 120, tk.CENTER),
            ("score", "점수", 50, tk.CENTER),
            ("text", "요지", 510, tk.W),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "text"))
        scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        self.tree.bind("<Double-1>", lambda _e: self.register_selected())
        pane.add(tree_box, minsize=120)

        detail_box = tk.Frame(pane, bg=BG)
        self.reason_lbl = tk.Label(
            detail_box, text="", font=(FONT, 9, "bold"), bg=BG, fg="#334155", justify=tk.LEFT, anchor="w", wraplength=820
        )
        self.reason_lbl.pack(fill=tk.X, pady=(6, 4))
        self.body_text = tk.Text(detail_box, font=(FONT, 9), height=12, wrap=tk.WORD, relief=tk.FLAT, bg="white")
        self.body_text.pack(fill=tk.BOTH, expand=True)
        self.body_text.config(state=tk.DISABLED)
        pane.add(detail_box, minsize=100)

    def _view_args(self) -> dict:
        return next(args for name, args in VIEWS if name == self.view_var.get())

    def reload(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._rows.clear()
        args = self._view_args()
        ezq_rows = self.collector.db.list_ezq_messages(**args)
        doc_rows = self.collector.db.list_workmgmt_judgments(**args) if self.workmgmt_collector else []
        records = merged_records(ezq_rows, doc_rows)
        for rec in records:
            self._rows[rec["row_id"]] = rec
            self.tree.insert("", tk.END, iid=rec["row_id"], values=row_values(rec))
        if not records:
            self._set_detail("", "표시할 쪽지·문서가 없습니다.")
        else:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self._show_selected()

    def _selected(self):
        sel = self.tree.selection()
        return self._rows.get(sel[0]) if sel else None

    def _set_detail(self, reasons: str, body: str) -> None:
        self.reason_lbl.config(text=reasons)
        self.body_text.config(state=tk.NORMAL)
        self.body_text.delete("1.0", tk.END)
        self.body_text.insert("1.0", body)
        self.body_text.config(state=tk.DISABLED)

    def _show_selected(self) -> None:
        rec = self._selected()
        if not rec:
            self.register_btn.config(state=tk.DISABLED)
            return
        reasons = ", ".join(rec["reasons"]) or "근거 없음 (업무 표현이 없음)"
        self._set_detail(f"판정 근거: {reasons}", rec.get("body", ""))
        self.register_btn.config(state=tk.NORMAL if self._can_register(rec) else tk.DISABLED)

    def _owner(self, rec: dict):
        """그 행을 처리할 수집기: 문서 행은 업무관리시스템 수집기, 쪽지 행은 쪽지 수집기."""
        return self.workmgmt_collector if rec.get("origin") == ORIGIN_DOC else self.collector

    def _can_register(self, rec: dict) -> bool:
        return can_register(rec, self._owner(rec).has_task(rec["msg_key"]))

    def register_selected(self) -> None:
        rec = self._selected()
        if not rec or not self._can_register(rec):
            return
        owner = self._owner(rec)
        created = owner.register_as_task(rec["msg_key"])
        if created:
            self.result_lbl.config(text=f"✅ '{created[0].title}' 등록 ({created[0].due_date} 마감)", fg="#166534")
            self.on_registered(created)
        elif owner is not self.collector and owner.has_task(rec["msg_key"]):
            # 배부 목록 업무·쪽지 업무와 이어져 새 업무 없이 연결만 됨
            self.result_lbl.config(text="✅ 기존 업무에 연결했습니다", fg="#166534")
        else:
            self.result_lbl.config(text="업무로 만들 내용이 없습니다 (본문이 비었거나 이미 등록됨).", fg="#B45309")
        self.reload()


def review_handler(task, ezq_collector, workmgmt_collector):
    """확인 필요 업무를 정리할 수집기: 배부 업무·판정 문서 업무면 업무관리시스템 수집기, 아니면 쪽지 수집기.

    배부 기록과 문서 판정에 함께 이어진 업무는 배부 쪽으로 (is_list_task 먼저).
    """
    if workmgmt_collector is not None and (
        workmgmt_collector.is_list_task(task) or workmgmt_collector.is_doc_task(task)
    ):
        return workmgmt_collector
    return ezq_collector


def confirm_handler(task, ezq_collector, workmgmt_collector):
    """'업무 맞음' 처리 수집기: 배부 업무는 쪽지 수집기, 판정 문서 업무만 업무관리시스템 수집기."""
    wm = workmgmt_collector
    if wm is not None and not wm.is_list_task(task) and wm.is_doc_task(task):
        return wm
    return ezq_collector


class EzqReviewDialog(tk.Toplevel):
    """'확인 필요' 정리 창: 업무인지 애매한 쪽지·문서를 '업무 맞음 / 업무 아님'으로 정리.

    정리하기 전까지 이 업무들은 캘린더·알림·브리핑에 나오지 않음.
    """

    def __init__(
        self, parent: tk.Widget, collector: EzqMessageCollector, on_changed: Callable[[], None], workmgmt_collector=None
    ):
        super().__init__(parent)
        self.title("확인 필요")
        self.geometry("880x600")
        self.configure(bg=BG)
        self.transient(parent)
        self.collector = collector
        self.on_changed = on_changed
        self.workmgmt_collector = workmgmt_collector
        self._tasks: Dict[str, object] = {}
        self._build()
        self.reload()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _build(self) -> None:
        frame = tk.Frame(self, bg=BG, padx=16, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(frame, text="❔ 확인 필요", font=(FONT, 13, "bold"), bg=BG, fg="#0F172A").pack(anchor="w")
        tk.Label(
            frame,
            text="업무인지 애매한 쪽지·문서(기한·첨부·공문 없는 요청)와 업무관리자로 지정된 배부 문서입니다. 정리하기 전까지 캘린더·알림에 나오지 않습니다.",
            font=(FONT, 8),
            bg=BG,
            fg="#64748B",
        ).pack(anchor="w", pady=(2, 8))

        btn_bar = tk.Frame(frame, bg=BG, pady=8)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Button(
            btn_bar, text="닫기", font=(FONT, 9), bg="#E2E8F0", fg="#334155", relief=tk.FLAT, padx=14, pady=6,
            command=self.destroy,
        ).pack(side=tk.RIGHT)
        self.no_btn = tk.Button(
            btn_bar, text="🚫 업무 아님", font=(FONT, 9, "bold"), bg="#B45309", fg="white", relief=tk.FLAT,
            padx=14, pady=6, cursor="hand2", command=self.dismiss_selected,
        )
        self.no_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.yes_btn = tk.Button(
            btn_bar, text="✅ 업무 맞음 (캘린더에 등록)", font=(FONT, 9, "bold"), bg="#16A34A", fg="white",
            relief=tk.FLAT, padx=14, pady=6, cursor="hand2", command=self.confirm_selected,
        )
        self.yes_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.result_lbl = tk.Label(btn_bar, text="", font=(FONT, 9), bg=BG, fg="#166534")
        self.result_lbl.pack(side=tk.LEFT)

        pane = tk.PanedWindow(frame, orient=tk.VERTICAL, bg=BG, sashwidth=6)
        pane.pack(fill=tk.BOTH, expand=True)
        tree_box = tk.Frame(pane, bg=BG)
        self.tree = ttk.Treeview(tree_box, columns=("due", "title"), show="headings", height=8)
        self.tree.heading("due", text="기한(추정)")
        self.tree.heading("title", text="업무 제목")
        self.tree.column("due", width=130, anchor=tk.CENTER, stretch=False)
        self.tree.column("title", width=680, anchor=tk.W)
        scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        pane.add(tree_box, minsize=120)

        detail_box = tk.Frame(pane, bg=BG)
        self.reason_lbl = tk.Label(
            detail_box, text="", font=(FONT, 9, "bold"), bg=BG, fg="#334155", justify=tk.LEFT, anchor="w", wraplength=820
        )
        self.reason_lbl.pack(fill=tk.X, pady=(6, 4))
        self.body_text = tk.Text(detail_box, font=(FONT, 9), height=12, wrap=tk.WORD, relief=tk.FLAT, bg="white")
        self.body_text.pack(fill=tk.BOTH, expand=True)
        self.body_text.config(state=tk.DISABLED)
        pane.add(detail_box, minsize=100)

    def reload(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._tasks.clear()
        tasks = [t for t in self.collector.db.query_tasks(status_filter="INCOMPLETE", needs_review=True)]
        for t in tasks:
            iid = str(t.id)
            self._tasks[iid] = t
            self.tree.insert("", tk.END, iid=iid, values=(f"{t.due_date} {t.due_time}", t.title))
        has = bool(tasks)
        for btn in (self.yes_btn, self.no_btn):
            btn.config(state=tk.NORMAL if has else tk.DISABLED)
        if has:
            self.tree.selection_set(self.tree.get_children()[0])
            self._show_selected()
        else:
            self._set_detail("", "정리할 쪽지·문서가 없습니다.")

    def _selected(self):
        sel = self.tree.selection()
        return self._tasks.get(sel[0]) if sel else None

    def _set_detail(self, reasons: str, body: str) -> None:
        self.reason_lbl.config(text=reasons)
        self.body_text.config(state=tk.NORMAL)
        self.body_text.delete("1.0", tk.END)
        self.body_text.insert("1.0", body)
        self.body_text.config(state=tk.DISABLED)

    def _show_selected(self) -> None:
        task = self._selected()
        if not task:
            return
        handler = review_handler(task, self.collector, self.workmgmt_collector)
        if handler is not self.collector:
            self._set_detail(f"판정 근거: {handler.review_reason(task)}", task.description)
            return
        key = ezq_key_of(task)
        rec = self.collector.db.get_ezq_message(key) if key else None
        reasons = ", ".join(rec["reasons"]) if rec else ""
        self._set_detail(f"판정 근거: {reasons}" if reasons else "", task.description)

    def _done(self, text: str, fg: str) -> None:
        self.result_lbl.config(text=text, fg=fg)
        self.on_changed()
        self.reload()

    def confirm_selected(self) -> None:
        task = self._selected()
        if task and confirm_handler(task, self.collector, self.workmgmt_collector).confirm_review(task):
            self._done(f"✅ '{task.title}' 캘린더에 등록 ({task.due_date} 마감)", "#166534")

    def dismiss_selected(self) -> None:
        task = self._selected()
        if task and review_handler(task, self.collector, self.workmgmt_collector).dismiss_task(task):
            self._done(f"🚫 '{task.title}' 업무 아님으로 정리", "#B45309")
