"""사내포털 저장 문서(TXT, HTML, CSV, JSON) 및 메신저 대화 묶음 구조 파서.

사용자가 사내포털 화면을 '다른 이름으로 저장(HTML/TXT/CSV)'하거나
복수 건의 배부 문서/메신저 대화를 복사·저장했을 때 개별 업무 단위로 분리해 추출합니다.
"""

import csv
import io
import json
import os
import re
from datetime import date
from html.parser import HTMLParser
from typing import List, Optional

from core.models import TaskItem, TaskSource, TaskStatus
from parsers.base_parser import BaseTaskExtractor
from parsers.pdf_text import PDFTextError, extract_pdf_text
from parsers.rule_parser import CHAT_HEADER_RE, DOC_NO_TOKEN, RuleBasedExtractor

# 사내 메신저 저장 파일명: '발신자-YYYY-MM-DD_HHMMSS.txt' (또는 '발신자-YYYY-MM-DD-HH-MM-SS.txt')
MESSENGER_FILENAME_RE = re.compile(
    r"^(?P<sender>.+?)-(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})[-_ ](?P<H>\d{2})-?(?P<M>\d{2})-?(?P<S>\d{2})$"
)

# 메신저 저장 파일 내용: 맨 위가 받은 메시지('송신:/시간:/제목:/내용:'),
# 그 아래 '-----' 줄로 구분된 이전 대화 인용('발신자 :/수신자 :/날 짜 :')
MESSENGER_SEPARATOR_RE = re.compile(r"^[ \t]*-{10,}[ \t]*$", re.MULTILINE)
MESSENGER_FIELD_RE = re.compile(r"^\s*(송신|시간|제목|내용|발신자|수신자|날\s*짜)\s*:\s?(.*)$")
MESSENGER_TIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")
# 받은 메시지가 '아래 내용 확인 부탁드립니다'처럼 인용된 이전 메시지를 가리키는 경우
REFERS_TO_QUOTE_RE = re.compile(r"아래|하단|밑의|밑에|전달\s*드|포워드")

# 공문 PDF 판별: 하단 '시행 부서-번호' 줄, 또는 '제 목'과 '수신' 줄이 함께 있음
GONGMUN_ENFORCE_RE = re.compile(r"^\s*시행\s*" + DOC_NO_TOKEN, re.MULTILINE)
GONGMUN_TITLE_RE = re.compile(r"^\s*제\s*목\s", re.MULTILINE)
GONGMUN_RECEIVER_RE = re.compile(r"^\s*수\s*신", re.MULTILINE)


def is_work_file_name(file_path: str) -> bool:
    """'업무 파일만' 폴더에서 받을 파일인지 이름으로 먼저 거름: 메신저 쪽지 txt, PDF."""
    stem, ext = os.path.splitext(os.path.basename(file_path))
    ext = ext.lower()
    if ext == ".txt":
        return bool(MESSENGER_FILENAME_RE.match(stem))
    return ext == ".pdf"


def looks_like_gongmun(text: str) -> bool:
    if GONGMUN_ENFORCE_RE.search(text):
        return True
    return bool(GONGMUN_TITLE_RE.search(text) and GONGMUN_RECEIVER_RE.search(text))


# 업무관리시스템 목록의 처리상태 중 '이미 끝난 문서'로 볼 값
COMPLETED_STATUS_RE = re.compile(r"(?<!미)(?:완료|종결)|처리됨|결재됨|공람됨")

# 새 문서의 시작을 알리는 말머리 줄
DOC_TAG_START_RE = re.compile(
    r"^\s*\[(?:배부|공문|협조|결재|요청|긴급|업무|쪽지|메신저|알림|공지)\]", re.MULTILINE
)
# 문서번호 줄: 현재 블록에 이미 문서번호가 있을 때만 새 문서의 시작으로 봄
DOC_NO_START_RE = re.compile(r"^\s*(?:문서번호|접수번호)\s*[:：]", re.MULTILINE)


