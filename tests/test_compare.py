# -*- coding: utf-8 -*-
import unittest

import _path

from h3_prompt_toolkit.timeline import Timeline
from h3_prompt_toolkit.compare import compare_outputs, render_table, render_details


def demo_tl():
    return Timeline.from_json(_path.fixture("timeline_demo.json"))


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.reports = compare_outputs(
            [("good", _path.fixture("ref2va_good.txt")),
             ("drifted", _path.fixture("ref2va_drifted.txt"))],
            demo_tl())

    def test_verdicts(self):
        good, drifted = self.reports
        self.assertEqual(good.n_errors, 0)
        self.assertGreater(drifted.n_errors, 0)
        self.assertEqual(good.verdicts["dialogue"], "ok")
        self.assertEqual(drifted.verdicts["dialogue"], "error")
        self.assertEqual(drifted.verdicts["soundscape"], "error")
        self.assertEqual(drifted.verdicts["ts_match"], "warn")

    def test_table(self):
        table = render_table(self.reports)
        self.assertIn("good", table)
        self.assertIn("drifted", table)
        self.assertIn("台詞の同一性", table)
        self.assertIn("NG", table)
        self.assertIn("OK", table)

    def test_details(self):
        details = render_details(self.reports)
        self.assertIn("── good", details)
        self.assertIn("問題は見つかりませんでした", details)

    def test_empty(self):
        self.assertIn("比較対象がありません", render_table([]))


if __name__ == "__main__":
    unittest.main()
