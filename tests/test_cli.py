# -*- coding: utf-8 -*-
"""CLI の配線テスト。合成 wav と fixtures だけで一巡させる。"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import _path

import numpy as np

from h3_prompt_toolkit import cli
from h3_prompt_toolkit.audio import write_wav_pcm16

SR = 16000


def make_demo_wav(path):
    """0.5-1.0s と 2.0-2.6s にトーンがある 3.2 秒の wav。"""
    n = int(3.2 * SR)
    sig = np.zeros(n, dtype=np.float32)
    t = np.arange(n) / SR
    for a, b in ((0.5, 1.0), (2.0, 2.6)):
        ia, ib = int(a * SR), int(b * SR)
        sig[ia:ib] = 0.5 * np.sin(2 * np.pi * 440 * t[ia:ib])
    write_wav_pcm16(path, sig.reshape(-1, 1), SR)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.wav = os.path.join(self.dir, "voice.wav")
        make_demo_wav(self.wav)
        self.lines = os.path.join(self.dir, "lines.txt")
        with open(self.lines, "w", encoding="utf-8") as fh:
            fh.write("こんにちは\nS2: そうですね\n")
        self.tl_json = os.path.join(_path.FIXTURES, "timeline_demo.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_measure_and_save(self):
        save = os.path.join(self.dir, "tl.json")
        code, out, err = run_cli("measure", "--wav", self.wav,
                                 "--lines", self.lines,
                                 "--save-timeline", save, "--json")
        self.assertEqual(code, 0)
        self.assertIn("[1]", out)
        data = json.loads(out[out.index("{"):])
        self.assertEqual(len(data["utterances"]), 2)
        self.assertEqual(data["utterances"][1]["speaker"], "S2")
        self.assertTrue(os.path.exists(save))
        # 3.2 秒 → 82f (17*4+5=73f は 3.042s で足りず、 k=5 → 90f=3.75s…計算で確認)
        self.assertEqual(data["frames"] % 17, 5)
        self.assertGreaterEqual(data["total_sec"], 3.2)

    def test_pad(self):
        code, out, err = run_cli("pad", "--wav", self.wav)
        self.assertEqual(code, 0)
        made = [f for f in os.listdir(self.dir) if f.endswith("f.wav")]
        self.assertEqual(len(made), 1)
        # 貼り付け用の数値がサイドカー txt に書き出される
        notes = [f for f in os.listdir(self.dir) if f.endswith("f.txt")]
        self.assertEqual(len(notes), 1)
        with open(os.path.join(self.dir, notes[0]), encoding="utf-8") as fh:
            note = fh.read()
        # 3.2 秒 → 90 フレーム = 3.750 秒。1 桁制限時の安全値は 3.7
        self.assertIn("90 フレーム", note)
        self.assertIn("3.750", note)
        self.assertIn("3.7", note)
        self.assertIn("Float (Duration)", note)
        self.assertIn("3.7", err)

    def test_scaffold_from_timeline(self):
        # 既定は英語 (LLM も H3 も英語前提)。台詞はそのまま
        code, out, err = run_cli("scaffold", "--timeline", self.tl_json)
        self.assertEqual(code, 0)
        self.assertIn("=== FIXED FACTS / DO NOT CHANGE ===", out)
        self.assertIn("subject_definitions:", out)
        self.assertIn("<d>[Japanese] こんにちは、今日はいい天気ですね。</d>", out)
        self.assertIn("運用設定", err)

    def test_scaffold_japanese_option(self):
        code, out, _ = run_cli("scaffold", "--timeline", self.tl_json,
                               "--out-lang", "ja")
        self.assertEqual(code, 0)
        self.assertIn("=== 固定情報 / 変更禁止 ===", out)

    def test_scaffold_reference_header(self):
        code, out, _ = run_cli("scaffold", "--timeline", self.tl_json,
                               "--pic-desc", "the singer",
                               "--pic-desc", "the stage",
                               "--audio-desc", "her voice")
        self.assertEqual(code, 0)
        self.assertIn("<Picture 1> is the singer.", out)
        self.assertIn("<Picture 2> is the stage.", out)
        self.assertIn("<Audio 1> is her voice.", out)
        # ヘッダは固定枠より前に出る
        self.assertLess(out.index("<Picture 1> is"), out.index("FIXED FACTS"))

    def test_substitute_ok(self):
        src = os.path.join(self.dir, "llm.txt")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(_path.fixture("ref2va_drifted.txt"))
        dst = os.path.join(self.dir, "final.txt")
        code, out, err = run_cli("substitute", "--timeline", self.tl_json,
                                 src, "-o", dst)
        self.assertEqual(code, 0)
        with open(dst, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), _path.fixture("ref2va_good.txt"))
        self.assertIn("差し替え完了", err)

    def test_substitute_needs_mapping_exit_2(self):
        src = os.path.join(self.dir, "llm.txt")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(_path.fixture("ref2va_extra_ts.txt"))
        code, out, err = run_cli("substitute", "--timeline", self.tl_json, src)
        self.assertEqual(code, 2)
        self.assertIn("ts_map", err)
        # 明示対応で解決
        code, out, err = run_cli("substitute", "--timeline", self.tl_json,
                                 src, "--ts-map", "1,3")
        self.assertEqual(code, 0)
        self.assertIn("At 00:00.512", out)

    def test_validate_exit_codes(self):
        good = os.path.join(self.dir, "good.txt")
        with open(good, "w", encoding="utf-8") as fh:
            fh.write(_path.fixture("ref2va_good.txt"))
        code, out, _ = run_cli("validate", "--timeline", self.tl_json, good)
        self.assertEqual(code, 0)
        self.assertIn("問題は見つかりませんでした", out)

        bad = os.path.join(self.dir, "bad.txt")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write(_path.fixture("ref2va_drifted.txt"))
        code, out, _ = run_cli("validate", "--timeline", self.tl_json, bad)
        self.assertEqual(code, 1)
        self.assertIn("台詞", out)

    def test_compare(self):
        g = os.path.join(self.dir, "good.txt")
        d = os.path.join(self.dir, "drifted.txt")
        for path, name in ((g, "ref2va_good.txt"), (d, "ref2va_drifted.txt")):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_path.fixture(name))
        code, out, _ = run_cli("compare", "--timeline", self.tl_json,
                               f"9b={g}", f"omni={d}", "--quiet")
        self.assertEqual(code, 0)
        self.assertIn("9b", out)
        self.assertIn("omni", out)
        self.assertIn("エラー/警告", out)

    def test_settings(self):
        code, out, _ = run_cli("settings")
        self.assertEqual(code, 0)
        self.assertIn("qwen3.8-27b", out)
        self.assertIn("temperature: 0.7", out)
        self.assertIn("reasoning_effort", out)
        self.assertIn("16384", out)


if __name__ == "__main__":
    unittest.main()
