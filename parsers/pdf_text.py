"""표준 라이브러리 전용 PDF 텍스트 추출기.

업무관리시스템에서 내려받은 공문 PDF(글자 선택이 가능한 PDF)에서 본문 텍스트를 뽑아
처리기한·문서번호를 찾기 위한 용도입니다. 외부 라이브러리 없이 zlib, re만 사용합니다.

지원 범위
- FlateDecode / ASCII85 / ASCIIHex 압축 스트림, 객체 스트림(ObjStm)
- 한글 복원 순서: ToUnicode CMap → 한글 표준 CMap(UniKS-UCS2-H, KSCms-UHC-H 등)
  → ToUnicode 없는 Identity-H 폰트는 내장 TrueType 폰트의 cmap 표
- 페이지 순서, 상속 Resources, Form XObject 안의 텍스트

한계: 스캔 이미지 PDF, 암호화된 PDF, DRM으로 잠긴 파일, 내장되지 않은 Identity 폰트는 글자를 복원할 수 없습니다.

직접 확인: python -m parsers.pdf_text <파일.pdf>
"""

import base64
import re
import struct
import sys
import zlib
from typing import Dict, List, Optional, Tuple

WHITESPACE = b" \t\r\n\x00\x0c"
DELIMITERS = b"()<>[]{}/%"


class Ref:
    __slots__ = ("num",)

    def __init__(self, num: int):
        self.num = num


class Name(str):
    """'/F1' 같은 이름 객체 (슬래시 제외)."""


class Op(str):
    """콘텐츠 스트림 연산자 (Tj, Tf, BT 등)."""


class PDFTextError(Exception):
    pass


# ---------------------------------------------------------------------------
# 기본 객체 파서
# ---------------------------------------------------------------------------

class _Lexer:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def skip_ws(self) -> None:
        d, n = self.data, len(self.data)
        while self.pos < n:
            c = d[self.pos]
            if c in WHITESPACE:
                self.pos += 1
            elif c == 0x25:  # % 주석
                while self.pos < n and d[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def read_token(self) -> bytes:
        self.skip_ws()
        d, start = self.data, self.pos
        while self.pos < len(d) and d[self.pos] not in WHITESPACE and d[self.pos] not in DELIMITERS:
            self.pos += 1
        return d[start:self.pos]

    def parse(self):
        self.skip_ws()
        d = self.data
        if self.pos >= len(d):
            return None
        c = d[self.pos:self.pos + 1]
        if c == b"/":
            self.pos += 1
            return Name(self._read_name())
        if c == b"(":
            return self._read_literal_string()
        if c == b"<":
            if d[self.pos:self.pos + 2] == b"<<":
                return self._read_dict()
            return self._read_hex_string()
        if c == b"[":
            self.pos += 1
            items = []
            while True:
                self.skip_ws()
                if self.pos >= len(d) or d[self.pos:self.pos + 1] == b"]":
                    self.pos += 1
                    return items
                items.append(self.parse())
        tok = self.read_token()
        if not tok:
            self.pos += 1
            return None
        if tok == b"true":
            return True
        if tok == b"false":
            return False
        if tok == b"null":
            return None
        if re.fullmatch(rb"[+-]?\d+", tok):
            # 'N G R' 간접 참조 확인
            save = self.pos
            tok2 = self.read_token()
            if re.fullmatch(rb"\d+", tok2 or b""):
                tok3 = self.read_token()
                if tok3 == b"R":
                    return Ref(int(tok))
            self.pos = save
            return int(tok)
        try:
            return float(tok)
        except ValueError:
            return Op(tok.decode("latin-1"))

    def _read_name(self) -> str:
        d, start = self.data, self.pos
        while self.pos < len(d) and d[self.pos] not in WHITESPACE and d[self.pos] not in DELIMITERS:
            self.pos += 1
        raw = d[start:self.pos]
        raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
        return raw.decode("latin-1")

    def _read_dict(self) -> dict:
        self.pos += 2
        result = {}
        while True:
            self.skip_ws()
            if self.data[self.pos:self.pos + 2] == b">>" or self.pos >= len(self.data):
                self.pos += 2
                return result
            key = self.parse()
            value = self.parse()
            if isinstance(key, Name):
                result[str(key)] = value

    def _read_hex_string(self) -> bytes:
        end = self.data.index(b">", self.pos)
        hexstr = re.sub(rb"\s", b"", self.data[self.pos + 1:end])
        self.pos = end + 1
        if len(hexstr) % 2:
            hexstr += b"0"
        return bytes.fromhex(hexstr.decode("ascii"))

    def _read_literal_string(self) -> bytes:
        d = self.data
        self.pos += 1
        depth, out = 1, bytearray()
        while self.pos < len(d):
            c = d[self.pos]
            self.pos += 1
            if c == 0x5C:  # 백슬래시
                nxt = d[self.pos:self.pos + 1]
                self.pos += 1
                mapping = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12, b"(": 40, b")": 41, b"\\": 92}
                if nxt in mapping:
                    out.append(mapping[nxt])
                elif nxt in (b"\r", b"\n"):
                    if nxt == b"\r" and d[self.pos:self.pos + 1] == b"\n":
                        self.pos += 1
                elif nxt.isdigit():
                    digits = nxt
                    while len(digits) < 3 and d[self.pos:self.pos + 1].isdigit():
                        digits += d[self.pos:self.pos + 1]
                        self.pos += 1
                    out.append(int(digits, 8) & 0xFF)
                else:
                    out += nxt
            elif c == 0x28:
                depth += 1
                out.append(c)
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    break
                out.append(c)
            else:
                out.append(c)
        return bytes(out)


