"""키보드 단축키 목록과 여러 업무 선택·삭제의 화면 밖 로직.

화면(tkinter) 없이 시험할 수 있도록 메인 창에서 분리함.
"""

from typing import Dict, Iterable, List, Tuple

from core.models import TaskItem

# (키, 동작, 쓰는 곳) — F1 안내 창과 사용 안내 문서가 이 목록을 그대로 보여 줌
SHORTCUTS: List[Tuple[str, str, str]] = [
    ("Ctrl+N", "새 업무 추가", "어디서나"),
    ("Ctrl+L", "빠른 입력 칸으로 이동", "어디서나"),
    ("Ctrl+1", "오른쪽 날짜 업무 목록으로 이동", "어디서나"),
    ("Ctrl+2", "일정 목록 보기로 바꾸고 목록으로 이동", "어디서나"),
    ("Alt+← / Alt+→", "이전 달 / 다음 달", "어디서나"),
    ("Ctrl+T", "오늘로 이동", "어디서나"),
    ("F5", "감시 폴더 지금 확인", "어디서나"),
    ("Ctrl+Shift+V", "클립보드 문서 확인", "어디서나"),
    ("F1", "단축키 안내 보기", "어디서나"),
    ("↑ / ↓", "업무 이동", "업무 목록"),
    ("Shift+↑↓ · Ctrl/Shift+클릭", "여러 업무 선택", "업무 목록"),
    ("Ctrl+A", "목록 전체 선택", "업무 목록"),
    ("Esc", "선택 해제", "업무 목록"),
    ("Enter", "편집 창 열기", "업무 목록"),
    ("Space", "완료 / 미완료 전환", "업무 목록"),
    ("Delete", "선택한 업무 삭제 (여러 건 한 번에)", "업무 목록"),
    ("Ctrl+Enter", "저장", "편집 창"),
    ("Esc", "닫기 (저장 안 함)", "편집 창"),
]

HINT_TEXT = "💡 우클릭: 추가·편집  ·  끌어 놓기: 기한 변경  ·  F1: 단축키"


def shortcut_help_text() -> str:
    """F1 안내 창 본문. 쓰는 곳별로 묶어 보여 줌."""
    lines: List[str] = []
    scope = None
    for keys, action, where in SHORTCUTS:
        if where != scope:
            if lines:
                lines.append("")
            lines.append(f"[{where}]")
            scope = where
        lines.append(f"  {keys}  —  {action}")
    return "\n".join(lines)


def tasks_in_selection(selection: Iterable[str], task_map: Dict[str, TaskItem]) -> List[TaskItem]:
    """목록에서 고른 줄(iid) → 업무 목록. 표에 없는 줄·저장 안 된 업무·같은 업무 중복은 뺌."""
    tasks: List[TaskItem] = []
    seen = set()
    for iid in selection:
        t = task_map.get(iid)
        if t is None or t.id is None or t.id in seen:
            continue
        seen.add(t.id)
        tasks.append(t)
    return tasks


def delete_confirm_message(tasks: List[TaskItem], max_titles: int = 5) -> str:
    """삭제 확인 창 문구. 1건이면 기존 문구, 여러 건이면 건수와 제목 몇 개."""
    if len(tasks) == 1:
        return f"'{tasks[0].title}' 업무를 삭제하시겠습니까?"
    lines = [f"선택한 업무 {len(tasks)}건을 삭제하시겠습니까?", ""]
    lines += [f"• {t.title}" for t in tasks[:max_titles]]
    if len(tasks) > max_titles:
        lines.append(f"… 외 {len(tasks) - max_titles}건")
    lines += ["", "삭제한 업무는 되돌릴 수 없습니다."]
    return "\n".join(lines)
