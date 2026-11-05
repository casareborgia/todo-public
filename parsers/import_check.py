"""실제 업무 파일 점검 (DB에 등록하지 않고 결과만 보여줌).

업무관리시스템 CSV·공문 PDF·메신저 저장 파일을 넣었을 때 무엇이 어떻게 인식되는지 확인합니다.
결과는 두 가지입니다.
- 상세 결과: 제목·기한 등 문서 내용 포함 (본인 확인용)
- 공유용 요약: 파일 이름·제목·본문 없이 구조와 건수만 (문의할 때 전달용)

직접 실행: python -m parsers.import_check <파일 또는 폴더> ...
앱에서는 상단 필터 줄의 [🔎 파일 점검] 버튼으로 같은 결과를 볼 수 있습니다.
"""

import csv
import io
import os
import sys
from datetime import date
from typing import List, Optional, Tuple

from core.models import TaskStatus
from parsers.pdf_text import PDFTextError, describe_pdf, extract_pdf_text
from parsers.portal_parser import (
    MESSENGER_FILENAME_RE,
    PortalAndMessengerParser,
    csv_column_roles,
    split_messenger_export,
)

PREVIEW_LINES = 8


def check_files(
    paths: List[str],
    parser: Optional[PortalAndMessengerParser] = None,
    db=None,
    ref_date: Optional[date] = None,
) -> Tuple[str, str]:
    """(상세 결과, 공유용 요약) 문자열을 돌려줌. db를 주면 기존 업무와 연결되는지도 표시(읽기만 함)."""
    parser = parser or PortalAndMessengerParser()
    full: List[str] = []
    share: List[str] = []
    files = _expand(paths)
    if not files:
        return "점검할 파일이 없습니다.", "점검할 파일이 없습니다."
    for no, path in enumerate(files, 1):
        try:
            f, s = _check_one(no, path, parser, db, ref_date)
        except Exception as e:  # 점검 도구는 어떤 파일에서도 멈추지 않음
            f = [f"■ {os.path.basename(path)}", f"  점검 중 오류: {type(e).__name__}: {e}"]
            s = [f"■ 파일 {no}", f"  점검 중 오류: {type(e).__name__}"]
        full += f + [""]
        share += s + [""]
    return "\n".join(full).rstrip(), "\n".join(share).rstrip()


def _expand(paths: List[str]) -> List[str]:
    files: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names)]
        elif os.path.isfile(p):
            files.append(p)
    return files


