# -*- coding: utf-8 -*-
"""[B] 固定枠・骨組みレンダラのテスト (日本語版 / 英語版 / 参照ヘッダ)。"""

import unittest

import _path

from h3_prompt_toolkit import ref2va
from h3_prompt_toolkit.timeline import Timeline, fmt_ts
from h3_prompt_toolkit.scaffold import (
    render_scaffold, render_prompt_skeleton,
    render_scaffold_en, render_prompt_skeleton_en,
    render_scaffold_for_llm, render_skeleton_for_llm,
    render_reference_header, render_brief,
    DEFAULT_PIC1_DESC, DEFAULT_AUDIO_DESC)


def demo_tl():
    return Timeline.from_json(_path.fixture("timeline_demo.json"))


class TestEnglishScaffold(unittest.TestCase):
    def setUp(self):
        self.tl = demo_tl()
        self.sc = render_scaffold_en(self.tl.utterances, self.tl.total_sec,
                                     self.tl.frames, "voice.wav",
                                     self.tl.n_images, "")

    def test_markers(self):
        self.assertIn("=== FIXED FACTS / DO NOT CHANGE ===", self.sc)
        self.assertIn("141 frames @ 24fps", self.sc)
        self.assertIn("<Picture 1>..<Picture 2>", self.sc)
        self.assertIn("=== INSTRUCTIONS ===", self.sc)
        self.assertIn("Never alter the contents of any <d> tag", self.sc)

    def test_timeline_lines_keep_japanese_dialogue(self):
        for u in self.tl.usable_utterances():
            self.assertIn(fmt_ts(u.start), self.sc)
            self.assertIn(f"<d>[{u.lang}] {u.text}</d>", self.sc)

    def test_mode_note_passthrough(self):
        sc = render_scaffold_en(self.tl.utterances, self.tl.total_sec,
                                self.tl.frames, "voice.wav", 2, "確認すること")
        self.assertIn("- NOTE: 確認すること", sc)


class TestEnglishSkeleton(unittest.TestCase):
    def setUp(self):
        self.tl = demo_tl()
        self.pr = render_prompt_skeleton_en(self.tl.utterances,
                                            self.tl.total_sec, self.tl.n_images)

    def test_all_six_fields_parse(self):
        bodies = ref2va.section_bodies(self.pr)
        for name in ref2va.REF_OUTPUT_FIELDS:
            self.assertTrue(bodies[name], f"{name} が空")

    def test_one_at_line_per_utterance(self):
        occs = ref2va.ts_occurrences(self.pr)
        at = [o for o in occs if o.kind == "at"]
        self.assertEqual(len(at), len(self.tl.usable_utterances()))
        d = ref2va.dialogue_occurrences(self.pr)
        self.assertEqual(len(d), len(self.tl.usable_utterances()))

    def test_na_fields(self):
        bodies = ref2va.section_bodies(self.pr)
        self.assertEqual(bodies["overall_soundscape"], "N/A")
        self.assertEqual(bodies["non_diegetic_music"], "N/A")


class TestDispatch(unittest.TestCase):
    def test_default_is_english(self):
        tl = demo_tl()
        sc = render_scaffold_for_llm(tl.utterances, tl.total_sec, tl.frames,
                                     "voice.wav", 2, "")
        self.assertIn("FIXED FACTS", sc)

    def test_japanese_kept_available(self):
        tl = demo_tl()
        sc = render_scaffold_for_llm(tl.utterances, tl.total_sec, tl.frames,
                                     "voice.wav", 2, "", out_lang="ja")
        self.assertIn("=== 固定情報 / 変更禁止 ===", sc)
        self.assertEqual(sc, render_scaffold(tl.utterances, tl.total_sec,
                                             tl.frames, "voice.wav", 2, ""))
        pr = render_skeleton_for_llm(tl.utterances, tl.total_sec, 2,
                                     out_lang="ja")
        self.assertEqual(pr, render_prompt_skeleton(tl.utterances,
                                                    tl.total_sec, 2))


class TestBrief(unittest.TestCase):
    """仕様書 (minimax_ref2v_rule.txt) に追記するブリーフ。"""

    def setUp(self):
        self.tl = demo_tl()
        self.brief = render_brief(self.tl.utterances, self.tl.total_sec,
                                  self.tl.frames, "voice_141f.wav",
                                  ["the singer", "the stage"], "her voice")

    def test_declares_forced_audio_mode(self):
        self.assertIn("forced-audio / TTS-driven workflow", self.brief)
        self.assertIn("frozen", self.brief)

    def test_duration_facts(self):
        self.assertIn("5.875 s = 141 frames @ 24 fps (17k+5 grid)", self.brief)
        self.assertIn("voice_141f.wav", self.brief)
        # 無音尾部 = 5.875 - 5.021
        self.assertIn("Silent tail after the last utterance: 0.854 s.", self.brief)

    def test_verbatim_transcript_lines(self):
        self.assertIn("[1] 0:00.512 - 0:02.104 (S1) "
                      "<d>[Japanese] こんにちは、今日はいい天気ですね。</d>", self.brief)
        self.assertIn("[2] 0:03.008 - 0:05.021 (S2) "
                      "<d>[Japanese] そうですね、散歩に行きましょう。</d>", self.brief)

    def test_references_included(self):
        self.assertIn("- <Picture 1> is the singer.", self.brief)
        self.assertIn("- <Audio 1> is her voice.", self.brief)

    def test_empty_timeline_placeholder(self):
        brief = render_brief([], 5.875, 141, "voice.wav")
        self.assertIn("(no utterances entered yet)", brief)

    def test_scenario_included_verbatim(self):
        brief = render_brief(self.tl.utterances, self.tl.total_sec,
                             self.tl.frames, "voice.wav",
                             scenario="公園で楽しく散歩する二人。\n明るい雰囲気で。")
        self.assertIn("Scenario — what the user wants", brief)
        self.assertIn("公園で楽しく散歩する二人。", brief)
        self.assertIn("明るい雰囲気で。", brief)
        # シナリオはトランスクリプトより前に来る
        self.assertLess(brief.index("Scenario"), brief.index("Verbatim transcript"))

    def test_scenario_default_latitude(self):
        # 未記入なら「創作裁量で埋めよ」の一文が入る
        self.assertIn("Scenario: not specified", self.brief)


class TestReferenceHeader(unittest.TestCase):
    def test_basic(self):
        out = render_reference_header(["the singer", "the stage"],
                                      "her voice reference")
        self.assertEqual(out.splitlines(), [
            "<Picture 1> is the singer.",
            "<Picture 2> is the stage.",
            "<Audio 1> is her voice reference.",
        ])

    def test_defaults_and_punctuation(self):
        out = render_reference_header(["", "already ends."], "")
        lines = out.splitlines()
        self.assertEqual(lines[0], f"<Picture 1> is {DEFAULT_PIC1_DESC}.")
        self.assertEqual(lines[1], "<Picture 2> is already ends.")
        self.assertEqual(lines[2], f"<Audio 1> is {DEFAULT_AUDIO_DESC}.")


if __name__ == "__main__":
    unittest.main()
