# -*- coding: utf-8 -*-
"""区間検出の回帰テスト。合成波形 (トーン + 無音) で行う。実 wav は使わない。"""

import unittest

import _path  # noqa: F401

import numpy as np

from h3_prompt_toolkit.segments import detect_segments

SR = 16000


def synth(total_sec, spans, freq=440.0, amp=0.5):
    n = int(total_sec * SR)
    out = np.zeros(n, dtype=np.float32)
    t = np.arange(n) / SR
    for a, b in spans:
        ia, ib = int(a * SR), int(b * SR)
        out[ia:ib] = (amp * np.sin(2 * np.pi * freq * t[ia:ib])).astype(np.float32)
    return out


class TestDetectSegments(unittest.TestCase):
    def test_two_segments(self):
        sig = synth(3.2, [(0.5, 1.0), (2.0, 2.6)])
        segs = detect_segments(sig, SR)
        self.assertEqual(len(segs), 2)
        (s1, e1), (s2, e2) = segs
        self.assertAlmostEqual(s1, 0.5, delta=0.08)
        self.assertAlmostEqual(e1, 1.0, delta=0.08)
        self.assertAlmostEqual(s2, 2.0, delta=0.08)
        self.assertAlmostEqual(e2, 2.6, delta=0.08)

    def test_short_gap_merged(self):
        # 100ms の隙間は min_silence_ms=250 で埋まる
        sig = synth(2.0, [(0.5, 1.0), (1.1, 1.5)])
        segs = detect_segments(sig, SR)
        self.assertEqual(len(segs), 1)
        s, e = segs[0]
        self.assertAlmostEqual(s, 0.5, delta=0.08)
        self.assertAlmostEqual(e, 1.5, delta=0.08)

    def test_short_gap_kept_when_configured(self):
        sig = synth(2.0, [(0.5, 1.0), (1.1, 1.5)])
        segs = detect_segments(sig, SR, min_silence_ms=50)
        self.assertEqual(len(segs), 2)

    def test_silence_only_degenerate(self):
        # 完全なデジタル無音は相対しきい値の縮退ケース: 全フレームがピークと
        # 同レベルになり、全体が 1 区間として返る (移植元の実挙動をそのまま
        # 固定する。実際の TTS 出力でゼロ埋め波形は来ない)。
        sig = np.zeros(SR, dtype=np.float32)
        segs = detect_segments(sig, SR)
        self.assertEqual(len(segs), 1)

    def test_too_short_input(self):
        self.assertEqual(detect_segments(np.zeros(10, dtype=np.float32), SR), [])

    def test_min_speech_filters_blips(self):
        # 50ms のトーンは min_speech_ms=120 で捨てられる
        sig = synth(1.0, [(0.5, 0.55)])
        self.assertEqual(detect_segments(sig, SR), [])

    def test_segments_do_not_overlap(self):
        sig = synth(3.0, [(0.3, 0.9), (1.2, 1.8), (2.1, 2.7)])
        segs = detect_segments(sig, SR, min_silence_ms=100)
        for (a1, b1), (a2, b2) in zip(segs, segs[1:]):
            self.assertLessEqual(b1, a2)


if __name__ == "__main__":
    unittest.main()