def _check_one(no: int, path: str, parser: PortalAndMessengerParser, db, ref_date) -> Tuple[List[str], List[str]]:
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    full = [f"■ {name}"]
    share = [f"■ 파일 {no} ({ext or '확장자 없음'})"]
    problems: List[str] = []

    if ext == ".pdf":
        with open(path, "rb") as f:
            data = f.read()
        info = describe_pdf(data)
        summary = (
            f"  공문 PDF: 버전 {info.get('버전')} / 생성 프로그램 {info.get('생성 프로그램', '알 수 없음')}"
            f" / {info.get('쪽수', '?')}쪽 / 글자 {info.get('글자 수', 0)}자"
            f" / 한글 {round(info.get('한글 비율', 0) * 100)}%"
        )
        full.append(summary)
        share.append(summary)
        for font in info.get("폰트", []):
            full.append(f"    폰트 {font}")
            share.append(f"    폰트 {font}")
        problems += info["문제"]
        if not info["문제"]:
            try:
                lines = extract_pdf_text(path).splitlines()
            except PDFTextError:
                lines = []
            full.append(f"  본문 앞부분 ({min(len(lines), PREVIEW_LINES)}줄, 글자가 올바르게 보이는지 확인):")
            full += [f"    | {ln}" for ln in lines[:PREVIEW_LINES]]
    elif ext == ".csv":
        text = parser._read_file_AutoEncoding(path)
        rows = [r for r in csv.reader(io.StringIO(text.strip())) if any(c.strip() for c in r)]
        headers = [h.strip().lstrip("﻿") for h in rows[0]] if rows else []
        roles = csv_column_roles(headers)
        line = f"  목록 CSV: 열 {len(headers)}개, 자료 {max(len(rows) - 1, 0)}줄"
        if roles:
            cols = "  열 이름: " + " | ".join(headers)
        else:  # 첫 줄이 열 이름이 아니면 자료일 수 있으므로 공유용 요약에 넣지 않음
            cols = "  열 이름: (첫 줄이 열 이름이 아닌 것 같음)"
        found = "  인식한 열: " + (", ".join(f"{role}='{headers[i]}'" for role, i in roles.items()) or "없음")
        full += [line, cols, found]
        share += [line, cols, found]
        if "문서번호" not in roles:
            problems.append("문서번호 열을 찾지 못했습니다 (매일 다시 받으면 중복 등록될 수 있음)")
        if "제목" not in roles:
            problems.append("제목 열을 찾지 못했습니다 (제목이 부정확할 수 있음)")
        if "상태" not in roles:
            problems.append("처리상태 열이 없습니다 (완료 여부가 자동 반영되지 않음)")
    elif ext == ".txt":
        msg = MESSENGER_FILENAME_RE.match(stem)
        body = parser._read_file_AutoEncoding(path)
        n_lines = len([ln for ln in body.splitlines() if ln.strip()])
        if msg:
            export = split_messenger_export(body)
            full.append(f"  메신저 저장 파일: 파일 이름의 발신자 '{msg.group('sender')}', 시각 "
                        f"{msg.group('y')}-{msg.group('m')}-{msg.group('d')} "
                        f"{msg.group('H')}:{msg.group('M')}:{msg.group('S')}, 본문 {n_lines}줄")
            share.append(f"  메신저 저장 파일 이름 형식 인식됨, 본문 {n_lines}줄")
            if export:
                top, history = export
                line = f"  받은 메시지 1건(업무 판단 대상) + 인용된 이전 대화 {len(history)}건(참고용)"
                full.append(line + f", 받은 시각 {top.get('sent_at', '알 수 없음')}")
                share.append(line)
            else:
                full.append("  메신저 저장 형식('송신:/시간:/내용:')이 아님 → 본문 전체로 판단")
                share.append("  메신저 저장 형식('송신:/시간:/내용:')이 아님 → 본문 전체로 판단")
        else:
            full.append(f"  일반 텍스트 파일 (메신저 파일 이름 형식 아님), 본문 {n_lines}줄")
            share.append(f"  일반 텍스트 파일 (메신저 파일 이름 형식 아님), 본문 {n_lines}줄")
    else:
        full.append(f"  기타 파일 ({ext})")
        share.append(f"  기타 파일 ({ext})")

    tasks = parser.parse_file(path, ref_date=ref_date)
    full.append(f"  추출 업무 {len(tasks)}건" + (" (등록되지 않음)" if not tasks else ""))
    for i, t in enumerate(tasks, 1):
        flags = []
        if t.due_estimated:
            flags.append("기한 추정")
        if t.status == TaskStatus.COMPLETED:
            flags.append("완료 상태")
        full.append(f"   {i}) {t.title}")
        full.append(f"      기한 {t.due_date} {t.due_time}{' (' + ', '.join(flags) + ')' if flags else ''}"
                    f" / 키 {t.external_key or '(내용 기준)'}")
        if t.match_keys:
            full.append(f"      연결할 문서번호: {', '.join(k[4:] for k in t.match_keys)}")
        if db is not None:
            existing = db.find_task_by_keys([t.external_key] + list(t.match_keys))
            full.append(f"      → {'이미 있는 업무와 연결: ' + existing.title if existing else '새 업무로 등록됨'}")
    if tasks:
        share.append(
            f"  추출 업무 {len(tasks)}건: 기한 확정 {sum(not t.due_estimated for t in tasks)}"
            f" / 기한 추정 {sum(t.due_estimated for t in tasks)}"
            f" / 완료 상태 {sum(t.status == TaskStatus.COMPLETED for t in tasks)}"
            f" / 문서번호 키 {sum(t.external_key.startswith('DOC:') for t in tasks)}"
            f" / 메신저 키 {sum(t.external_key.startswith('MSG:') for t in tasks)}"
        )
    else:
        share.append("  추출 업무 0건")
        if ext == ".txt":
            # 메신저 답장·인사처럼 할 일이 없는 메시지는 정상적으로 0건
            full.append("  업무로 볼 내용(요청·기한)이 없어 등록하지 않음")
            share.append("  업무로 볼 내용(요청·기한)이 없어 등록하지 않음")
        elif not problems:
            problems.append("업무를 찾지 못했습니다")

    for p in problems:
        full.append(f"  ⚠ {p}")
        share.append(f"  ⚠ {p}")
    if not problems:
        full.append("  문제 없음")
        share.append("  문제 없음")
    return full, share


def main(argv: List[str]) -> int:
    if not argv:
        print("사용법: python -m parsers.import_check <파일 또는 폴더> ...")
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    full, share = check_files(argv)
    print(full)
    print("\n===== 공유용 요약 (파일 이름·제목·본문 없음) =====\n")
    print(share)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
