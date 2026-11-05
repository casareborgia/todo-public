"""PDF 생성 프로그램마다 다른 한글 저장 방식 대응 테스트.

실제 업무관리시스템 PDF는 Chrome이 아닌 다른 프로그램(한컴, 리포트 도구 등)이 만들 수 있으므로
대표적인 구조를 직접 만들어 확인합니다.
"""

import os
import re
import unittest
import zlib

from parsers.pdf_text import PDFTextError, describe_pdf, extract_pdf_text_from_bytes

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
LINES = ["제목 예산 집행 점검 결과 제출", "2026. 11. 6.(금)까지 회신 바랍니다."]


def _content(encoded_lines):
    ops = [b"BT /F1 12 Tf"]
    for i, raw in enumerate(encoded_lines):
        ops.append(b"1 0 0 1 50 %d Tm <%s> Tj" % (700 - 20 * i, raw.hex().encode()))
    ops.append(b"ET")
    return b"\n".join(ops)


def _build_pdf(objects, objstm=False):
    """objects: {번호: 사전 bytes 또는 (사전 bytes, 스트림 bytes)} → PDF bytes."""
    out = bytearray(b"%PDF-1.5\n")
    plain = {n: o for n, o in objects.items() if not isinstance(o, tuple)}
    if objstm:
        header, body = [], bytearray()
        for n, o in plain.items():
            header.append(b"%d %d" % (n, len(body)))
            body += o + b"\n"
        head = b" ".join(header) + b"\n"
        packed = zlib.compress(head + body)
        objects = {n: o for n, o in objects.items() if isinstance(o, tuple)}
        objects[90] = (b"<</Type/ObjStm/N %d/First %d/Filter/FlateDecode>>" % (len(plain), len(head)), packed)
        objects[91] = (b"<</Type/XRef/Size 92/Root 1 0 R/W[1 2 1]/Filter/FlateDecode>>", zlib.compress(b"\x00" * 8))
    for n, o in objects.items():
        if isinstance(o, tuple):
            d, stream = o
            d = d[:-2] + b"/Length %d>>" % len(stream)
            out += b"%d 0 obj\n%s\nstream\n%s\nendstream\nendobj\n" % (n, d, stream)
        else:
            out += b"%d 0 obj\n%s\nendobj\n" % (n, o)
    if not objstm:
        out += b"trailer\n<</Root 1 0 R>>\n"
    out += b"%%EOF\n"
    return bytes(out)


def _korean_font_pdf(encoding, codec, objstm=False):
    return _build_pdf({
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        4: b"<</Type/Font/Subtype/Type0/BaseFont/HYGoThic-Medium/Encoding/%s/DescendantFonts[6 0 R]>>" % encoding,
        6: b"<</Type/Font/Subtype/CIDFontType0/BaseFont/HYGoThic-Medium"
           b"/CIDSystemInfo<</Registry(Adobe)/Ordering(Korea1)/Supplement 2>>>>",
        5: (b"<<>>", _content([line.encode(codec) for line in LINES])),
    }, objstm=objstm)


def _per_glyph_pdf(runs):
    """글자마다 Td로 자리를 잡는 PDF (ezPDF 등 공문 PDF 방식). runs: [(앞 간격, 글자들)], 글자 폭 = 10."""
    chars = sorted({c for _, text in runs for c in text})
    code = {c: i + 1 for i, c in enumerate(chars)}
    cmap = b"begincmap\n1 begincodespacerange <0000> <FFFF> endcodespacerange\n%d beginbfchar\n" % len(chars)
    cmap += b"".join(b"<%04X> <%s>\n" % (code[c], c.encode("utf-16-be").hex().upper().encode()) for c in chars)
    cmap += b"endbfchar\nendcmap"
    ops, prev = [b"BT /F1 10 Tf 1 0 0 1 50 700 Tm"], None
    for gap, text in runs:
        for k, c in enumerate(text):
            if prev is not None:
                ops.append(b"%g 0 Td" % (10 + (gap if k == 0 else 0)))
            ops.append(b"<%04X> Tj" % code[c])
            prev = c
    ops.append(b"ET")
    return _build_pdf({
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        4: b"<</Type/Font/Subtype/Type0/BaseFont/Gulim/Encoding/Identity-H/DescendantFonts[6 0 R]/ToUnicode 7 0 R>>",
        6: b"<</Type/Font/Subtype/CIDFontType2/BaseFont/Gulim/DW 1000/W[1[%s]]>>" % b" ".join(b"1000" for _ in chars),
        5: (b"<<>>", b"\n".join(ops)),
        7: (b"<<>>", cmap),
    })


