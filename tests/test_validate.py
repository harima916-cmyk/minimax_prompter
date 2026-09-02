# -*- coding: utf-8 -*-
"""[D] 検証パスのテスト。仕様書の検証表の各項目が確実に発火することを見る。"""

import unittest

import _path

from h3_prompt_toolkit.timeline import Timeline
from h3_prompt_toolkit.validate import validate, summarize, counts, render_report


def demo_tl():
    return Timeline.from_json(_path.fixture("timeline_demo.json"))


def levels(findings, check):
    return [x.level for x in findings if x.check == check]


class TestCleanOutput(unittest.TestCase):
    def test_good_has_no_errors(self):
        findings = validate(_path.fixture("ref2va_good.txt"), demo_tl())
        self.assertEqual(counts(findings)[0], 0, render_report(findings))
        s = summarize(findings)
        for check in ("fields", "ts_format", "dialogue", "monotonic",
                      "duration", "speakers", "soundscape", "ts_match"):
            self.assertEqual(s[check], "ok", f"{check}: {render_report(findings)}")


class TestFields(unittest.TestCase):
    def test_missing_fields(self):
        findings = validate(_path.fixture("ref2va_missing_fields.txt"), demo_tl())
        self.assertIn("error", levels(findings, "fields"))
        msg = "\n".join(x.message for x in findings if x.check == "fields")
        self.assertIn("subject_definitions", msg)
        self.assertIn("detailed_description", msg)

    def test_empty_text(self):
        findings = validate("", demo_tl())
        self.assertEqual(findings[0].level, "error")


class TestTsFormat(unittest.TestCase):
    def test_legacy_single_digit_minute_still_accepted(self):
        # P1 で出力は MM:SS.mmm に統一したが、読み取りは M:SS.mmm も受ける
        text = _path.fixture("ref2va_drifted.txt")
        self.assertIn("At 0:00.500", text)
        findings = validate(text, demo_tl())
        self.assertEqual(levels(findings, "ts_format"), [])

    def test_missing_milliseconds(self):
        text = _path.fixture("ref2va_good.txt").replace("At 00:00.512,", "At 00:00.5,")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "ts_format"))

    def test_unbalanced_dialogue_tags(self):
        text = _path.fixture("ref2va_good.txt").replace("</d>. She closes", ". She closes", 1)
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "ts_format"))


class TestDialogueIdentity(unittest.TestCase):
    def test_punctuation_drift_is_warn(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "こんにちは、今日はいい天気ですね。", "こんにちは。今日はいい天気ですね")
        findings = validate(text, demo_tl())
        self.assertEqual(levels(findings, "dialogue"), ["warn"])

    def test_rewrite_is_error(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "そうですね、散歩に行きましょう。", "ええ、散歩へ行きましょう。")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "dialogue"))

    def test_count_mismatch(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "S2 says, <d>[Japanese] そうですね、散歩に行きましょう。</d>. ", "")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "dialogue"))

    def test_missing_lang_tag(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "<d>[Japanese] そうですね、散歩に行きましょう。</d>",
            "<d>そうですね、散歩に行きましょう。</d>")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "dialogue"))

    def test_wrong_lang_tag(self):
        text = _path.fixture("ref2va_good.txt").replace("[Japanese] そうですね",
                                                        "[English] そうですね")
        findings = validate(text, demo_tl())
        self.assertIn("warn", levels(findings, "dialogue"))


class TestMonotonicAndDuration(unittest.TestCase):
    def test_decreasing_timestamp(self):
        text = _path.fixture("ref2va_good.txt").replace("At 00:03.008, the man",
                                                        "At 00:01.000, the man")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "monotonic"))

    def test_beyond_duration(self):
        text = _path.fixture("ref2va_good.txt").replace("until 00:05.875",
                                                        "until 00:06.500")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "duration"))


class TestSpeakers(unittest.TestCase):
    def test_swapped_speaker(self):
        text = _path.fixture("ref2va_good.txt").replace("S2 says,", "S1 says,")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "speakers"))

    def test_unknown_extra_speaker(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "Both walk slowly along the path.",
            "Both walk slowly along the path. S3 hums quietly.")
        findings = validate(text, demo_tl())
        self.assertIn("warn", levels(findings, "speakers"))


class TestRefTags(unittest.TestCase):
    def test_picture_beyond_connected(self):
        text = _path.fixture("ref2va_good.txt").replace("<Picture 2>", "<Picture 3>")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "ref_tags"))
        self.assertIn("warn", levels(findings, "ref_tags"))  # Picture 2 未引用

    def test_video_not_connected(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "reused as-is.", "reused as-is with <Video 1> motion.")
        findings = validate(text, demo_tl())
        self.assertIn("warn", levels(findings, "ref_tags"))

    def test_without_timeline_uses_capacity(self):
        text = _path.fixture("ref2va_good.txt").replace("<Picture 2>", "<Picture 12>")
        findings = validate(text, None)
        self.assertIn("warn", levels(findings, "ref_tags"))


class TestShots(unittest.TestCase):
    def test_shot1_stamped(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "[Shot 1] Live-action", "[Shot 1] At 00:00.000, live-action")
        findings = validate(text, demo_tl())
        self.assertIn("warn", levels(findings, "shots"))

    def test_shot_number_jump(self):
        text = _path.fixture("ref2va_good.txt").replace("[Shot 2]", "[Shot 3]")
        findings = validate(text, demo_tl())
        self.assertIn("warn", levels(findings, "shots"))

    def test_later_shot_unstamped(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "[Shot 2] At 00:03.008, the camera cuts", "[Shot 2] The camera cuts")
        findings = validate(text, demo_tl())
        self.assertIn("warn", levels(findings, "shots"))