class _SimpleHTMLTableAndBlockParser(HTMLParser):
    """표준 라이브러리 html.parser만을 사용하여 사내포털 HTML의 표(tr)와 문단 블록을 추출."""

    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self.blocks: List[str] = []
        self._in_tr = False
        self._in_cell = False
        self._current_row: List[str] = []
        self._current_buf: List[str] = []
        self._skip_tags = {"script", "style", "head"}
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        t = tag.lower()
        if t in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "tr":
            self._in_tr = True
            self._current_row = []
        elif t in ("td", "th") and self._in_tr:
            self._in_cell = True
            self._current_buf = []
        elif t in ("p", "div", "li", "br", "h1", "h2", "h3", "h4"):
            self._current_buf.append("\n")

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if t in ("td", "th") and self._in_cell:
            cell_text = re.sub(r"\s+", " ", "".join(self._current_buf)).strip()
            if cell_text:
                self._current_row.append(cell_text)
            self._current_buf = []
            self._in_cell = False
        elif t == "tr" and self._in_tr:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = []
            self._in_tr = False
        elif t in ("p", "div", "li", "article", "section") and not self._in_tr:
            blk = "".join(self._current_buf).strip()
            if blk:
                self.blocks.append(blk)
            self._current_buf = []

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        self._current_buf.append(data)


def split_messenger_export(text: str) -> Optional[tuple]:
    """메신저 저장 파일을 (맨 위 받은 메시지, [인용된 이전 메시지...])로 분리. 형식이 아니면 None.

    각 메시지는 {'송신'/'발신자', '수신자', '제목', 'sent_at': 'YYYY-MM-DD HH:MM:SS', 'body'} 사전.
    """
    parts = MESSENGER_SEPARATOR_RE.split(text.replace("\r\n", "\n"))
    messages = [_parse_messenger_block(p) for p in parts]
    top = messages[0]
    if not top or "송신" not in top or "body" not in top:
        return None
    return top, [m for m in messages[1:] if m and m.get("body")]


def _parse_messenger_block(block: str) -> Optional[dict]:
    msg: dict = {}
    body: List[str] = []
    in_body = False  # 머리 항목이 끝나고 본문이 시작됐는지 (본문 속 '제목:' 같은 줄은 항목으로 보지 않음)
    for line in block.strip("\n").splitlines():
        field = None if in_body else MESSENGER_FIELD_RE.match(line)
        if field:
            key = re.sub(r"\s", "", field.group(1))
            if key in ("시간", "날짜"):
                t = MESSENGER_TIME_RE.search(field.group(2))
                if t:
                    msg["sent_at"] = "{}-{}-{} {}:{}:{}".format(*t.groups())
            elif key == "내용":
                in_body = True
                body.append(field.group(2))
            else:
                msg[key] = field.group(2).strip()
        elif msg:
            in_body = in_body or bool(line.strip())
            if in_body:
                body.append(line)
    if not msg:
        return None
    text = "\n".join(body).strip()
    if text:
        msg["body"] = text
    return msg


def _messenger_task_text(msg: dict) -> str:
    """메신저 '제목'이 본문 첫 줄을 그대로 옮긴 것이면 빼고, 따로 쓴 제목이면 '제목:' 줄로 붙임."""
    title = msg.get("제목", "").strip()
    body = msg["body"]
    squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    if title and not squash(body).startswith(squash(title)):
        return f"제목: {title}\n{body}"
    return body


def _date_of(sent_at: str) -> Optional[date]:
    try:
        return date(int(sent_at[0:4]), int(sent_at[5:7]), int(sent_at[8:10]))
    except (ValueError, IndexError):
        return None


CSV_DOC_NO_HEADER_RE = re.compile(r"(?:문서|접수|생산|등록|시행|공문)\s*번호")
CSV_TITLE_HEADER_RE = re.compile(r"제\s*목|건\s*명|문서명|과제명|업무명")


def csv_column_roles(headers: List[str]) -> dict:
    """CSV 열 이름에서 문서번호·제목·상태 열의 위치를 찾음 (각 역할의 첫 번째 열)."""
    roles: dict = {}
    for i, h in enumerate(headers):
        if CSV_DOC_NO_HEADER_RE.search(h):
            roles.setdefault("문서번호", i)
        elif CSV_TITLE_HEADER_RE.search(h):
            roles.setdefault("제목", i)
        elif "상태" in h:
            roles.setdefault("상태", i)
    return roles


