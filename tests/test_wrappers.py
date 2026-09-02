# -*- coding: utf-8 -*-
"""A3: LLM 出力の包み (<think> / コードフェンス / 前置き) の剥がし。"""

import unittest

import _path

from h3_prompt_toolkit import ref2va
from h3_prompt_toolkit.timeline import Timeline
from h3_prompt_toolkit.substitute import substitute
from h3_prompt_toolkit.validate import validate


BODY = _path.fixture("ref2va_good.txt").strip()


def demo_tl():
    return Timeline.from_json(_path.fixture("timeline_demo.json"))


def levels(findings, check):
    return [x.level for x in findings if x.check == check]


class TestStripWrappers(unittest.TestCase):
    def test_closed_think_block(self):
        text = "<think>\nLet me plan the shot.\n</think>\n\n" + BODY
        self.assertEqual(ref2va.strip_wrappers(text), BODY)

    def test_unclosed_think_block_drops_tail(self):
        # 途中で切れた出力: 開いたまま終わるので本文は無い
        self.assertEqual(ref2va.strip_wrappers("<think>thinking and cut off"), "")

    def test_code_fence(self):
        self.assertEqual(ref2va.strip_wrappers("```\n" + BODY + "\n```"), BODY)

    def test_code_fence_with_language(self):
        self.assertEqual(ref2va.strip_wrappers("```text\n" + BODY + "\n```"), BODY)

    def test_unclosed_code_fence(self):
        self.assertEqual(ref2va.strip_wrappers("```\n" + BODY), BODY)

    def test_preamble_before_first_field(self):
        text = ("Assumption: the audio carries its own soundtrack.\n"
                "Here is the prompt:\n\n" + BODY)
        self.assertEqual(ref2va.strip_wrappers(text), BODY)

    def test_all_three_at_once(self):
        text = ("<think>plan</think>\n```text\nAssumption: something.\n\n"
                + BODY + "\n```")
        self.assertEqual(ref2va.strip_wrappers(text), BODY)

    def test_clean_text_untouched(self):
        self.assertEqual(ref2va.strip_wrappers(BODY), BODY)

    def test_no_fields_left_alone(self):
        # フィールド見出しが無いテキストは前置き判定をしない
        self.assertEqual(ref2va.strip_wrappers("just a sentence"), "just a sentence")

    def test_has_wrappers(self):
        self.assertTrue(ref2va.has_wrappers("<think>x</think>" + BODY))
        self.assertTrue(ref2va.has_wrappers("```\n" + BODY))
        self.assertFalse(ref2va.has_wrappers(BODY))


class TestSubstituteStrips(unittest.TestCase):
    def test_wrapped_input_is_unwrapped(self):
        text = "<think>plan</think>\n```\n" + BODY + "\n```"
        res = substitute(text, demo_tl())
        self.assertTrue(res.ok, res.report_text())
        self.assertNotIn("<think>", res.text)
        self.assertNotIn("```", res.text)
        self.assertTrue(res.text.startswith("subject_definitions:"))
        self.assertIn("包み", res.report_text())

    def test_clean_input_keeps_trailing_newline(self):
        text = _path.fixture("ref2va_good.txt")
        res = substitute(text, demo_tl())
        self.assertTrue(res.ok)
        self.assertEqual(res.text, text)


class TestValidateReportsWrappers(unittest.TestCase):
    def test_think_block_is_error(self):
        findings = validate("<think>plan</think>\n" + BODY, demo_tl())
        self.assertIn("error", levels(findings, "wrappers"))

    def test_preamble_is_warning(self):
        findings = validate("Assumption: something.\n\n" + BODY, demo_tl())
        self.assertIn("warn", levels(findings, "wrappers"))

    def test_clean_output_has_none(self):
        findings = validate(BODY, demo_tl())
        self.assertEqual(levels(findings, "wrappers"), [])


if __name__ == "__main__":
    unittest.main()
