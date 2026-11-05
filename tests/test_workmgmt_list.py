"""업무관리시스템 결재시행 목록 행 해석 테스트 (가상 목록).

머리글은 매뉴얼 Ⅱ-8 '배부'의 결재시행 목록 설명(문서종류 '수신', 처리구분 '업무담당자/업무관리자')을 바탕으로 가정.
"""

import unittest

from parsers.workmgmt_list import ListRow, classify_row, distributed_rows, parse_list_table

H = ["상태", "문서종류", "제목", "문서번호", "입안자", "처리구분", "수신일시"]


def row(kind="수신", role="업무담당자", no="계약부-77", title="가상 공문 제목"):
    return ["미결", kind, title, no, "가상처", role, "2026-11-04 09:12"]


class ParseListTableTest(unittest.TestCase):
    def test_task_row(self):
        r = parse_list_table(H, [row()])
        self.assertEqual(r[0].doc_no, "계약부-77")
        self.assertEqual(r[0].sender_dept, "가상처")
        self.assertEqual(r[0].received_at, "2026-11-04 09:12")
        self.assertEqual(classify_row(r[0]), "task")

    def test_manager_row_is_review(self):
        self.assertEqual(classify_row(parse_list_table(H, [row(role="업무관리자")])[0]), "review")

    def test_non_received_ignored(self):
        self.assertIsNone(classify_row(parse_list_table(H, [row(kind="생산")])[0]))

    def test_empty_notice_row_ignored(self):
        self.assertEqual(parse_list_table(H, [["조회된 문서가 없습니다."]]), [])

    def test_missing_title_column(self):
        self.assertEqual(parse_list_table(["상태", "문서종류"], [["미결", "수신"]]), [])

    def test_header_spacing_and_alias(self):
        hs = ["상 태", "문서종류", "문서제목", "접수번호", "발송부서", "처리구분", "접수일시"]
        r = parse_list_table(hs, [row()])
        self.assertEqual(r[0].title, "가상 공문 제목")
        self.assertEqual(r[0].doc_no, "계약부-77")
        self.assertEqual(r[0].received_at, "2026-11-04 09:12")

    def test_same_doc_two_roles_keeps_task(self):
        rows = parse_list_table(H, [row(role="업무관리자"), row(role="업무담당자")])
        self.assertEqual([k for _, k in distributed_rows(rows)], ["task"])

    def test_distributed_rows_skips_unclassified(self):
        rows = parse_list_table(H, [row(kind="생산"), row(no="계약부-78")])
        self.assertEqual([r.doc_no for r, _ in distributed_rows(rows)], ["계약부-78"])

    def test_no_doc_no_dedupes_by_title(self):
        rows = parse_list_table(H, [row(no="", role="업무관리자"), row(no="")])
        self.assertEqual(len(distributed_rows(rows)), 1)

    def test_list_row_is_named_tuple(self):
        r = ListRow("t", "n", "수신", "업무담당자", "d", "x")
        self.assertEqual(r.role, "업무담당자")


if __name__ == "__main__":
    unittest.main()