class PortalAndMessengerParser:
    """다양한 포맷의 사내포털/메신저 저장 데이터에서 다중 TaskItem을 추출."""

    def __init__(self, extractor: Optional[BaseTaskExtractor] = None):
        self.extractor = extractor or RuleBasedExtractor()

    def parse_file(
        self, file_path: str, ref_date: Optional[date] = None, work_files_only: bool = False
    ) -> List[TaskItem]:
        """work_files_only: 다운로드 폴더처럼 다른 파일이 섞인 곳 → 메신저 쪽지 txt와 공문 PDF만 처리."""
        if not os.path.isfile(file_path):
            return []
        if work_files_only and not is_work_file_name(file_path):
            return []

        stem, ext = os.path.splitext(os.path.basename(file_path))
        ext = ext.lower()
        file_name = os.path.basename(file_path)

        if ext == ".pdf":
            return self.parse_pdf_file(file_path, ref_date=ref_date, gongmun_only=work_files_only)

        raw_content = self._read_file_AutoEncoding(file_path)
        if not raw_content.strip():
            return []

        msg = MESSENGER_FILENAME_RE.match(stem)
        if msg and ext == ".txt":
            return self.parse_messenger_text(raw_content, msg, file_name)

        if ext == ".csv":
            return self.parse_csv_text(raw_content, source_ref=f"파일: {file_name}", ref_date=ref_date)
        if ext in (".html", ".htm"):
            return self.parse_html_text(raw_content, source_ref=f"파일: {file_name}", ref_date=ref_date)
        if ext == ".json":
            return self.parse_json_text(raw_content, source_ref=f"파일: {file_name}", ref_date=ref_date)

        return self.parse_plain_text(raw_content, source_ref=f"파일: {file_name}", ref_date=ref_date)

    def parse_pdf_file(
        self, file_path: str, ref_date: Optional[date] = None, gongmun_only: bool = False
    ) -> List[TaskItem]:
        """업무관리시스템 공문 PDF: 본문에서 처리기한을 찾아 같은 문서번호의 업무에 연결."""
        try:
            text = extract_pdf_text(file_path)
        except (PDFTextError, OSError, ValueError):
            return []
        if not text.strip():
            return []  # 스캔 이미지 PDF 등 글자가 없는 경우
        if gongmun_only and not looks_like_gongmun(text):
            return []  # 설명서·영수증 등 공문이 아닌 PDF
        return self.extractor.extract_tasks(
            text,
            source_hint=TaskSource.PORTAL,
            source_ref=f"PDF: {os.path.basename(file_path)}",
            ref_date=ref_date,
        )

    def parse_messenger_text(self, text: str, name_match: "re.Match", file_name: str) -> List[TaskItem]:
        """'발신자-YYYY-MM-DD_HHMMSS.txt' 메신저 저장 파일: 발신일 기준으로 상대 날짜 계산.

        메신저 저장 형식('송신:/시간:/제목:/내용:' + 인용된 이전 대화)이면 맨 위 받은 메시지만 업무로 보고,
        이전 대화는 설명(참고)으로만 붙입니다. 이미 주고받은 인용 메시지가 따로 등록되지 않게 하기 위함입니다.
        """
        g = name_match.groupdict()
        sender = g["sender"].strip()
        sent_at = f"{g['y']}-{g['m']}-{g['d']} {g['H']}:{g['M']}:{g['S']}"
        export = split_messenger_export(text)
        if export is None:
            items = self.parse_plain_text(
                text,
                source_hint=TaskSource.MESSENGER,
                source_ref=f"메신저: {sender} {sent_at}",
                ref_date=_date_of(sent_at),
            )
        else:
            top, history = export
            sender = top.get("송신") or sender
            sent_at = top.get("sent_at") or sent_at
            items = self.extractor.extract_tasks(
                _messenger_task_text(top),
                source_hint=TaskSource.MESSENGER,
                source_ref=f"메신저: {sender} {sent_at}",
                ref_date=_date_of(sent_at),
            )
            for item in items:
                item.description = text.strip()
                if item.due_estimated and REFERS_TO_QUOTE_RE.search(top["body"]):
                    self._take_due_from_history(item, history)
        for i, item in enumerate(items):
            if not item.external_key.startswith("DOC:"):
                item.external_key = f"MSG:{sender}|{sent_at}|{i}"
        return items

    def _take_due_from_history(self, item: TaskItem, history: List[dict]) -> None:
        """'아래 내용 확인 부탁드립니다' 같은 전달 메시지: 인용된 이전 메시지(최근 것부터)의 기한을 사용."""
        if not isinstance(self.extractor, RuleBasedExtractor):
            return
        for msg in history:
            due_date, due_time, estimated = self.extractor.extract_due(
                msg["body"], ref_date=_date_of(msg.get("sent_at", ""))
            )
            if not estimated:
                item.due_date, item.due_time, item.due_estimated = due_date, due_time, False
                return

    def _read_file_AutoEncoding(self, file_path: str) -> str:
        """윈도우 사내 환경(UTF-8, UTF-8-SIG, CP949/EUC-KR) 인코딩 자동 감지 읽기."""
        for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def parse_plain_text(
        self,
        text: str,
        source_hint: str = "AUTO",
        source_ref: str = "",
        ref_date: Optional[date] = None,
    ) -> List[TaskItem]:
        """일반 텍스트(사내포털 복사본, 메신저 대화 저장본 등)를 블록별로 분리하여 파싱."""
        chunks = self._split_text_into_blocks(text)
        results: List[TaskItem] = []
        for chunk in chunks:
            items = self.extractor.extract_tasks(
                chunk, source_hint=source_hint, source_ref=source_ref, ref_date=ref_date
            )
            results.extend(items)
        return results

    def _split_text_into_blocks(self, text: str) -> List[str]:
        normalized = text.replace("\r\n", "\n").strip()
        if not normalized:
            return []

        # 1) 명시적 구분선 (---, ===, ***) 기준 분리
        if re.search(r"^\s*[-=*]{3,}\s*$", normalized, re.MULTILINE):
            parts = re.split(r"^\s*[-=*]{3,}\s*$", normalized, flags=re.MULTILINE)
            return [p.strip() for p in parts if p.strip()]

        # 2) 메신저 대화 로그: 대화 헤더 줄('[홍길동 과장] 오전 09:30')이 2개 이상이면 메시지 단위로 분리
        #    (본문 안의 '[긴급]' 같은 말머리로 쪼개지 않도록 문서 말머리 분리보다 먼저 처리)
        chat_starts = [m.start() for m in CHAT_HEADER_RE.finditer(normalized)]
        if len(chat_starts) > 1:
            return self._split_at(normalized, chat_starts)

        # 3) 복수 건의 '[배부]', '[공문]', '[협조]' 등 말머리 또는 반복되는 '문서번호:' 기준 분리
        doc_parts = self._split_by_doc_headers(normalized)
        if len(doc_parts) > 1:
            return doc_parts

        # 4) 빈 줄 2개 이상으로 구분된 단락들 중 각각 독립된 업무/기한을 가진 경우
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]
        if len(paragraphs) > 1:
            # 각 단락이 독립적인 번호 매기기(1., 2., ①, ②)나 말머리로 시작하면 개별 블록으로 처리
            if all(re.match(r"^(?:\d+[.)]|[①-⑳]|[-•▶■]|\[)", p) for p in paragraphs):
                return paragraphs

        # 5) 표를 드래그해서 복사한 탭(TSV) 구분 줄 처리
        lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
        if len(lines) >= 2 and sum(1 for ln in lines if "\t" in ln) >= len(lines) // 2:
            return lines

        return [normalized]

    @staticmethod
    def _split_at(text: str, starts: List[int]) -> List[str]:
        bounds = sorted(set([0] + starts + [len(text)]))
        parts = [text[a:b].strip() for a, b in zip(bounds, bounds[1:])]
        return [p for p in parts if p]

    def _split_by_doc_headers(self, text: str) -> List[str]:
        """말머리 줄은 항상 새 문서로, 문서번호 줄은 현재 블록에 이미 문서번호가 있을 때만 새 문서로 분리.

        '[배부] 제목' 다음 줄의 '문서번호: ...'는 같은 문서이므로 쪼개지 않음.
        """
        marks = [(m.start(), "tag") for m in DOC_TAG_START_RE.finditer(text)]
        marks += [(m.start(), "docno") for m in DOC_NO_START_RE.finditer(text)]
        starts: List[int] = []
        seg_start = 0
        for pos, kind in sorted(marks):
            if pos <= seg_start:
                continue
            if kind == "docno" and not DOC_NO_START_RE.search(text[seg_start:pos]):
                continue
            starts.append(pos)
            seg_start = pos
        return self._split_at(text, starts)

    def parse_csv_text(
        self, csv_text: str, source_ref: str = "", ref_date: Optional[date] = None
    ) -> List[TaskItem]:
        """사내포털에서 내보내기/저장한 CSV 문서 목록 파싱."""
        reader = csv.reader(io.StringIO(csv_text.strip()))
        rows = [r for r in reader if any(cell.strip() for cell in r)]
        if not rows:
            return []

        results: List[TaskItem] = []
        headers = [h.strip().lstrip("﻿") for h in rows[0]]
        has_header = any(
            any(k in h for k in ("제목", "건명", "문서", "기한", "마감", "발신", "부서", "내용", "상태"))
            for h in headers
        )
        data_rows = rows[1:] if has_header else rows
        columns = csv_column_roles(headers) if has_header else {}
        status_idx = columns.get("상태")
        doc_no_idx = columns.get("문서번호")

        for row in data_rows:
            if has_header:
                composed_lines = []
                for idx, val in enumerate(row):
                    col_name = headers[idx] if idx < len(headers) else f"항목{idx+1}"
                    # '문서번호(접수)', '문서제목' 같은 시스템별 열 이름을 규칙 파서가 아는 이름으로 통일
                    if idx == doc_no_idx:
                        col_name = "문서번호"
                    elif idx == columns.get("제목"):
                        col_name = "제목"
                    if val.strip():
                        composed_lines.append(f"{col_name}: {val.strip()}")
                block_text = "\n".join(composed_lines)
            else:
                block_text = " | ".join(c.strip() for c in row if c.strip())

            items = self.extractor.extract_tasks(
                block_text,
                source_hint=TaskSource.PORTAL,
                source_ref=source_ref,
                ref_date=ref_date,
            )
            if status_idx is not None and status_idx < len(row) and COMPLETED_STATUS_RE.search(row[status_idx]):
                for item in items:
                    item.status = TaskStatus.COMPLETED
            # 문서번호 형식이 '부서-번호'가 아니어도 목록의 문서번호 칸을 그대로 식별자로 사용
            doc_no = re.sub(r"\s+", "", row[doc_no_idx]) if doc_no_idx is not None and doc_no_idx < len(row) else ""
            if doc_no:
                for item in items:
                    if not item.external_key.startswith("DOC:"):
                        item.external_key = f"DOC:{doc_no}"
                    if item.external_key not in item.match_keys:
                        item.match_keys.insert(0, item.external_key)
            results.extend(items)
        return results

    def parse_html_text(
        self, html_text: str, source_ref: str = "", ref_date: Optional[date] = None
    ) -> List[TaskItem]:
        """사내포털 웹페이지를 '다른 이름으로 저장(.html)'한 파일에서 표/문단 추출."""
        parser = _SimpleHTMLTableAndBlockParser()
        parser.feed(html_text)
        results: List[TaskItem] = []

        if parser.rows:
            for row in parser.rows:
                # 헤더 행(예: ['번호', '문서번호', '제목', '처리기한'])은 건너뜀
                joined = " | ".join(row)
                if all(
                     len(c) <= 6 and any(k in c for k in ("번호", "제목", "건명", "기한", "부서", "기안", "상태", "구분"))
                    for c in row
                ):
                    continue
                items = self.extractor.extract_tasks(
                    joined,
                    source_hint=TaskSource.PORTAL,
                    source_ref=source_ref,
                    ref_date=ref_date,
                )
                results.extend(items)

        for blk in parser.blocks:
            items = self.extractor.extract_tasks(
                blk,
                source_hint=TaskSource.PORTAL,
                source_ref=source_ref,
                ref_date=ref_date,
            )
            results.extend(items)

        return results

    def parse_json_text(
        self, json_text: str, source_ref: str = "", ref_date: Optional[date] = None
    ) -> List[TaskItem]:
        try:
            data = json.loads(json_text)
        except Exception:
            return self.parse_plain_text(json_text, source_ref=source_ref, ref_date=ref_date)

        items_list = data if isinstance(data, list) else data.get("items", [data])
        results: List[TaskItem] = []
        for entry in items_list:
            if isinstance(entry, dict):
                lines = [f"{k}: {v}" for k, v in entry.items() if v is not None]
                text_block = "\n".join(lines)
            else:
                text_block = str(entry)
            extracted = self.extractor.extract_tasks(
                text_block, source_hint="AUTO", source_ref=source_ref, ref_date=ref_date
            )
            results.extend(extracted)
        return results