class TestSoundscape(unittest.TestCase):
    def test_filled_soundscape_is_error(self):
        findings = validate(_path.fixture("ref2va_drifted.txt"), demo_tl())
        self.assertIn("error", levels(findings, "soundscape"))

    def test_relaxed_mode(self):
        findings = validate(_path.fixture("ref2va_drifted.txt"), demo_tl(),
                            expect_na=False)
        self.assertEqual(levels(findings, "soundscape"), ["info"])


class TestTaskTypeAndAudioMarker(unittest.TestCase):
    """A4: 強制音声モードの FIXED (audio reuse / fully_copy)。"""

    def test_missing_audio_reuse(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "[reference generation + audio reuse]", "[reference generation]")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "task_type"))

    def test_missing_bracketed_task_type(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "[reference generation + audio reuse] The target", "The target")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "task_type"))

    def test_wrong_audio_marker(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "<Audio 1>: fully_copy", "<Audio 1>: fully_preserved")
        findings = validate(text, demo_tl())
        self.assertIn("error", levels(findings, "audio_marker"))

    def test_missing_audio_line(self):
        good = _path.fixture("ref2va_good.txt")
        line = [l for l in good.splitlines() if l.startswith("<Audio 1>: ")][0]
        findings = validate(good.replace(line + "\n", ""), demo_tl())
        self.assertIn("error", levels(findings, "audio_marker"))

    def test_good_passes(self):
        findings = validate(_path.fixture("ref2va_good.txt"), demo_tl())
        self.assertEqual(levels(findings, "task_type"), [])
        self.assertEqual(levels(findings, "audio_marker"), [])


def anchor_tl(on=True):
    tl = demo_tl()
    tl.ref_texts = dict(tl.ref_texts or {})
    tl.ref_texts["anchor"] = on
    return tl


class TestAnchorConsistency(unittest.TestCase):
    """P2: First-frame anchor (AddGuide) の ON/OFF とプロンプトの整合。"""

    def setUp(self):
        self.anchor_text = _path.fixture("ref2va_anchor_on.txt")

    def test_anchor_on_fixture_is_clean(self):
        findings = validate(self.anchor_text, anchor_tl(True))
        self.assertEqual(levels(findings, "anchor"), [], render_report(findings))
        self.assertEqual(counts(findings)[0], 0, render_report(findings))

    def test_anchor_on_requires_keyframe_completion(self):
        text = self.anchor_text.replace(
            "[keyframe completion + reference generation + audio reuse]",
            "[reference generation + audio reuse]")
        findings = validate(text, anchor_tl(True))
        self.assertIn("error", levels(findings, "anchor"))

    def test_anchor_on_requires_picture1_fully_preserved(self):
        text = self.anchor_text.replace(
            "<Picture 1> (appears in [Shot 1]): fully_preserved -",
            "<Picture 1> (appears in [Shot 1]): weak_reference -")
        findings = validate(text, anchor_tl(True))
        self.assertIn("error", levels(findings, "anchor"))

    def test_anchor_on_missing_picture1_retention_line(self):
        line = [l for l in self.anchor_text.splitlines()
                if l.startswith("<Picture 1> (appears")][0]
        findings = validate(self.anchor_text.replace(line + "\n", ""),
                            anchor_tl(True))
        self.assertIn("error", levels(findings, "anchor"))

    def test_anchor_on_wants_begins_from_phrase(self):
        text = self.anchor_text.replace(
            "[Shot 1] The target video begins from <Picture 1>:",
            "[Shot 1] A medium close-up shows the woman,")
        findings = validate(text, anchor_tl(True))
        self.assertIn("warn", levels(findings, "anchor"))

    def test_anchor_off_flags_keyframe_completion(self):
        findings = validate(self.anchor_text, anchor_tl(False))
        self.assertIn("warn", levels(findings, "anchor"))

    def test_anchor_off_normal_output_is_clean(self):
        findings = validate(_path.fixture("ref2va_good.txt"), anchor_tl(False))
        self.assertEqual(levels(findings, "anchor"), [])

    def test_no_timeline_skips_check(self):
        findings = validate(self.anchor_text, None)
        self.assertEqual(levels(findings, "anchor"), [])


class TestResolutionIndependence(unittest.TestCase):
    """A4: 精修パスで使い回せるよう、解像度・工程の語を弾く。"""

    def test_resolution_words(self):
        for word in ("768p", "1344x768", "0.4 MP", "low-res", "upscaled",
                     "draft", "refinement pass"):
            text = _path.fixture("ref2va_good.txt").replace(
                "Live-action style.", f"Live-action style, {word}.")
            findings = validate(text, demo_tl())
            self.assertIn("warn", levels(findings, "resolution"),
                          f"{word} が検出されない")

    def test_good_passes(self):
        findings = validate(_path.fixture("ref2va_good.txt"), demo_tl())
        self.assertEqual(levels(findings, "resolution"), [])


class TestTsMatch(unittest.TestCase):
    def test_drifted_timestamps_warn(self):
        findings = validate(_path.fixture("ref2va_drifted.txt"), demo_tl())
        warns = [x.message for x in findings if x.check == "ts_match"]
        self.assertEqual(len(warns), 2)
        self.assertIn("0:00.512", warns[0])


if __name__ == "__main__":
    unittest.main()
