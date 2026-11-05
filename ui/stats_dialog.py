"""처리 실적 요약 창: 이번 주·이번 달 등록·완료 건수, 기한 내 처리율, 최근 4주 완료 추이."""

import tkinter as tk
from datetime import date
from typing import Dict

FONT = "Malgun Gothic"
BG = "#F8FAFC"


def rate_text(on_time: int, completed: int) -> str:
    """기한 내 처리율. 완료가 없으면 '-'."""
    if completed <= 0:
        return "-"
    return f"{round(on_time * 100 / completed)}%"


def _md(d: date) -> str:
    return f"{d.month}.{d.day}"


class WorkStatsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, stats: Dict[str, object], ref_date: date):
        super().__init__(parent)
        self.title("처리 실적 요약")
        self.configure(bg=BG)
        self.transient(parent)
        self._build(stats, ref_date)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _build(self, s: Dict[str, object], ref_date: date) -> None:
        frame = tk.Frame(self, bg=BG, padx=18, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame, text="📊 처리 실적 요약", font=(FONT, 14, "bold"), bg=BG, fg="#0F172A"
        ).pack(anchor="w")
        tk.Label(
            frame,
            text=f"기준일 {ref_date.isoformat()} · 완료한 날짜가 마감일 이전이면 '기한 내 처리'",
            font=(FONT, 8),
            bg=BG,
            fg="#64748B",
        ).pack(anchor="w", pady=(0, 10))

        # 1) 지금 상태 타일
        tiles = tk.Frame(frame, bg=BG)
        tiles.pack(fill=tk.X)
        for i, (title, value, fg, tile_bg) in enumerate(
            [
                ("미완료 업무", s["open"], "#1E293B", "#FFFFFF"),
                ("🚨 기한경과", s["overdue"], "#B91C1C", "#FEF2F2"),
                ("📌 오늘 마감", s["due_today"], "#1D4ED8", "#EFF6FF"),
            ]
        ):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            box = tk.Frame(tiles, bg=tile_bg, highlightbackground="#E2E8F0", highlightthickness=1, padx=10, pady=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            tk.Label(box, text=title, font=(FONT, 9), bg=tile_bg, fg="#475569").pack(anchor="w")
            tk.Label(box, text=f"{value}건", font=(FONT, 16, "bold"), bg=tile_bg, fg=fg).pack(anchor="w")

        # 2) 기간별 표
        table = tk.Frame(frame, bg="#E2E8F0")
        table.pack(fill=tk.X, pady=(14, 0))
        headers = ["기간", "새로 등록", "자동 수집", "완료", "기한 내 처리율"]
        for c, h in enumerate(headers):
            table.columnconfigure(c, weight=1 if c else 2, uniform="col" if c else None)
            tk.Label(table, text=h, font=(FONT, 9, "bold"), bg="#F1F5F9", fg="#334155", pady=5).grid(
                row=0, column=c, sticky="nsew", padx=1, pady=1
            )
        ranges = s["ranges"]
        rows = [
            ("week", f"이번 주 ({_md(ranges['week'][0])}~{_md(ranges['week'][1])})"),
            ("month", f"이번 달 ({ranges['month'][0].month}월)"),
        ]
        for r, (key, name) in enumerate(rows, start=1):
            b = s[key]
            values = [name, f"{b['created']}건", f"{b['auto']}건", f"{b['completed']}건", rate_text(b["on_time"], b["completed"])]
            for c, v in enumerate(values):
                tk.Label(
                    table,
                    text=v,
                    font=(FONT, 9, "bold" if c == 4 else "normal"),
                    bg="#FFFFFF",
                    fg="#16A34A" if c == 4 and v != "-" else "#1E293B",
                    pady=6,
                ).grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
        tk.Label(
            frame,
            text="자동 수집 = 공문·쪽지·클립보드 등 직접 입력하지 않고 등록된 업무",
            font=(FONT, 8),
            bg=BG,
            fg="#64748B",
        ).pack(anchor="w", pady=(4, 0))

        # 3) 최근 4주 완료 막대
        tk.Label(frame, text="최근 4주 완료 건수", font=(FONT, 10, "bold"), bg=BG, fg="#0F172A").pack(
            anchor="w", pady=(12, 4)
        )
        weekly = s["weekly"]
        canvas = tk.Canvas(frame, width=560, height=140, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E2E8F0")
        canvas.pack(fill=tk.X)
        canvas.bind("<Configure>", lambda e: self._draw_bars(canvas, weekly, e.width))

        tk.Button(
            frame,
            text="닫기",
            font=(FONT, 9),
            bg="#E2E8F0",
            fg="#334155",
            relief=tk.FLAT,
            padx=14,
            pady=4,
            command=self.destroy,
        ).pack(anchor="e", pady=(12, 0))

    @staticmethod
    def _draw_bars(canvas: tk.Canvas, weekly, width: int) -> None:
        canvas.delete("all")
        n = len(weekly)
        top, bottom = 22, 112
        peak = max([c for _, c in weekly] + [1])
        slot = width / max(n, 1)
        bar_w = min(56, slot * 0.5)
        for i, (ws, count) in enumerate(weekly):
            cx = slot * (i + 0.5)
            h = (bottom - top) * count / peak
            is_this_week = i == n - 1
            color = "#2563EB" if is_this_week else "#93C5FD"
            if count:
                canvas.create_rectangle(cx - bar_w / 2, bottom - h, cx + bar_w / 2, bottom, fill=color, width=0)
            canvas.create_text(cx, bottom - h - 9, text=f"{count}건", font=(FONT, 9, "bold"), fill="#1E293B")
            label = "이번 주" if is_this_week else f"{_md(ws)}~"
            canvas.create_text(cx, bottom + 14, text=label, font=(FONT, 8), fill="#475569")
        canvas.create_line(8, bottom, width - 8, bottom, fill="#CBD5E1")
