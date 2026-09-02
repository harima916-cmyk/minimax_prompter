# -*- coding: utf-8 -*-
"""[C] 差し替えパスのテスト。fixtures/ の LLM 出力を回帰テストとして使う。"""

import unittest

import _path

from h3_prompt_toolkit.timeline import Timeline
from h3_prompt_toolkit.substitute import substitute


def demo_tl():
    return Timeline.from_json(_path.fixture("timeline_demo.json"))


class TestGoodOutput(unittest.TestCase):
    def test_already_correct_is_unchanged(self):
        text = _path.fixture("ref2va_good.txt")
        res = substitute(text, demo_tl())
        self.assertTrue(res.ok)
        self.assertFalse(res.needs_mapping)
        self.assertEqual(res.text, text)


class TestDriftedOutput(unittest.TestCase):
    def test_repaired_to_canonical(self):
        drifted = _path.fixture("ref2va_drifted.txt")
        good = _path.fixture("ref2va_good.txt")
        res = substitute(drifted, demo_tl())
        self.assertTrue(res.ok)
        # 時刻 2 箇所 + ショット時刻 + 台詞 2 箇所 + soundscape を直すと
        # 正解 (good) と完全一致する
        self.assertEqual(res.text, good)

    def test_report_mentions_replacements(self):
        res = substitute(_path.fixture("ref2va_drifted.txt"), demo_tl())
        rep = res.report_text()
        self.assertIn("0:00.500 → 00:00.512", rep)
        self.assertIn("スナップ", rep)
        self.assertIn("overall_soundscape", rep)

    def test_keep_soundscape_option(self):
        res = substitute(_path.fixture("ref2va_drifted.txt"), demo_tl(),
                         force_na=False)
        self.assertTrue(res.ok)
        self.assertIn("birdsong", res.text)

    def test_no_snap_option(self):
        res = substitute(_path.fixture("ref2va_drifted.txt"), demo_tl(),
                         snap_shots=False)
        self.assertTrue(res.ok)
        self.assertIn("[Shot 2] At 00:03.000,", res.text)   # 値はスナップされない


class TestCountMismatch(unittest.TestCase):
    def test_extra_ts_needs_mapping(self):
        text = _path.fixture("ref2va_extra_ts.txt")
        res = substitute(text, demo_tl())
        self.assertFalse(res.ok)
        self.assertTrue(res.needs_mapping)
        # 無言で辻褄を合わせない: 原文のまま返す
        self.assertEqual(res.text, text)
        self.assertEqual(len(res.at_occs), 3)
        self.assertEqual(len(res.d_occs), 2)
        # 差分が提示されている
        self.assertIn("実測タイムライン", "\n".join(res.problems))

    def test_explicit_ts_map_resolves(self):
        text = _path.fixture("ref2va_extra_ts.txt")
        res = substitute(text, demo_tl(), ts_map=[1, 3])
        self.assertTrue(res.ok)
        self.assertIn("At 00:00.512, she smiles.", res.text)
        self.assertIn("At 00:03.008, a man enters the frame.", res.text)
        # 対応させなかった出現は値そのまま (書式だけ揃う)
        self.assertIn("At 00:02.100, she pauses", res.text)

    def test_skip_with_zero(self):
        text = _path.fixture("ref2va_extra_ts.txt")
        res = substitute(text, demo_tl(), ts_map=[1, 0])
        self.assertTrue(res.ok)
        self.assertIn("At 00:03.000, a man enters", res.text)  # 値は未置換

    def test_bad_map_rejected(self):
        text = _path.fixture("ref2va_extra_ts.txt")
        res = substitute(text, demo_tl(), ts_map=[1, 9])
        self.assertFalse(res.ok)
        res = substitute(text, demo_tl(), ts_map=[1, 1])
        self.assertFalse(res.ok)
        res = substitute(text, demo_tl(), ts_map=[1])
        self.assertFalse(res.ok)


