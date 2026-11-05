"""쪽지 표본 회귀 확인 스크립트 테스트 (가상 표본, CP949 txt)."""

import json
import os
import tempfile
import unittest

from scripts.ezq_sample_eval import compare, evaluate, label_of, summarize

TASK = "가상 보고서 11/7까지 제출 부탁드립니다. 첨부파일 확인 바랍니다."
NOTICE = "[안내] 이번 주 가상 행사가 열립니다. 많은 참여 바랍니다."


class TestSampleEval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        for name, body in (("01_업무.txt", TASK), ("02_공지.txt", NOTICE), ("03_문의.txt", "가상 자료 어디 있나요?")):
            with open(os.path.join(self.dir, name), "w", encoding="cp949") as f:
                f.write(body)

    def tearDown(self):
        self.tmp.cleanup()

    def test_label_of(self):
        self.assertEqual(label_of("12_가상_업무.txt"), "업무")
        self.assertEqual(label_of("noname.txt"), "")

    def test_evaluate_reads_cp949_and_judges_each_file(self):
        verdicts = evaluate(self.dir)
        self.assertEqual(sorted(verdicts), ["01_업무.txt", "02_공지.txt", "03_문의.txt"])
        self.assertEqual(verdicts["01_업무.txt"], "등록")
        self.assertEqual(verdicts["02_공지.txt"], "제외")

    def test_summarize_counts_only(self):
        lines = summarize(evaluate(self.dir))
        text = "\n".join(lines)
        self.assertIn("업무 1건: 등록 1", text)
        self.assertIn("업무 누락 0건", text)
        self.assertNotIn("가상 보고서", text)  # 본문은 출력하지 않음

    def test_compare_lists_changed_files_only(self):
        base = os.path.join(self.dir, "base.json")
        with open(base, "w", encoding="utf-8") as f:
            json.dump({"01_업무.txt": "등록", "02_공지.txt": "등록"}, f, ensure_ascii=False)
        self.assertEqual(compare(evaluate(self.dir), base), ["02_공지.txt: 등록 → 제외"])


if __name__ == "__main__":
    unittest.main()