# ---------------------------------------------------------------------------
# 문서 구조
# ---------------------------------------------------------------------------

class _Document:
    OBJ_RE = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")

    def __init__(self, data: bytes):
        self.data = data
        self.objects: Dict[int, object] = {}
        self.streams: Dict[int, bytes] = {}
        # trailer 사전은 객체가 아니므로 원본 바이트에서 직접 확인
        if re.search(rb"/Encrypt\s+\d+\s+\d+\s+R", data) or re.search(rb"/Encrypt\s*<<", data):
            raise PDFTextError("암호화된 PDF는 지원하지 않습니다 (보안 설정이 걸린 PDF)")
        self._index_objects()

    def _index_objects(self) -> None:
        stream_starts: Dict[int, int] = {}
        for m in self.OBJ_RE.finditer(self.data):
            num = int(m.group(1))
            lex = _Lexer(self.data, m.end())
            try:
                obj = lex.parse()
            except Exception:  # 손상된 객체나 스트림 내부의 우연한 'obj' 문자열은 무시
                continue
            self.objects[num] = obj
            if isinstance(obj, dict):
                lex.skip_ws()
                if self.data[lex.pos:lex.pos + 6] == b"stream":
                    start = lex.pos + 6
                    if self.data[start:start + 2] == b"\r\n":
                        start += 2
                    elif self.data[start:start + 1] in (b"\n", b"\r"):
                        start += 1
                    stream_starts[num] = start
        # 간접 참조 Length('12 0 R')를 풀 수 있도록 모든 객체를 읽은 뒤 스트림을 잘라냄
        for num, start in stream_starts.items():
            self.streams[num] = self._raw_stream(self.objects[num], start)
        # 객체 스트림 안의 객체 풀기 (PDF 1.5+)
        for num, obj in list(self.objects.items()):
            if isinstance(obj, dict) and obj.get("Type") == "ObjStm" and num in self.streams:
                self._unpack_objstm(obj, self.decoded_stream(num))

    def _raw_stream(self, header: dict, start: int) -> bytes:
        length = self.resolve(header.get("Length"))
        if isinstance(length, int) and self.data[start + length:start + length + 20].lstrip().startswith(b"endstream"):
            return self.data[start:start + length]
        end = self.data.find(b"endstream", start)
        raw = self.data[start:end if end != -1 else len(self.data)]
        return raw.rstrip(b"\r\n")

    def _unpack_objstm(self, header: dict, data: bytes) -> None:
        n, first = int(header.get("N", 0)), int(header.get("First", 0))
        lex = _Lexer(data[:first])
        pairs = []
        for _ in range(n):
            num, off = lex.parse(), lex.parse()
            if isinstance(num, int) and isinstance(off, int):
                pairs.append((num, off))
        for num, off in pairs:
            if num not in self.objects:
                try:
                    self.objects[num] = _Lexer(data, first + off).parse()
                except (ValueError, IndexError):
                    pass

    def resolve(self, value):
        seen = 0
        while isinstance(value, Ref) and seen < 20:
            value = self.objects.get(value.num)
            seen += 1
        return value

    def decoded_stream(self, num: int) -> bytes:
        raw = self.streams.get(num, b"")
        header = self.objects.get(num) or {}
        filters = self.resolve(header.get("Filter")) if isinstance(header, dict) else None
        if filters is None:
            return raw
        if not isinstance(filters, list):
            filters = [filters]
        data = raw
        for f in filters:
            f = str(self.resolve(f))
            if f in ("FlateDecode", "Fl"):
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompressobj().decompress(data)
            elif f in ("ASCII85Decode", "A85"):
                data = base64.a85decode(data.strip().rstrip(b"~>").lstrip(b"<~") + b"~>", adobe=True)
            elif f in ("ASCIIHexDecode", "AHx"):
                data = bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", data).decode("ascii"))
            else:
                return b""  # 이미지 등 텍스트와 무관한 압축
        return data

    def stream_of(self, value) -> bytes:
        if isinstance(value, Ref):
            return self.decoded_stream(value.num)
        return b""

    def pages(self) -> List[Tuple[dict, dict]]:
        """(페이지 dict, 상속 반영 Resources) 목록을 문서 순서대로."""
        root = None
        for obj in self.objects.values():
            if isinstance(obj, dict) and obj.get("Type") == "Catalog":
                root = obj
                break
        result: List[Tuple[dict, dict]] = []

        def walk(node_ref, inherited_res, depth=0):
            node = self.resolve(node_ref)
            if not isinstance(node, dict) or depth > 50:
                return
            res = self.resolve(node.get("Resources")) or inherited_res
            if node.get("Type") == "Pages" or "Kids" in node:
                for kid in self.resolve(node.get("Kids")) or []:
                    walk(kid, res, depth + 1)
            else:
                result.append((node, res or {}))

        if root is not None:
            walk(root.get("Pages"), {})
        if not result:  # 카탈로그를 못 찾은 경우 객체 번호 순서로 대체
            for num in sorted(self.objects):
                obj = self.objects[num]
                if isinstance(obj, dict) and obj.get("Type") == "Page":
                    result.append((obj, self.resolve(obj.get("Resources")) or {}))
        return result