SINGLE_SHOT = """subject_definitions:
<Subject 1> is the woman in <Picture 1>, long dark hair, blue cardigan.
<Audio 1> is the voice-timbre reference for <Subject 1> (S1), and is the target video's complete final audio track.

summary:
[reference generation + audio reuse] <Subject 1> speaks to camera in a single unbroken medium close-up, lip-synced to <Audio 1>.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - identity and outfit.
<Audio 1>: fully_copy - reused 1:1 as the final audio track.

detailed_description:
The target video is live-action with soft window light.
[Shot 1] A medium close-up frames <Subject 1> facing the camera. She looks up and <Subject 1> (S1) says, <d>[Japanese] こんにちは。</d> Her lip movements are perfectly synchronized with her words. A man steps in beside her and (S2) replies, <d>[Japanese] ええ、そうですね</d> while she nods slowly.

overall_soundscape:
N/A

non_diegetic_music:
N/A
"""


class TestTimestampFormatNormalisation(unittest.TestCase):
    """P1: 置換しない時刻も書式だけ MM:SS.mmm に揃える (値は変えない)。"""

    def test_bare_timestamp_is_reformatted_not_revalued(self):
        res = substitute(_path.fixture("ref2va_drifted.txt"), demo_tl())
        self.assertTrue(res.ok)
        self.assertIn("until 00:05.875", res.text)
        self.assertNotIn("until 0:05.875", res.text)
        self.assertIn("書式", res.report_text())

    def test_short_milliseconds_are_padded(self):
        text = _path.fixture("ref2va_good.txt").replace(
            "until 00:05.875", "until 0:05.8")
        res = substitute(text, demo_tl())
        self.assertTrue(res.ok)
        self.assertIn("until 00:05.800", res.text)


class TestSingleShotStyle(unittest.TestCase):
    """仕様書 §8 の想定形 (単一ショット・At 時刻なし) を正常系として扱う。"""

    def test_no_at_timestamps_is_not_a_mismatch(self):
        res = substitute(SINGLE_SHOT, demo_tl())
        self.assertTrue(res.ok, res.report_text())
        self.assertFalse(res.needs_mapping)
        self.assertIn("単一ショット構成", res.report_text())
        # 台詞は逐語に復元される
        self.assertIn("<d>[Japanese] こんにちは、今日はいい天気ですね。</d>", res.text)
        self.assertIn("<d>[Japanese] そうですね、散歩に行きましょう。</d>", res.text)


class TestStructuralIssues(unittest.TestCase):
    def test_missing_detailed_description(self):
        res = substitute(_path.fixture("ref2va_missing_fields.txt"), demo_tl())
        self.assertFalse(res.ok)
        self.assertIn("detailed_description", "\n".join(res.problems))

    def test_missing_na_fields_appended(self):
        good = _path.fixture("ref2va_good.txt")
        cut = good.split("\noverall_soundscape:")[0]
        res = substitute(cut, demo_tl())
        self.assertTrue(res.ok)
        self.assertIn("overall_soundscape: N/A", res.text)
        self.assertIn("non_diegetic_music: N/A", res.text)

    def test_shot1_timestamp_reported_not_touched(self):
        good = _path.fixture("ref2va_good.txt")
        text = good.replace("[Shot 1] Live-action",
                            "[Shot 1] At 0:00.000, live-action")
        res = substitute(text, demo_tl())
        self.assertTrue(res.ok)
        self.assertIn("[Shot 1] At 00:00.000,", res.text)   # 値は触らない
        self.assertIn("Shot 1", res.report_text())

    def test_speaker_mismatch_reported_not_fixed(self):
        good = _path.fixture("ref2va_good.txt")
        text = good.replace("S2 says,", "S1 says,")
        res = substitute(text, demo_tl())
        self.assertTrue(res.ok)
        self.assertIn("話者 ID が不一致", res.report_text())
        # <d> の外は触らない
        self.assertIn("the man walks beside her. S1 says,", res.text)


if __name__ == "__main__":
    unittest.main()
