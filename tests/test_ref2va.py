# -*- coding: utf-8 -*-
import unittest

import _path  # noqa: F401

from h3_prompt_toolkit import ref2va


class TestSections(unittest.TestCase):
    def test_plain(self):
        text = ("subject_definitions:\nA\n\nsummary:\nB\n\n"
                "retention_analysis:\nC\n\ndetailed_description:\nD\n\n"
                "overall_soundscape: N/A\n\nnon_diegetic_music: N/A\n")
        head, secs = ref2va.find_sections(text)
        self.assertEqual(head, "")
        self.assertEqual(sorted(secs), sorted(ref2va.REF_OUTPUT_FIELDS))
        self.assertEqual(secs["summary"].body(text), "B")
        self.assertEqual(secs["overall_soundscape"].body(text), "N/A")

    def test_decorated_labels(self):
        text = ("**subject_definitions:** A\n\n## Summary: B\n\n"
                "> retention_analysis: C\n\n- Detailed_Description: D\n\n"
                "overall_soundscape： N/A\n\nnon_diegetic_music: N/A\n")
        bodies = ref2va.section_bodies(text)
        self.assertEqual(bodies["summary"], "B")
        self.assertEqual(bodies["detailed_description"], "D")
        self.assertEqual(bodies["overall_soundscape"], "N/A")

    def test_missing_field(self):
        bodies = ref2va.section_bodies("summary: only this")
        self.assertEqual(bodies["summary"], "only this")
        self.assertEqual(bodies["detailed_description"], "")

    def test_head_kept(self):
        text = "前置きの行\nsummary: B"
        head, _ = ref2va.find_sections(text)
        self.assertEqual(head, "前置きの行")


class TestTsOccurrences(unittest.TestCase):
    def test_kinds(self):
        text = ("[Shot 1] style line.\n"
                "At 0:00.512, she speaks.\n"
                "[Shot 2] At 0:03.008, the camera cuts.\n"
                "The camera holds until 0:05.875.")
        occs = ref2va.ts_occurrences(text)
        kinds = [(o.kind, o.raw) for o in occs]
        self.assertEqual(kinds, [("at", "0:00.512"), ("shot", "0:03.008"),
                                 ("bare", "0:05.875")])
        self.assertEqual(occs[1].shot, 2)

    def test_dialogue_excluded(self):
        text = "At 0:01.000, S1 says, <d>[Japanese] 1:30に会おう</d>."
        occs = ref2va.ts_occurrences(text)
        self.assertEqual([o.raw for o in occs], ["0:01.000"])

    def test_aspect_ratio_not_matched(self):
        occs = ref2va.ts_occurrences("The frame is 16:9 wide.")
        self.assertEqual(occs, [])

    def test_strict_format(self):
        self.assertTrue(ref2va.TS_STRICT.match("0:00.512"))
        self.assertTrue(ref2va.TS_STRICT.match("10:59.999"))
        self.assertFalse(ref2va.TS_STRICT.match("0:03.5"))
        self.assertFalse(ref2va.TS_STRICT.match("0:03"))
        self.assertFalse(ref2va.TS_STRICT.match("0:73.000"))

    def test_loose_matches_missing_ms(self):
        occs = ref2va.ts_occurrences("At 0:03, something happens.")
        self.assertEqual([o.raw for o in occs], ["0:03"])


class TestDialogue(unittest.TestCase):
    def test_lang_and_speaker(self):
        text = ("At 0:00.512, she smiles. S1 says, "
                "<d>[Japanese] こんにちは。</d>. Later S2 says, "
                "<d>そうですね。</d>.")
        occs = ref2va.dialogue_occurrences(text)
        self.assertEqual(len(occs), 2)
        self.assertEqual(occs[0].lang, "Japanese")
        self.assertEqual(occs[0].spoken, "こんにちは。")
        self.assertEqual(occs[0].speaker, "S1")
        self.assertIsNone(occs[1].lang)
        self.assertEqual(occs[1].speaker, "S2")

    def test_shot_word_is_not_speaker(self):
        occs = ref2va.dialogue_occurrences("[Shot 1] someone says <d>[English] hi</d>")
        self.assertIsNone(occs[0].speaker)


class TestShotMarks(unittest.TestCase):
    def test_marks(self):
        text = ("[Shot 1] opening.\n"
                "[Shot 2] At 0:03.008, the camera cuts.\n"
                "[Shot 3] no timestamp here.")
        marks = ref2va.shot_marks(text)
        self.assertEqual([m.number for m in marks], [1, 2, 3])
        self.assertIsNone(marks[0].ts)
        self.assertIsNotNone(marks[1].ts)
        self.assertAlmostEqual(marks[1].ts.sec, 3.008)
        self.assertIsNone(marks[2].ts)


class TestRefTags(unittest.TestCase):
    def test_tags(self):
        tags = ref2va.ref_tags("<Picture 1> and <Picture 3>, <Audio 1>, <Video 2>")
        self.assertEqual(tags["Picture"], {1, 3})
        self.assertEqual(tags["Audio"], {1})
        self.assertEqual(tags["Video"], {2})


if __name__ == "__main__":
    unittest.main()