# ---------------------------------------------------------------------------
# 폰트 → 유니코드
# ---------------------------------------------------------------------------

# ToUnicode 없이 문자 코드 자체가 한글 인코딩인 표준 CMap (비내장 한글 폰트에서 사용)
PREDEFINED_KOREAN_CMAPS = {
    "UniKS-UCS2-H": "utf-16-be", "UniKS-UCS2-V": "utf-16-be",
    "UniKS-UTF16-H": "utf-16-be", "UniKS-UTF16-V": "utf-16-be",
    "KSCms-UHC-H": "cp949", "KSCms-UHC-V": "cp949", "KSCms-UHC-HW-H": "cp949", "KSCms-UHC-HW-V": "cp949",
    "KSCpc-EUC-H": "cp949", "KSCpc-EUC-V": "cp949",
    "KSC-EUC-H": "euc-kr", "KSC-EUC-V": "euc-kr",
}


class _Font:
    def __init__(self, doc: _Document, font_dict: dict):
        self.name = str(font_dict.get("BaseFont") or "")
        self.two_byte = font_dict.get("Subtype") == "Type0"
        self.cmap: Dict[int, str] = {}
        self.codec: Optional[str] = None
        self.source = "없음"  # 점검 도구 표시용: 글자를 어떻게 복원했는지
        self._choose_decoder(doc, font_dict)
        self._load_widths(doc, font_dict)

    def _choose_decoder(self, doc: _Document, font_dict: dict) -> None:
        to_unicode = font_dict.get("ToUnicode")
        if isinstance(to_unicode, Ref):
            self._parse_cmap(doc.decoded_stream(to_unicode.num))
        if self.cmap:
            self.source = "ToUnicode"
            return
        encoding = doc.resolve(font_dict.get("Encoding"))
        if isinstance(encoding, Name) and str(encoding) in PREDEFINED_KOREAN_CMAPS:
            self.codec = PREDEFINED_KOREAN_CMAPS[str(encoding)]
            self.source = f"표준 CMap({encoding})"
        elif self.two_byte and encoding in ("Identity-H", "Identity-V"):
            # ToUnicode가 없는 Identity 폰트: 내장 TrueType 폰트의 cmap 표를 거꾸로 찾아 복원
            self.cmap = _embedded_truetype_cmap(doc, font_dict)
            if self.cmap:
                self.source = "내장 폰트 cmap"
        elif not self.two_byte:
            self.source = "기본 인코딩"

    def _load_widths(self, doc: _Document, font_dict: dict) -> None:
        """글자 폭(1/1000 em). 칸 사이 빈 공간을 공백으로 바꾸는 데 사용."""
        self.widths: Dict[int, float] = {}
        self.default_width: Optional[float] = None
        if self.two_byte:
            descendants = doc.resolve(font_dict.get("DescendantFonts")) or []
            cid_font = doc.resolve(descendants[0]) if descendants else None
            if not isinstance(cid_font, dict) or self.codec:
                return  # 표준 CMap은 문자 코드 ≠ CID라서 폭 표를 쓸 수 없음
            self.default_width = _num(doc.resolve(cid_font.get("DW"))) or 1000.0
            w = doc.resolve(cid_font.get("W")) or []
            i = 0
            while i < len(w):
                first = doc.resolve(w[i])
                nxt = doc.resolve(w[i + 1]) if i + 1 < len(w) else None
                if isinstance(nxt, list):
                    for k, width in enumerate(nxt):
                        self.widths[int(first) + k] = _num(doc.resolve(width))
                    i += 2
                elif i + 2 < len(w):
                    for code in range(int(first), min(int(nxt), int(first) + 65536) + 1):
                        self.widths[code] = _num(doc.resolve(w[i + 2]))
                    i += 3
                else:
                    break
        else:
            first = doc.resolve(font_dict.get("FirstChar"))
            widths = doc.resolve(font_dict.get("Widths"))
            if isinstance(first, int) and isinstance(widths, list):
                for k, width in enumerate(widths):
                    self.widths[first + k] = _num(doc.resolve(width))

    def advance(self, raw: bytes) -> Optional[Tuple[float, int]]:
        """(글자 폭 합계 em, 글자 수). 폭을 모르면 None."""
        if self.codec:
            return None
        codes = self._codes(raw)
        total = 0.0
        for c in codes:
            width = self.widths.get(c, self.default_width)
            if width is None:
                return None
            total += width
        return total / 1000.0, len(codes)

    def _codes(self, raw: bytes) -> List[int]:
        if self.two_byte:
            return [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)]
        return list(raw)

    def _parse_cmap(self, data: bytes) -> None:
        text = data.decode("latin-1")
        space = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", text)
        if space:
            self.two_byte = len(space.group(1)) >= 4
        for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
            for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
                self.cmap[int(src, 16)] = _utf16(dst)
        for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
            for lo, hi, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
                lo_i, hi_i = int(lo, 16), int(hi, 16)
                if rest.startswith("["):
                    for i, dst in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", rest)):
                        self.cmap[lo_i + i] = _utf16(dst)
                else:
                    base = rest.strip("<>")
                    start = int(base, 16) if base else 0
                    width = max(4, len(base))
                    for i in range(min(hi_i - lo_i + 1, 65536)):
                        self.cmap[lo_i + i] = _utf16(f"{start + i:0{width}X}")

    def decode(self, raw: bytes) -> str:
        if self.codec:
            return raw.decode(self.codec, errors="ignore")
        codes = self._codes(raw)
        if self.cmap:
            return "".join(self.cmap.get(c, "") for c in codes)
        if self.two_byte:
            return ""  # ToUnicode 없는 CID 폰트는 복원 불가
        return raw.decode("cp1252", errors="ignore")


