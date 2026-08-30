# -*- coding: utf-8 -*-
import unittest

import _path  # noqa: F401

from h3_prompt_toolkit.timeline import (Timeline, Utterance, build_timeline,
                                        fmt_ts, parse_ts, parse_lines)


class TestFmtTs(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(fmt_ts(0.0), "0:00.000")
        self.assertEqual(fmt_ts(0.512), "0:00.512")
        self.assertEqual(fmt_ts(65.25), "1:05.250")
        self.assertEqual(fmt_ts(600.0), "10:00.000")

    def test_negative_clamped(self):
        self.assertEqual(fmt_ts(-3.0), "0:00.000")

    def test_roundtrip(self):
        for sec in (0.0, 0.512, 3.008, 5.875, 61.001, 599.999):
            self.assertAlmostEqual(parse_ts(fmt_ts(sec)), sec, places=3)

    def test_parse_loose(self):
        self.assertAlmostEqual(parse_ts("0:03.5"), 3.5)
        self.assertAlmostEqual(parse_ts("00:03.500"), 3.5)
        self.assertIsNone(parse_ts("3.5"))
        self.assertIsNone(parse_ts("abc"))
        self.assertIsNone(parse_ts(None))


class TestParseLines(unittest.TestCase):
    def test_default_speaker(self):
        self.assertEqual(parse_lines("こんにちは"), [("S1", "こんにちは")])

    def test_speaker_prefix(self):
        self.assertEqual(parse_lines("S2: やあ"), [("S2", "やあ")])
        self.assertEqual(parse_lines("s3： 全角コロン"), [("S3", "全角コロン")])
        self.assertEqual(parse_lines("(S2): 括弧つき"), [("S2", "括弧つき")])

    def test_blank_lines_skipped(self):
        self.assertEqual(
            parse_lines("a\n\n  \nS2: b\n"),
            [("S1", "a"), ("S2", "b")])


class TestBuildTimeline(unittest.TestCase):
    def test_mismatch_keeps_both_sides(self):
        segs = [(0.5, 1.0), (2.0, 2.5)]
        lines = [("S1", "ひとつ")]
        rows = build_timeline(segs, lines)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].text, "ひとつ")
        self.assertEqual(rows[1].text, "")
        self.assertAlmostEqual(rows[1].start, 2.0)
        self.assertTrue(rows[0].usable())
        self.assertFalse(rows[1].usable())

    def test_more_lines_than_segments(self):
        rows = build_timeline([(0.5, 1.0)], [("S1", "a"), ("S2", "b")])
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[1].start)
        self.assertEqual(rows[1].speaker, "S2")


class TestTimelineJson(unittest.TestCase):
    def test_roundtrip(self):
        tl = Timeline(
            utterances=[
                Utterance(1, 0.512, 2.104, "S1", "こんにちは、今日はいい天気ですね。", "Japanese"),
                Utterance(2, 3.008, 5.021, "S2", "そうですね、散歩に行きましょう。", "Japanese"),
            ],
            total_sec=5.875, frames=141, wav_path="voice.wav", n_images=2)
        back = Timeline.from_json(tl.to_json())
        self.assertEqual(back, tl)
        self.assertEqual(len(back.usable_utterances()), 2)

    def test_fixture_loads(self):
        tl = Timeline.from_json(_path.fixture("timeline_demo.json"))
        self.assertEqual(tl.frames, 141)
        self.assertEqual(tl.utterances[1].speaker, "S2")
        self.assertEqual(tl.ref_texts, {})   # 旧形式 JSON も読める

    def test_ref_texts_roundtrip(self):
        tl = Timeline(total_sec=1.0, frames=22,
                      ref_texts={"pictures": ["a", "b"], "audio": "c"})
        back = Timeline.from_json(tl.to_json())
        self.assertEqual(back.ref_texts, {"pictures": ["a", "b"], "audio": "c"})


if __name__ == "__main__":
    unittest.main()
