"""월간 캘린더 날짜 칸에 업무를 몇 개 보여줄지, 제목을 어디서 자를지 정하는 계산.

많이 쓰는 캘린더처럼 글자 크기는 그대로 두고, 칸 높이에 들어가는 만큼만 보이고 나머지는
'+ 외 N건'으로 접음. 화면(tkinter) 없이 시험할 수 있게 메인 창에서 분리함.
"""

from typing import Callable, Tuple

DEFAULT_SHOWN = 3  # 칸 크기를 아직 모를 때(창이 그려지기 전) 보여줄 개수 — 예전과 같음


def chips_that_fit(n_tasks: int, avail_px: int, slot_px: int, more_px: int) -> Tuple[int, int]:
    """(보여줄 개수, 접을 개수).

    avail_px: 날짜 숫자 줄 아래 남은 높이, slot_px: 업무 1개 높이(간격 포함), more_px: '+ 외 N건' 줄 높이.
    모두 들어가면 다 보이고, 넘치면 '+ 외 N건' 줄 자리를 남긴 만큼만 보임.
    칸 크기를 모르면(avail_px <= 0) 예전처럼 3개.
    """
    if n_tasks <= 0:
        return 0, 0
    if avail_px <= 0 or slot_px <= 0:
        shown = min(n_tasks, DEFAULT_SHOWN)
        return shown, n_tasks - shown
    if n_tasks * slot_px <= avail_px:
        return n_tasks, 0
    shown = max(0, (avail_px - more_px) // slot_px)
    # 너무 낮은 칸이라도 업무가 있다는 것은 보이도록 1개는 남김 ('+ 외 N건'이 잘릴 수 있음)
    shown = max(1, min(shown, n_tasks - 1))
    return shown, n_tasks - shown


def fit_title(text: str, max_px: int, measure: Callable[[str], int], ellipsis: str = "…") -> str:
    """max_px 너비에 들어가도록 뒤를 잘라 '…'을 붙임. measure는 글자 너비(px)를 재는 함수."""
    if max_px <= 0 or measure(text) <= max_px:
        return text
    lo, hi = 0, len(text)  # text[:lo] + … 는 들어가고, text[:hi+1] + … 는 넘친다고 보고 이분 탐색
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid] + ellipsis) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis if lo > 0 else text[:1] + ellipsis
