"""결재시행 목록 화면 표 읽기: 화면 식이 돌려준 글자 해석 (실제 창 없이)."""

import unittest

from collectors.workmgmt_window import (
    DIAG_JS,
    LIST_JS,
    ListWindowResult,
    format_diag,
    mask,
    parse_list_js_result,
    read_list_windows,
)


class ParseListJsResultTest(unittest.TestCase):
    def test_parse_list_js_result(self):
        raw = "LIST:제목\x1f처리구분\x1e가상 공문\x1f업무담당자"
        self.assertEqual(parse_list_js_result(raw), (["제목", "처리구분"], [["가상 공문", "업무담당자"]]))

    def test_header_only(self):
        self.assertEqual(parse_list_js_result("LIST:제목\x1f처리구분"), (["제목", "처리구분"], []))

    def test_nolist_and_none(self):
        self.assertEqual(parse_list_js_result("NOLIST"), ([], []))
        self.assertEqual(parse_list_js_result(None), ([], []))
        self.assertEqual(parse_list_js_result("이상한 값"), ([], []))

    def test_list_js_is_es3(self):
        for banned in ("=>", "let ", "const ", "JSON.", ".map(", ".forEach(", "`", ".trim("):
            self.assertNotIn(banned, LIST_JS)

    def test_list_js_looks_for_role_and_title_headers(self):
        self.assertIn("처리구분", LIST_JS)
        self.assertIn("제목", LIST_JS)

    def test_result_type(self):
        r = ListWindowResult(1, "창", [])
        self.assertEqual(r.rows, [])

    def test_read_list_windows_returns_list(self):
        self.assertIsInstance(read_list_windows("NoSuchWindowClass_xyz"), list)



class CheckListTest(unittest.TestCase):
    """내부망 점검 5차: 결재시행 목록 표 모양 확인 (글자 모양만 남김, 머리글 이름은 그대로)."""

    def test_mask(self):
        self.assertEqual(mask("계약부-901 Ab"), "가가가-### aa")

    def test_format_diag_keeps_short_header_names(self):
        US, RS, FS, GS = "", "", "", ""
        table1 = FS.join(["main/list", "3", RS.join([
            US.join(["상태", "처리구분", "제목", "가상 안내 문구입니다 길게"]),
            US.join(["미결", "계약부-901"]),
        ])])
        table2 = FS.join(["main/menu", "0", ""])
        table3 = FS.join(["main/body", "2", US.join(["미결", "계약부-901", "가상감사처", "업무담당자", "홍길자"])])
        out = format_diag(GS.join([table1, table2, table3]))
        self.assertIn("가가 | 가가가-### | 가가가가가 | 가가가가가 | 가가가", out)  # 머리글 낱말이 아닌 첫 행 칸도 모양만
        self.assertIn("main/list 행 3", out)
        self.assertIn("상태 | 처리구분 | 제목 | 가가 가가 가가가가가 가가", out)  # 머리글 낱말이 아닌 칸은 모양만
        self.assertIn("가가 | 가가가-###", out)  # 데이터 행은 모두 모양만
        self.assertIn("입안자 | 수신일시 | 문서번호", format_diag(FS.join(["m", "1", US.join(["입안자", "수신일시", "문서번호"])])))
        self.assertIn("main/menu 행 0", out)
        self.assertNotIn("계약부", out)

    def test_format_diag_masks_data_row_of_split_table(self):  # 리뷰 지적 7
        US, FS = "", ""
        data_first = FS.join(["main/body", "2", US.join(["미결", "직원 교육 현황", "인사부서", "2026-11-04 09:12"])])
        out = format_diag(data_first)
        self.assertNotIn("직원 교육 현황", out)
        self.assertNotIn("인사부서", out)

    def test_header_row_needs_three_header_words(self):
        US, FS = "", ""
        out = format_diag(FS.join(["m", "1", US.join(["상태", "문서종류", "제목", "처리구분"])]))
        self.assertIn("상태 | 문서종류 | 제목 | 처리구분", out)
        out = format_diag(FS.join(["m", "1", US.join(["교육현황", "참석"])]))
        self.assertNotIn("교육현황", out)

    def test_header_list_is_filtered(self):
        from collectors.workmgmt_window import safe_headers

        self.assertEqual(safe_headers(["제목", "처리구분", "홍길동 과장"]), ["제목", "처리구분", "가가가 가가"])

    def test_list_js_header_uses_text_not_tooltip(self):  # 리뷰 지적 6: 머리글 칸 이름은 title 속성이 아니라 글자
        self.assertIn("headCells(", LIST_JS)

    def test_diag_js_is_es3(self):
        for banned in ("=>", "let ", "const ", "JSON.", ".map(", ".forEach(", "`", ".trim("):
            self.assertNotIn(banned, DIAG_JS)


if __name__ == "__main__":
    unittest.main()
