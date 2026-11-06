"""공문 표본 회귀 확인: 폴더의 공문(pdf·txt) 본문에 업무관리시스템 문서 판정 규칙을 적용해 건수와 번호별 결과만 보여줌.

문서 창에서 그 본문을 읽었다면 어떻게 판정할지를 보는 것이며, 폴더·클립보드 가져오기 경로의 판정이 아님
(폴더로 넣은 PDF/txt는 예전 판정 그대로).

표본은 실제 회사 공문이라 저장소 밖에 둠 (예: 별도 폴더).
제목·본문·문서번호는 출력하지 않음. 번호는 폴더 안 파일 이름 정렬 순서 (1부터).

  python scripts/gongmun_sample_eval.py <표본 폴더>                   기준일 = 오늘
  python scripts/gongmun_sample_eval.py <표본 폴더> --ref 2026-11-06  기준일 지정
"""

import argparse
import os
import sys
from collections import Counter
from datetime import date, datetime
from typing import List, NamedTuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.workmgmt_collector import WorkMgmtDocCollector  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from parsers.message_scorer import EXCLUDE, REGISTER, REVIEW  # noqa: E402
from parsers.pdf_text import PDFTextError, extract_pdf_text  # noqa: E402
from parsers.portal_parser import PortalAndMessengerParser  # noqa: E402

EXTS = (".pdf", ".txt")


class Row(NamedTuple):
    index: int
    verdict: str
    due: str
    estimated: bool
    start: str


def _read(path: str) -> str:
    if path.lower().endswith(".pdf"):
        return extract_pdf_text(path)
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace")


def evaluate(folder: str, ref: date) -> List[Row]:
    """번호별 (판정, 기한, 추정 여부, 시작일). 판정만 하고 아무것도 저장하지 않음."""
    collector = WorkMgmtDocCollector(
        DatabaseManager(":memory:"), PortalAndMessengerParser(), reader=lambda *a, **k: [], list_reader=lambda *a, **k: []
    )
    extractor = collector.parser.extractor
    rows = []
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(EXTS))
    for i, name in enumerate(names, 1):
        try:
            text = _read(os.path.join(folder, name))
        except (OSError, PDFTextError):
            rows.append(Row(i, "읽기 실패", "", True, ""))
            continue
        verdict = collector.judge_document(text).verdict
        # 제외 문서도 기한은 보여줌 (규칙 확인용)
        items = extractor.extract_tasks(text, ref_date=ref, already_judged=True)
        if items:
            it = items[0]
            rows.append(Row(i, verdict, it.due_date, it.due_estimated, it.start_date))
        else:
            rows.append(Row(i, verdict, "", True, ""))
    return rows


def report(rows: List[Row]) -> List[str]:
    counts = Counter(r.verdict for r in rows)
    lines = [f"표본 {len(rows)}건: " + ", ".join(f"{v} {counts[v]}" for v in (REGISTER, REVIEW, EXCLUDE) if counts[v])]
    for r in rows:
        due = f"{r.due}({'추정' if r.estimated else '명시'})" if r.due else "-"
        period = f"기간 있음 {r.start}~{r.due}" if r.start else "기간 없음"
        lines.append(f"{r.index}: {r.verdict}, {due}, {period}")
    return lines


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="공문 표본 회귀 확인: 파일 본문에 업무관리시스템 문서 판정 규칙 적용 (폴더 가져오기 판정 아님, 건수·번호별 판정만 출력)")
    ap.add_argument("folder")
    ap.add_argument("--ref", help="기준일 YYYY-MM-DD (기본 오늘)")
    args = ap.parse_args(argv)
    ref = datetime.strptime(args.ref, "%Y-%m-%d").date() if args.ref else date.today()
    print("\n".join(report(evaluate(args.folder, ref))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
