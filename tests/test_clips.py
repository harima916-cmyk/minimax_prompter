# -*- coding: utf-8 -*-
"""手動クリップ方式の支援ロジック (clips.py) のテスト。GUI なしで検証する。"""

import io
import unittest
import wave

import _path  # noqa: F401

import numpy as np

from h3_prompt_toolkit import clips
from h3_prompt_toolkit.timeline import Utterance, parse_lines


class TestEnvelope(unittest.TestCase):
    def test_shape_and_bounds(self):
        sig = np.sin(np.linspace(0, 40 * np.pi, 4000)).astype(np.float32)
        mins, maxs = clips.envelope(sig, 100)
        self.assertEqual(len(mins), 100)
        self.assertEqual(len(maxs), 100)
        self.assertTrue(np.all(mins <= maxs))
        self.assertAlmostEqual(float(maxs.max()), 1.0, places=2)
        self.assertAlmostEqual(float(mins.min()), -1.0, places=2)

    def test_more_columns_than_samples(self):
        sig = np.array([0.5, -0.5], dtype=np.float32)
        mins, maxs = clips.envelope(sig, 10)
        self.assertEqual(len(mins), 10)
        self.assertTrue(np.all(mins <= maxs))

    def test_empty(self):
        mins, maxs = clips.envelope(np.zeros(0, dtype=np.float32), 16)
        self.assertEqual(len(mins), 16)
        self.assertTrue(np.all(mins == 0))

    def test_localized_burst(self):
        sig = np.zeros(1000, dtype=np.float32)
        sig[500:520] = 0.9
        mins, maxs = clips.envelope(sig, 10)
        self.assertAlmostEqual(float(maxs[5]), 0.9, places=5)
        self.assertEqual(float(maxs[0]), 0.0)


class TestSliceRange(unittest.TestCase):
    def test_basic(self):
        sig = np.arange(100, dtype=np.float32)
        out = clips.slice_range(sig, 10, 2.0, 5.0)
        np.testing.assert_array_equal(out, sig[20:50])

    def test_clamped(self):
        sig = np.arange(10, dtype=np.float32)
        out = clips.slice_range(sig, 10, -1.0, 99.0)
        self.assertEqual(len(out), 10)

    def test_inverted_is_empty(self):
        sig = np.arange(10, dtype=np.float32)
        self.assertEqual(len(clips.slice_range(sig, 10, 0.8, 0.2)), 0)


class TestRenumber(unittest.TestCase):
    def test_sorts_by_start_in_place(self):
        a = Utterance(1, 3.0, 4.0, "S1", "b", "Japanese")
        b = Utterance(2, 0.5, 1.0, "S2", "a", "Japanese")
        c = Utterance(3, None, None, "S1", "範囲なし", "Japanese")
        utts = [a, b, c]
        out = clips.renumber(utts)
        self.assertIs(out, utts)                 # 同じリストを並べ替える
        self.assertEqual([u.text for u in utts], ["a", "b", "範囲なし"])
        self.assertEqual([u.index for u in utts], [1, 2, 3])
        self.assertIs(utts[0], b)                # オブジェクトは保たれる

    def test_none_start_goes_last_stable(self):
        xs = [Utterance(9, None, None, "S1", "x", "Japanese"),
              Utterance(9, None, None, "S1", "y", "Japanese"),
              Utterance(9, 1.0, 2.0, "S1", "z", "Japanese")]
        clips.renumber(xs)
        self.assertEqual([u.text for u in xs], ["z", "x", "y"])


class TestFromSegmentsAndDistribute(unittest.TestCase):
    def test_from_segments(self):
        utts = clips.from_segments([(0.5, 1.0), (2.0, 2.6)], "Japanese")
        self.assertEqual(len(utts), 2)
        self.assertEqual(utts[0].index, 1)
        self.assertEqual(utts[1].start, 2.0)
        self.assertEqual(utts[0].text, "")
        self.assertEqual(utts[0].speaker, "S1")

    def test_distribute_lines(self):
        utts = clips.from_segments([(0.5, 1.0), (2.0, 2.6), (3.0, 3.5)], "Japanese")
        lines = parse_lines("こんにちは\nS2: そうですね\n")
        n = clips.distribute_lines(utts, lines)
        self.assertEqual(n, 2)
        self.assertEqual(utts[0].text, "こんにちは")
        self.assertEqual(utts[1].speaker, "S2")
        self.assertEqual(utts[2].text, "")        # 余った行はそのまま

    def test_distribute_more_lines_than_rows(self):
        utts = clips.from_segments([(0.5, 1.0)], "Japanese")
        n = clips.distribute_lines(utts, parse_lines("a\nb\n"))
        self.assertEqual(n, 1)
        self.assertEqual(utts[0].text, "a")


class TestWavBytes(unittest.TestCase):
    def test_roundtrip(self):
        sig = (0.5 * np.sin(np.linspace(0, 20 * np.pi, 2400))).astype(np.float32)
        blob = clips.wav_bytes_pcm16(sig, 24000)
        with wave.open(io.BytesIO(blob), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 24000)
            self.assertEqual(wf.getnframes(), 2400)
            raw = wf.readframes(2400)
        back = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
        np.testing.assert_allclose(back, sig, atol=1e-3)


class TestPlayer(unittest.TestCase):
    def test_backend_reported(self):
        p = clips.Player()
        # 再生できるかは環境次第だが、問い合わせが例外を出さないこと
        self.assertIsInstance(p.available(), bool)
        self.assertIsInstance(p.backend(), str)
        p.stop()   # 何も再生していなくても安全
        p.close()


if __name__ == "__main__":
    unittest.main()
