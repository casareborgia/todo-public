"""업무관리시스템 [전자문서 > 결재 > 결재시행] 목록 표 해석.

부서장이 배부하면 업무담당자의 결재시행 목록에 문서종류 '수신', 처리구분 '업무담당자'(또는 '업무관리자')로
나타남 (업무관리시스템 설명서 Ⅱ-8 '배부'). 이 목록에는 처리기한 칸이 없어 기한은 문서 본문에서 정함.
머리글 이름은 화면마다 조금씩 달라 후보 이름으로 열을 찾음 (띄어쓰기 무시).
"""

import re
from typing import Dict, List, NamedTuple, Optional, Tuple


class ListRow(NamedTuple):
    title: str
    doc_no: str
    doc_kind: str
    role: str
    sender_dept: str
    received_at: str  # 화면 글자 그대로 (예: '2026-11-04 09:12')


HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "title": ("제목", "문서제목"),
    "doc_no": ("문서번호", "접수번호"),
    "doc_kind": ("문서종류",),
    "role": ("처리구분",),
    "sender_dept": ("입안자", "입안부서", "발송부서"),
    "received_at": ("수신일시", "접수일시", "도착일시"),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _columns(headers: List[str]) -> Dict[str, int]:
    names = [_norm(h) for h in headers]
    cols: Dict[str, int] = {}
    for role, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in names:
                cols[role] = names.index(alias)
                break
    return cols


def parse_list_table(headers: List[str], rows: List[List[str]]) -> List[ListRow]:
    """머리글로 열 위치를 찾아 행을 ListRow로. 칸이 머리글보다 적은 행(빈 목록 안내 등)은 버림."""
    cols = _columns(headers)
    if "title" not in cols or "doc_no" not in cols:
        return []
    result: List[ListRow] = []
    for cells in rows:
        if len(cells) < len(headers):
            continue

        def cell(role: str) -> str:
            idx = cols.get(role)
            return cells[idx].strip() if idx is not None else ""

        title = cell("title")
        if not title:
            continue
        result.append(
            ListRow(title, _norm(cell("doc_no")), cell("doc_kind"), cell("role"), cell("sender_dept"), cell("received_at"))
        )
    return result


def classify_row(row: ListRow) -> Optional[str]:
    """'task'(수신 + 업무담당자) / 'review'(수신 + 업무관리자) / None."""
    if "수신" not in row.doc_kind:
        return None
    if "업무담당자" in row.role:
        return "task"
    if "업무관리자" in row.role:
        return "review"
    return None


def distributed_rows(rows: List[ListRow]) -> List[Tuple[ListRow, str]]:
    """배부받은 행만, 같은 문서(문서번호, 없으면 제목)는 하나로 — 업무담당자 행 우선."""
    picked: Dict[str, Tuple[ListRow, str]] = {}
    order: List[str] = []
    for row in rows:
        kind = classify_row(row)
        if kind is None:
            continue
        key = row.doc_no or _norm(row.title)
        if key not in picked:
            picked[key] = (row, kind)
            order.append(key)
        elif kind == "task" and picked[key][1] != "task":
            picked[key] = (row, kind)
    return [picked[k] for k in order]
