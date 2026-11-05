"""월간 캘린더 칸에 보일 업무 개수·제목 자르기 시험."""

import unittest

from ui.calendar_fit import DEFAULT_SHOWN, chips_that_fit, fit_title

SLOT, MORE = 15, 13


class ChipsThatFitTest(unittest.TestCase):
    def test_all_fit(self):
        self.assertEqual(chips_that_fit(3, 58, SLOT, MORE), (3, 0))

    def test_exact_fit_without_more_line(self):
        self.assertEqual(chips_that_fit(4, 60, SLOT, MORE), (4, 0))

    def test_overflow_reserves_more_line(self):
        # 58px: 4개(60px)는 안 들어감 → '+N' 줄(13px) 빼고 45px → 3개 + 외 3건
        self.assertEqual(chips_that_fit(6, 58, SLOT, MORE), (3, 3))

    def test_bigger_cell_shows_more(self):
        small = chips_that_fit(10, 58, SLOT, MORE)[0]
        big = chips_that_fit(10, 99, SLOT, MORE)[0]
        self.assertGreater(big, small)
        self.assertEqual(big, 5)

    def test_tiny_cell_keeps_one(self):
        self.assertEqual(chips_that_fit(5, 10, SLOT, MORE), (1, 4))

    def test_unknown_size_uses_old_default(self):
        self.assertEqual(chips_that_fit(8, 0, SLOT, MORE), (DEFAULT_SHOWN, 8 - DEFAULT_SHOWN))
        self.assertEqual(chips_that_fit(2, 0, SLOT, MORE), (2, 0))

    def test_no_tasks(self):
        self.assertEqual(chips_that_fit(0, 58, SLOT, MORE), (0, 0))

    def test_never_shows_more_than_tasks(self):
        for n in range(1, 30):
            shown, hidden = chips_that_fit(n, 58, SLOT, MORE)
            self.assertEqual(shown + hidden, n)
            self.assertTrue(1 <= shown <= n)
            if hidden:
                self.assertLessEqual(shown * SLOT + MORE, 58)


def width(s):  # 글자당 10px로 가정
    return len(s) * 10


class FitTitleTest(unittest.TestCase):
    def test_short_title_kept(self):
        self.assertEqual(fit_title("보고서", 100, width), "보고서")

    def test_long_title_cut_with_ellipsis(self):
        got = fit_title("주간업무실적보고서제출", 60, width)
        self.assertEqual(got, "주간업무실…")
        self.assertLessEqual(width(got), 60)

    def test_keeps_at_least_one_char(self):
        self.assertEqual(fit_title("가나다라", 5, width), "가…")

    def test_unknown_width_keeps_text(self):
        self.assertEqual(fit_title("가나다라", 0, width), "가나다라")


if __name__ == "__main__":
    unittest.main()