class TestPdfVariants(unittest.TestCase):
    def test_gap_between_table_cells_becomes_space(self):
        # '제'와 '목' 사이 14pt, 칸 사이 20pt 빈 공간 / 글자 사이 0 → 공백은 빈 공간에만
        data = _per_glyph_pdf([(0, "제"), (14, "목"), (20, "자금계획"), (0, "제출"), (20, "시행"), (20, "기획팀-3133")])
        self.assertEqual(extract_pdf_text_from_bytes(data), "제 목 자금계획제출 시행 기획팀-3133")

    def test_predefined_ucs2_cmap(self):
        text = extract_pdf_text_from_bytes(_korean_font_pdf(b"UniKS-UCS2-H", "utf-16-be"))
        self.assertEqual(text.splitlines(), LINES)

    def test_predefined_uhc_cmap(self):
        text = extract_pdf_text_from_bytes(_korean_font_pdf(b"KSCms-UHC-H", "cp949"))
        self.assertEqual(text.splitlines(), LINES)

    def test_object_stream_and_xref_stream(self):
        data = _korean_font_pdf(b"UniKS-UCS2-H", "utf-16-be", objstm=True)
        self.assertNotIn(b"/Type/Catalog", data)  # 카탈로그·폰트가 압축된 객체 스트림 안에 있음
        self.assertEqual(extract_pdf_text_from_bytes(data).splitlines(), LINES)

    def test_identity_font_without_tounicode_uses_embedded_cmap(self):
        with open(os.path.join(FIXTURES, "sample_gongmun.pdf"), "rb") as f:
            original = f.read()
        # 같은 길이의 공백으로 지워 다른 객체 위치는 그대로 유지
        stripped = re.sub(rb"/ToUnicode\s+\d+\s+0\s+R", lambda m: b" " * len(m.group(0)), original)
        self.assertNotIn(b"/ToUnicode", stripped)
        text = extract_pdf_text_from_bytes(stripped)
        self.assertIn("2026. 11. 6.(금)까지 회신하여 주시기 바랍니다.", text)
        self.assertIn("시행 감사실-1204 (2026. 10. 27.)", text)
        self.assertTrue(any("내장 폰트 cmap" in f for f in describe_pdf(stripped)["폰트"]))

    def test_encrypted_pdf_is_reported(self):
        data = _korean_font_pdf(b"UniKS-UCS2-H", "utf-16-be").replace(
            b"<</Root 1 0 R>>", b"<</Root 1 0 R/Encrypt 9 0 R>>")
        with self.assertRaises(PDFTextError):
            extract_pdf_text_from_bytes(data)
        self.assertIn("암호화", describe_pdf(data)["문제"][0])

    def test_non_pdf_is_reported(self):
        self.assertIn("DRM", describe_pdf(b"\x00\x01FASOO-DRM...")["문제"][0])

    def test_describe_normal_pdf(self):
        with open(os.path.join(FIXTURES, "sample_gongmun.pdf"), "rb") as f:
            info = describe_pdf(f.read())
        self.assertEqual(info["문제"], [])
        self.assertEqual(info["쪽수"], 1)
        self.assertGreater(info["한글 비율"], 0.5)
        self.assertTrue(all(f.endswith("ToUnicode") for f in info["폰트"]))


if __name__ == "__main__":
    unittest.main()
