# -*- coding: utf-8 -*-
import unittest

import _path  # noqa: F401

from h3_prompt_toolkit.grid import (FPS, grid_frames, grid_seconds,
                                    snap_up, grid_candidates,
                                    template_frames, comfy_float_hint)


class TestGrid(unittest.TestCase):
    def test_frames(self):
        self.assertEqual(grid_frames(0), 5)
        self.assertEqual(grid_frames(1), 22)
        self.assertEqual(grid_frames(8), 141)

    def test_seconds(self):
        self.assertAlmostEqual(grid_seconds(8), 141 / 24)
        self.assertEqual(FPS, 24)

    def test_snap_up_exact(self):
        # ちょうどグリッド上の値は同じ k に収まる (切り上げない)
        k, frames, sec = snap_up(141 / 24)
        self.assertEqual((k, frames), (8, 141))
        self.assertAlmostEqual(sec, 5.875)

    def test_snap_up_above(self):
        k, frames, sec = snap_up(5.876)
        self.assertEqual((k, frames), (9, 158))

    def test_snap_up_zero(self):
        k, frames, sec = snap_up(0.0)
        self.assertEqual((k, frames), (0, 5))

    def test_candidates(self):
        cands, k0 = grid_candidates(5.5)
        self.assertEqual(k0, 8)
        ks = [k for k, _, _ in cands]
        self.assertEqual(ks, [7, 8, 9, 10])


class TestComfyDuration(unittest.TestCase):
    """公式テンプレートの Math Expression 丸めと、Float 1 桁制限の安全値。"""

    def test_template_rounds_up_to_grid(self):
        self.assertEqual(template_frames(15.0), 362)
        self.assertEqual(template_frames(15.792), 379)
        self.assertEqual(template_frames(0.0), 5)

    def test_exact_seconds_land_on_same_frames(self):
        for k in range(0, 41):
            frames = grid_frames(k)
            self.assertEqual(template_frames(frames / FPS), frames)

    def test_one_decimal_hint_lands_on_same_frames(self):
        for k in range(0, 41):
            frames = grid_frames(k)
            hint = comfy_float_hint(frames)
            self.assertEqual(hint, round(hint, 1))   # 小数第 1 位までの値である
            self.assertLessEqual(hint, frames / FPS) # 真値より大きくならない (切り捨て)
            self.assertEqual(template_frames(hint), frames,
                             f"k={k} frames={frames} hint={hint}")

    def test_why_floor_not_round(self):
        # 90f = 3.750 秒。四捨五入の 3.8 は次の窓 (107f) に飛び越えるが、
        # 切り捨ての 3.7 は同じ 90f に丸め上がる。
        self.assertEqual(comfy_float_hint(90), 3.7)
        self.assertEqual(template_frames(3.7), 90)
        self.assertEqual(template_frames(3.8), 107)

    def test_known_values(self):
        self.assertEqual(comfy_float_hint(141), 5.8)
        self.assertEqual(template_frames(5.8), 141)
        self.assertEqual(comfy_float_hint(379), 15.7)
        self.assertEqual(template_frames(15.7), 379)
        self.assertEqual(template_frames(15.8), 379)  # 379 は 15.8 でも収まる


if __name__ == "__main__":
    unittest.main()