def _embedded_truetype_cmap(doc: _Document, font_dict: dict) -> Dict[int, str]:
    """Type0 + CIDFontType2 + 내장 TrueType: 문자 코드(CID) → GID → 유니코드 표."""
    descendants = doc.resolve(font_dict.get("DescendantFonts")) or []
    cid_font = doc.resolve(descendants[0]) if descendants else None
    if not isinstance(cid_font, dict) or cid_font.get("Subtype") != "CIDFontType2":
        return {}
    descriptor = doc.resolve(cid_font.get("FontDescriptor")) or {}
    font_file = descriptor.get("FontFile2") if isinstance(descriptor, dict) else None
    if not isinstance(font_file, Ref):
        return {}
    try:
        gid_to_unicode = _truetype_gid_to_unicode(doc.decoded_stream(font_file.num))
    except (struct.error, IndexError, ValueError):
        return {}
    cid_to_gid = doc.resolve(cid_font.get("CIDToGIDMap"))
    if isinstance(cid_to_gid, Ref) or (cid_to_gid is not None and cid_to_gid != "Identity"):
        table = doc.stream_of(cid_font.get("CIDToGIDMap"))
        result = {}
        for cid in range(len(table) // 2):
            gid = int.from_bytes(table[cid * 2:cid * 2 + 2], "big")
            if gid in gid_to_unicode:
                result[cid] = gid_to_unicode[gid]
        return result
    return gid_to_unicode  # CIDToGIDMap Identity: CID == GID


def _truetype_gid_to_unicode(font: bytes) -> Dict[int, str]:
    """TrueType 'cmap' 표(형식 4·12, 유니코드 계열)를 뒤집어 GID → 글자 표를 만듦."""
    num_tables = struct.unpack(">H", font[4:6])[0]
    cmap_offset = None
    for i in range(num_tables):
        tag, _, offset, _ = struct.unpack(">4sIII", font[12 + 16 * i:28 + 16 * i])
        if tag == b"cmap":
            cmap_offset = offset
    if cmap_offset is None:
        return {}
    result: Dict[int, str] = {}
    _, n_sub = struct.unpack(">HH", font[cmap_offset:cmap_offset + 4])
    for i in range(n_sub):
        platform, encoding, sub_off = struct.unpack(">HHI", font[cmap_offset + 4 + 8 * i:cmap_offset + 12 + 8 * i])
        if (platform, encoding) not in ((3, 1), (3, 10), (0, 3), (0, 4)):
            continue  # 기호(3,0)·매킨토시 표는 유니코드가 아님
        base = cmap_offset + sub_off
        fmt = struct.unpack(">H", font[base:base + 2])[0]
        if fmt == 4:
            seg_x2 = struct.unpack(">H", font[base + 6:base + 8])[0]
            ends = base + 14
            starts = ends + seg_x2 + 2
            deltas = starts + seg_x2
            range_offs = deltas + seg_x2
            for s in range(seg_x2 // 2):
                end, start = (struct.unpack(">H", font[p + 2 * s:p + 2 * s + 2])[0] for p in (ends, starts))
                delta = struct.unpack(">h", font[deltas + 2 * s:deltas + 2 * s + 2])[0]
                ro_pos = range_offs + 2 * s
                ro = struct.unpack(">H", font[ro_pos:ro_pos + 2])[0]
                for code in range(start, min(end, 0xFFFE) + 1):
                    if ro == 0:
                        gid = (code + delta) & 0xFFFF
                    else:
                        p = ro_pos + ro + 2 * (code - start)
                        gid = struct.unpack(">H", font[p:p + 2])[0]
                        gid = (gid + delta) & 0xFFFF if gid else 0
                    if gid:
                        result.setdefault(gid, chr(code))
        elif fmt == 12:
            n_groups = struct.unpack(">I", font[base + 12:base + 16])[0]
            for g in range(n_groups):
                start, end, gid0 = struct.unpack(">III", font[base + 16 + 12 * g:base + 28 + 12 * g])
                for code in range(start, min(end, 0x10FFFF) + 1):
                    result.setdefault(gid0 + code - start, chr(code))
    return result


def _utf16(hexstr: str) -> str:
    if not hexstr:
        return ""
    if len(hexstr) % 4:
        hexstr = hexstr.rjust(len(hexstr) + (4 - len(hexstr) % 4), "0")
    try:
        return bytes.fromhex(hexstr).decode("utf-16-be", errors="ignore")
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# 콘텐츠 스트림 → 텍스트
# ---------------------------------------------------------------------------

class _TextCollector:
    def __init__(self, doc: _Document):
        self.doc = doc
        self.lines: List[str] = []
        self.current: List[str] = []
        self.font_cache: Dict[int, _Font] = {}

    def newline(self) -> None:
        line = re.sub(r"[ \xa0　]+", " ", "".join(self.current)).strip()
        if line:
            self.lines.append(line)
        self.current = []

    def _font(self, resources: dict, name: str) -> Optional[_Font]:
        fonts = self.doc.resolve(resources.get("Font")) or {}
        ref = fonts.get(name)
        key = ref.num if isinstance(ref, Ref) else id(ref)
        if key not in self.font_cache:
            font_dict = self.doc.resolve(ref)
            if not isinstance(font_dict, dict):
                return None
            self.font_cache[key] = _Font(self.doc, font_dict)
        return self.font_cache[key]

    def run(self, content: bytes, resources: dict, depth: int = 0) -> None:
        lex = _Lexer(content)
        operands: list = []
        font: Optional[_Font] = None
        font_size = 10.0
        char_spacing = 0.0
        h_scale = 1.0
        last_y: Optional[float] = None
        # 텍스트 공간 좌표: line_x는 현재 줄(Td 기준점)의 x, pen_x는 마지막 글자가 끝난 x (폭을 모르면 None)
        line_x = 0.0
        pen_x: Optional[float] = None

        def move_to(new_x: float) -> None:
            """같은 줄 안에서 글자 끝보다 크게 떨어진 위치로 가면 칸이 나뉜 것으로 보고 공백 삽입."""
            nonlocal line_x, pen_x
            if self.current:
                if pen_x is not None:
                    gap = new_x - pen_x > font_size * 0.3
                else:
                    gap = new_x - line_x > font_size * 1.3
                if gap:
                    self.current.append(" ")
            line_x = pen_x = new_x

        def show(raw: bytes) -> None:
            nonlocal pen_x
            self.current.append(font.decode(raw))
            adv = font.advance(raw)
            if pen_x is not None and adv is not None:
                pen_x += (adv[0] * font_size + char_spacing * adv[1]) * h_scale
            else:
                pen_x = None

        while True:
            lex.skip_ws()
            if lex.pos >= len(content):
                break
            if content[lex.pos:lex.pos + 2] == b"BI" and content[lex.pos + 2:lex.pos + 3] in WHITESPACE:
                end = content.find(b"EI", lex.pos + 2)  # 인라인 이미지 건너뛰기
                lex.pos = end + 2 if end != -1 else len(content)
                continue
            try:
                tok = lex.parse()
            except Exception:
                break
            if not isinstance(tok, Op):
                operands.append(tok)
                continue
            op = str(tok)
            if op == "Tf" and len(operands) >= 2 and isinstance(operands[-2], Name):
                font = self._font(resources, str(operands[-2]))
                font_size = abs(_num(operands[-1])) or font_size
            elif op in ("Tj", "'", '"') and operands and isinstance(operands[-1], bytes) and font:
                if op != "Tj":
                    self.newline()
                    pen_x = line_x
                show(operands[-1])
            elif op == "TJ" and operands and isinstance(operands[-1], list) and font:
                for part in operands[-1]:
                    if isinstance(part, bytes):
                        show(part)
                    elif isinstance(part, (int, float)):
                        if part < -250:
                            self.current.append(" ")
                        if pen_x is not None:
                            pen_x -= part / 1000.0 * font_size * h_scale
            elif op == "Tc" and operands:
                char_spacing = _num(operands[-1])
            elif op == "Tz" and operands:
                h_scale = _num(operands[-1]) / 100.0 or 1.0
            elif op in ("Td", "TD") and len(operands) >= 2:
                tx, ty = _num(operands[-2]), _num(operands[-1])
                if ty != 0:
                    self.newline()
                    if last_y is not None:
                        last_y += ty
                    line_x += tx
                    pen_x = line_x
                else:
                    move_to(line_x + tx)
            elif op == "Tm" and len(operands) >= 6:
                a = _num(operands[-6]) or 1.0
                x, y = _num(operands[-2]) / a, _num(operands[-1])
                if last_y is None or abs(y - last_y) > 1:
                    self.newline()
                    line_x = pen_x = x
                else:
                    move_to(x)  # 같은 줄에서 새 위치 (표의 칸 등)
                last_y = y
            elif op == "T*":
                self.newline()
                pen_x = line_x
            elif op == "ET":
                pass
            elif op == "Do" and operands and isinstance(operands[-1], Name) and depth < 5:
                xobjs = self.doc.resolve(resources.get("XObject")) or {}
                ref = xobjs.get(str(operands[-1]))
                xobj = self.doc.resolve(ref)
                if isinstance(xobj, dict) and xobj.get("Subtype") == "Form" and isinstance(ref, Ref):
                    sub_res = self.doc.resolve(xobj.get("Resources")) or resources
                    self.run(self.doc.decoded_stream(ref.num), sub_res, depth + 1)
            operands = []


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------

def extract_pdf_text(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read()
    return extract_pdf_text_from_bytes(data)


def extract_pdf_text_from_bytes(data: bytes) -> str:
    return "\n".join(_collect(data)[1].lines)


def _collect(data: bytes) -> Tuple[_Document, "_TextCollector", int]:
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise PDFTextError("PDF 파일이 아닙니다 (보안 프로그램(DRM)으로 잠긴 파일일 수 있음)")
    doc = _Document(data)
    collector = _TextCollector(doc)
    pages = doc.pages()
    for page, resources in pages:
        contents = page.get("Contents")
        refs = doc.resolve(contents)
        if isinstance(contents, Ref) and not isinstance(refs, list):
            refs = [contents]
        content = b"\n".join(doc.stream_of(r) for r in (refs or []))
        collector.run(content, resources)
        collector.newline()
    return doc, collector, len(pages)


def describe_pdf(data: bytes) -> dict:
    """점검 도구용 요약: 본문 내용 없이 PDF 구조와 글자 복원 결과만 돌려줌."""
    info: dict = {"문제": []}
    version = re.match(rb"\s*%PDF-(\d\.\d)", data)
    info["버전"] = version.group(1).decode() if version else "?"
    producer = re.search(rb"/Producer\s*\(((?:\\.|[^\\)])*)\)", data)
    if producer:
        raw = _Lexer(data, producer.start(1) - 1)._read_literal_string()
        info["생성 프로그램"] = raw.decode("utf-16", "ignore") if raw[:2] in (b"\xfe\xff", b"\xff\xfe") else raw.decode("latin-1")
    try:
        _, collector, pages = _collect(data)
    except PDFTextError as e:
        info["문제"].append(str(e))
        return info
    text = "\n".join(collector.lines)
    letters = [c for c in text if not c.isspace()]
    hangul = sum(1 for c in letters if "가" <= c <= "힣")
    info.update({
        "쪽수": pages,
        "글자 수": len(letters),
        "한글 비율": round(hangul / len(letters), 2) if letters else 0.0,
        "폰트": sorted({f"{f.name} → {f.source}" for f in collector.font_cache.values()}),
    })
    if not letters:
        info["문제"].append("글자가 없습니다 (스캔 이미지 PDF일 수 있음)")
    if any(f.source == "없음" for f in collector.font_cache.values()):
        info["문제"].append("글자를 복원할 수 없는 폰트가 있습니다")
    if len(letters) >= 50 and hangul / len(letters) < 0.2:
        info["문제"].append("한글이 거의 없습니다 (글자가 깨졌을 수 있음)")
    return info


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("사용법: python -m parsers.pdf_text <파일.pdf>")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print(extract_pdf_text(sys.argv[1]))
