"""EzQ 쪽지 표본 회귀 확인: 표본 폴더의 쪽지(txt)를 지금 규칙으로 판정해 건수만 보여줌.

표본은 실제 쪽지라 저장소 밖에 둠 (예: 별도 폴더). 파일 이름 끝이 정답 표시: '…_업무.txt', '…_공지.txt' 등.
본문·보낸 사람은 출력하지 않음. 규칙을 고치기 전후로 판정이 바뀐 파일만 확인할 수 있음.

  python scripts/ezq_sample_eval.py <표본 폴더>                     건수
  python scripts/ezq_sample_eval.py <표본 폴더> --save base.json     지금 판정을 저장
  python scripts/ezq_sample_eval.py <표본 폴더> --compare base.json  저장한 판정과 달라진 파일 이름
"""

import argparse
import json
import os
import sys
from collections import Counter
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.ezq_collector import EzqMessageCollector  # noqa: E402
from core.database import DatabaseManager  # noqa: E402
from parsers.message_scorer import EXCLUDE, REGISTER, REVIEW  # noqa: E402
from parsers.portal_parser import PortalAndMessengerParser  # noqa: E402

TASK_LABEL = "업무"


def label_of(file_name: str) -> str:
    """'12_가상_업무.txt' → '업무'. 표시가 없으면 ''."""
    stem = os.path.splitext(file_name)[0]
    return stem.rsplit("_", 1)[1] if "_" in stem else ""


def _read(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("cp949", "utf-8-sig"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace")


def evaluate(folder: str) -> Dict[str, str]:
    """파일 이름 → 판정(등록/확인 필요/제외). 판정만 하고 아무것도 저장하지 않음."""
    collector = EzqMessageCollector(DatabaseManager(":memory:"), PortalAndMessengerParser(), reader=lambda _c: [])
    result = {}
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".txt"):
            result[name] = collector.judge(_read(os.path.join(folder, name))).verdict
    return result


def summarize(verdicts: Dict[str, str]) -> List[str]:
    by_label: Dict[str, Counter] = {}
    for name, verdict in verdicts.items():
        by_label.setdefault(label_of(name) or "(표시 없음)", Counter())[verdict] += 1
    lines = [f"표본 {len(verdicts)}건"]
    for label, counts in sorted(by_label.items()):
        parts = ", ".join(f"{v} {counts[v]}" for v in (REGISTER, REVIEW, EXCLUDE) if counts[v])
        lines.append(f"- {label} {sum(counts.values())}건: {parts}")
    missed = sum(1 for n, v in verdicts.items() if label_of(n) == TASK_LABEL and v == EXCLUDE)
    false_reg = sum(1 for n, v in verdicts.items() if label_of(n) not in (TASK_LABEL, "") and v == REGISTER)
    lines.append(f"업무 누락 {missed}건 / 업무 아닌데 등록 {false_reg}건")
    return lines


def compare(verdicts: Dict[str, str], base_path: str) -> List[str]:
    with open(base_path, encoding="utf-8") as f:
        base = json.load(f)
    return [f"{n}: {base[n]} → {v}" for n, v in sorted(verdicts.items()) if n in base and base[n] != v]


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="EzQ 쪽지 표본 회귀 확인 (건수만 출력)")
    ap.add_argument("folder")
    ap.add_argument("--save")
    ap.add_argument("--compare")
    args = ap.parse_args(argv)
    verdicts = evaluate(args.folder)
    print("\n".join(summarize(verdicts)))
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(verdicts, f, ensure_ascii=False, indent=1)
    if args.compare:
        changed = compare(verdicts, args.compare)
        print(f"판정이 바뀐 파일 {len(changed)}건")
        print("\n".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
